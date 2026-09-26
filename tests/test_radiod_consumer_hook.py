"""`smd admin radiod restart-stale-consumers` — every system call faked.

Restarts the radiod consumers still running from before radiod's current
start (2026-09-26 plan: AC0G-ND's RX888 stalls make radiod crash; systemd
restarts it; recorders that keep running hold the old radiod's RTP->UTC
anchor and decode nothing). Run by sigmond-radiod-consumers@.service on
every radiod start (Task 2).

final review adds four binding rulings, each with its own test group below:
  I-1: stale = a consumer's MONOTONIC start < radiod's monotonic start plus
       the settle allowance sigmond-radiod-ready itself waits out.
  I-2: radiod restarting again WHILE this run restarts its consumers gets a
       fresh pass, up to 3 total.
  I-3: (bin/sigmond-sdr-recover, tests/test_sdr_recover.py — not here.)
  I-4: a catalog that fails to load restarts nothing (never "everything
       running is a consumer"); every consumer considered is printed, dry
       run included.
  M-2: _wait_lifecycle_lock narrates a wait.
"""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_radiod_consumer_hook", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class RadiodStaleConsumersTests(unittest.TestCase):
    def _st(self, started, consumers):
        return {"radiod_consumers": set(consumers), "started_at": dict(started),
                "running": {c for c, t in started.items() if t is not None}}

    def test_only_consumers_older_than_radiod_are_stale(self):
        st = self._st({"psk-recorder": 100.0, "wspr-recorder": 300.0,
                       "hf-timestd": 150.0, "stopped": None, "same-start": 200.0},
                      {"psk-recorder", "wspr-recorder", "hf-timestd", "stopped",
                       "same-start"})
        self.assertEqual(smd._radiod_stale_consumers(200.0, st),
                          ["hf-timestd", "psk-recorder"])

    def test_non_consumers_are_never_stale(self):
        st = self._st({"station-web": 100.0}, set())
        self.assertEqual(smd._radiod_stale_consumers(200.0, st), [])


def _show(active, ts):
    return f"ActiveState={active}\nActiveEnterTimestamp=@{ts}\n"


def _show_mono(active, secs):
    us = 0 if secs is None else int(secs * 1e6)
    return f"ActiveState={active}\nActiveEnterTimestampMonotonic={us}\n"


class RadiodUnitStartedAtTests(unittest.TestCase):
    """_radiod_unit_started_at delegates to _align_units_started_at (Fix
    round 1 / finding 3) — same fakes, same semantics, no duplicated
    ActiveEnterTimestamp parsing."""

    def test_active_unit_reads_its_start(self):
        run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, _show("active", 1790000000), "")
        self.assertEqual(smd._radiod_unit_started_at("radiod@x.service", run=run), 1790000000.0)

    def test_inactive_unit_reads_none(self):
        run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, _show("inactive", 1), "")
        self.assertIsNone(smd._radiod_unit_started_at("radiod@x.service", run=run))

    def test_delegates_to_align_units_started_at(self):
        with mock.patch.object(smd, "_align_units_started_at",
                              return_value=42.0) as delegate:
            self.assertEqual(
                smd._radiod_unit_started_at("radiod@x.service", run="RUN"), 42.0)
        delegate.assert_called_once_with(["radiod@x.service"], run="RUN")


class RadiodUnitStartedAtMonotonicTests(unittest.TestCase):
    """final review / I-1: the hook compares on the monotonic clock — a
    second delegating wrapper, same shape as the wall-clock one above."""

    def test_active_unit_reads_its_monotonic_start(self):
        run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, _show_mono("active", 5.0), "")
        self.assertEqual(
            smd._radiod_unit_started_at_monotonic("radiod@x.service", run=run), 5.0)

    def test_inactive_unit_reads_none(self):
        run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, _show_mono("inactive", None), "")
        self.assertIsNone(smd._radiod_unit_started_at_monotonic("radiod@x.service", run=run))

    def test_delegates_to_align_units_started_at_monotonic(self):
        with mock.patch.object(smd, "_align_units_started_at",
                              return_value=42.0) as delegate:
            self.assertEqual(
                smd._radiod_unit_started_at_monotonic("radiod@x.service", run="RUN"), 42.0)
        delegate.assert_called_once_with(["radiod@x.service"], run="RUN", monotonic=True)


class WaitLifecycleLockTests(unittest.TestCase):
    def test_retries_until_the_lock_frees(self):
        attempts = []

        @contextlib.contextmanager
        def lock(reason=None):
            attempts.append(1)
            if len(attempts) < 3:
                raise SystemExit("busy")
            yield

        t = [0.0]
        with mock.patch.object(smd, "lifecycle_lock", lock):
            with smd._wait_lifecycle_lock("x", sleep=lambda s: t.__setitem__(0, t[0] + s),
                                          clock=lambda: t[0], say=lambda m: None):
                pass
        self.assertEqual(len(attempts), 3)

    def test_gives_up_at_the_deadline(self):
        @contextlib.contextmanager
        def lock(reason=None):
            raise SystemExit("busy")
            yield

        t = [0.0]
        with mock.patch.object(smd, "lifecycle_lock", lock):
            with self.assertRaises(TimeoutError):
                with smd._wait_lifecycle_lock("x", deadline_s=20, poll_s=5,
                                              sleep=lambda s: t.__setitem__(0, t[0] + s),
                                              clock=lambda: t[0], say=lambda m: None):
                    pass

    def test_default_deadline_is_900s(self):
        """M-4: the verb's default — covers 3 passes' readiness waits plus
        the restarts themselves, not the generic 600s a bare lifecycle_lock
        wait might otherwise use."""
        attempts = []

        @contextlib.contextmanager
        def lock(reason=None):
            attempts.append(1)
            raise SystemExit("busy")
            yield

        t = [0.0]

        def sleep(s):
            t[0] += s

        with mock.patch.object(smd, "lifecycle_lock", lock):
            with self.assertRaises(TimeoutError) as ctx:
                with smd._wait_lifecycle_lock("x", poll_s=100, sleep=sleep,
                                              clock=lambda: t[0], say=lambda m: None):
                    pass
        self.assertIn("900", str(ctx.exception))


class WaitLifecycleLockMessagingTests(unittest.TestCase):
    """M-2: one line when the lock is first found busy, one when it is
    acquired after having waited — silence for up to 900s looks like a hang
    in the unit's own log."""

    def test_prints_once_busy_and_once_acquired(self):
        attempts = []

        @contextlib.contextmanager
        def lock(reason=None):
            attempts.append(1)
            if len(attempts) < 3:
                raise SystemExit("busy")
            yield

        t = [0.0]
        msgs = []
        with mock.patch.object(smd, "lifecycle_lock", lock):
            with smd._wait_lifecycle_lock(
                    "x", sleep=lambda s: t.__setitem__(0, t[0] + s),
                    clock=lambda: t[0], say=msgs.append):
                pass
        self.assertEqual(msgs, [
            "waiting for the lifecycle lock (held by another smd operation)",
            "lifecycle lock acquired",
        ])

    def test_no_message_when_the_lock_is_free_immediately(self):
        msgs = []
        with mock.patch.object(smd, "lifecycle_lock",
                              lambda reason=None: contextlib.nullcontext()):
            with smd._wait_lifecycle_lock("x", say=msgs.append):
                pass
        self.assertEqual(msgs, [])


# ─── shared harness for the end-to-end cmd tests below ──────────────────────

SERVICES = {"ka9q-radio": ["radiod@x.service"], "psk-recorder": ["psk.service"],
            "wspr-recorder": ["wspr.service"]}
RADIOD_UNIT = "radiod@x.service"


def _fake_run(*, ready_rc=0, ready_rcs=None, restart_rc=0, radiod_mono=200.0, mono=None):
    """Every system call the hook makes, faked.

    ``mono``: {unit: seconds-since-boot} answered for
    ActiveEnterTimestampMonotonic reads of consumer units — merged over
    defaults that reproduce the classic scenario (psk.service started
    before radiod: stale; wspr.service after: fresh). ``radiod_mono`` is
    the same for radiod@x.service. A unit with no mono entry reads
    inactive (not running).
    """
    calls = []
    ready_iter = iter(ready_rcs) if ready_rcs is not None else None
    mono = {"psk.service": 100.0, "wspr.service": 300.0, **(mono or {})}

    def run(argv, **kw):
        calls.append(list(argv))
        if argv[0] == "/usr/local/sbin/sigmond-radiod-ready":
            if ready_iter is not None:
                rc = next(ready_iter, ready_rcs[-1])
            else:
                rc = ready_rc
            return subprocess.CompletedProcess(argv, rc, "", "")
        if argv[:2] == ["systemctl", "show"] and "ActiveEnterTimestampMonotonic" in argv:
            unit = argv[-1]
            secs = radiod_mono if unit == RADIOD_UNIT else mono.get(unit)
            return subprocess.CompletedProcess(
                argv, 0, _show_mono("active" if secs is not None else "inactive", secs), "")
        if argv[:2] == ["systemctl", "show"] and RADIOD_UNIT in argv:
            return subprocess.CompletedProcess(argv, 0, _show("active", 200), "")
        if argv[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        if argv[:2] == ["systemctl", "restart"]:
            return subprocess.CompletedProcess(
                argv, restart_rc, "", "boom" if restart_rc else "")
        return subprocess.CompletedProcess(argv, 0, "", "")
    return run, calls


def _run_cmd(run, *, dry_run=False, lock=None, staleness=None, wait_ready=90):
    """Invoke the real cmd_radiod_restart_stale_consumers with `_align_services`,
    `_align_staleness`, `lifecycle_lock` and `load_catalog` mocked — the
    consumer-selection math and restart machinery are exercised for real."""
    args = argparse.Namespace(unit=RADIOD_UNIT, dry_run=dry_run, wait_ready=wait_ready)
    lock_fn = lock if lock is not None else (lambda reason=None: contextlib.nullcontext())
    if callable(staleness):
        staleness_patch = mock.patch.object(smd, "_align_staleness", side_effect=staleness)
    else:
        st = staleness or {"radiod_consumers": {"psk-recorder", "wspr-recorder"}}
        staleness_patch = mock.patch.object(smd, "_align_staleness", return_value=st)
    with mock.patch.object(smd, "_align_services", return_value=SERVICES), \
         mock.patch.object(smd, "load_catalog", return_value={}), \
         staleness_patch, \
         mock.patch.object(smd, "lifecycle_lock", lock_fn), \
         contextlib.redirect_stdout(io.StringIO()) as out:
        rc = smd.cmd_radiod_restart_stale_consumers(args, run=run, sleep=lambda s: None)
    return rc, out.getvalue()


class RestartStaleConsumersCmdTests(unittest.TestCase):
    """The verb end to end, with every system call faked."""

    def test_restarts_only_the_stale_consumer_after_readiness(self):
        run, calls = _fake_run()
        rc, out = _run_cmd(run)
        self.assertEqual(rc, 0)
        restarts = [c for c in calls if c[:2] == ["systemctl", "restart"]]
        self.assertEqual(restarts, [["systemctl", "restart", "psk.service"]])
        ready = next(i for i, c in enumerate(calls) if c[0] == "/usr/local/sbin/sigmond-radiod-ready")
        self.assertLess(ready, calls.index(restarts[0]))
        self.assertNotIn(["systemctl", "restart", RADIOD_UNIT], calls)

    def test_radiod_not_ready_restarts_nothing(self):
        run, calls = _fake_run(ready_rc=1)
        rc, _ = _run_cmd(run)
        self.assertEqual(rc, 2)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])

    def test_dry_run_restarts_nothing_and_names_the_stale(self):
        run, calls = _fake_run()
        rc, out = _run_cmd(run, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertIn("psk-recorder", out)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])

    def test_nothing_stale_is_a_quiet_no_op(self):
        run, calls = _fake_run(mono={"psk.service": 300.0, "wspr.service": 300.0})
        rc, out = _run_cmd(run)
        self.assertEqual(rc, 0)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])

    def test_radiod_not_ready_on_the_post_lock_recheck_restarts_nothing(self):
        """Fix round 1 / finding 1: radiod can crash-restart again while
        this run waited for the lock — the post-lock recheck must catch
        that and refuse, not trust a start time nobody confirmed ready."""
        run, calls = _fake_run(ready_rcs=[0, 1])
        rc, _ = _run_cmd(run)
        self.assertEqual(rc, 2)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])
        ready_calls = [c for c in calls if c[0] == "/usr/local/sbin/sigmond-radiod-ready"]
        self.assertEqual(len(ready_calls), 2)

    def test_staleness_is_read_only_after_the_lock_is_acquired(self):
        """Fix round 1 / finding 2a: concurrent-align safety depends on
        staleness being read AFTER the lock, never before."""
        order = []

        @contextlib.contextmanager
        def lock(reason=None):
            order.append("acquired")
            yield

        def fake_staleness(base, services, catalog=None, run=None):
            self.assertIn("acquired", order)
            return {"radiod_consumers": {"psk-recorder"}}

        run, calls = _fake_run()
        rc, _ = _run_cmd(run, lock=lock, staleness=fake_staleness)
        self.assertEqual(rc, 0)
        self.assertIn("acquired", order)
        self.assertEqual([c for c in calls if c[:2] == ["systemctl", "restart"]],
                         [["systemctl", "restart", "psk.service"]])

    def test_zero_restarts_when_the_post_lock_read_finds_nothing_stale(self):
        """Fix round 1 / finding 2b, restated for the monotonic design: only
        a read taken after the lock decides. Here the freshly-read monotonic
        start (via `run`, itself only ever read after the lock) already
        shows the consumer younger than radiod — as if another align had
        restarted it while this run waited."""
        def fake_staleness(base, services, catalog=None, run=None):
            return {"radiod_consumers": {"psk-recorder"}}

        run, calls = _fake_run(mono={"psk.service": 250.0})
        rc, _ = _run_cmd(run, staleness=fake_staleness)
        self.assertEqual(rc, 0)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])

    def test_lock_timeout_restarts_nothing(self):
        """Fix round 1 / finding 2c."""
        @contextlib.contextmanager
        def timeout_lock(reason, sleep=None, deadline_s=None, clock=None):
            raise TimeoutError("still held")
            yield

        run, calls = _fake_run()
        with mock.patch.object(smd, "_wait_lifecycle_lock", timeout_lock):
            rc, _ = _run_cmd(run)
        self.assertEqual(rc, 3)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])

    def test_a_failed_restart_returns_1(self):
        """Fix round 1 / finding 2d."""
        run, calls = _fake_run(restart_rc=1)
        rc, _ = _run_cmd(run)
        self.assertEqual(rc, 1)
        self.assertTrue([c for c in calls if c[:2] == ["systemctl", "restart"]])


class RadiodSettleWindowTests(unittest.TestCase):
    """final review / I-1: stale = monotonic start < radiod's monotonic
    start PLUS the settle allowance sigmond-radiod-ready itself waits out —
    a consumer that took its anchor inside that window is stale even though
    it started "after" radiod by the wall clock."""

    def test_five_seconds_after_radiod_inside_settle_is_stale(self):
        run, calls = _fake_run(radiod_mono=1000.0, mono={"psk.service": 1005.0})
        rc, out = _run_cmd(run, staleness={"radiod_consumers": {"psk-recorder"}})
        self.assertEqual(rc, 0)
        self.assertEqual([c for c in calls if c[:2] == ["systemctl", "restart"]],
                         [["systemctl", "restart", "psk.service"]])
        self.assertIn("stale", out)

    def test_twenty_seconds_after_radiod_outside_settle_is_not_stale(self):
        run, calls = _fake_run(radiod_mono=1000.0, mono={"psk.service": 1020.0})
        rc, _ = _run_cmd(run, staleness={"radiod_consumers": {"psk-recorder"}})
        self.assertEqual(rc, 0)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])

    def test_exactly_at_the_settle_boundary_is_not_stale(self):
        from sigmond.radiod_ready import READY_SETTLE_S
        run, calls = _fake_run(radiod_mono=1000.0, mono={"psk.service": 1000.0 + READY_SETTLE_S})
        rc, _ = _run_cmd(run, staleness={"radiod_consumers": {"psk-recorder"}})
        self.assertEqual(rc, 0)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])


class RadiodConsumerAccountingTests(unittest.TestCase):
    """final review / I-4: every consumer considered is printed, dry run
    included — label, monotonic offset from radiod's start, and verdict."""

    def test_every_consumer_prints_its_offset_and_verdict(self):
        run, _ = _fake_run(radiod_mono=200.0,
                           mono={"psk.service": 100.0, "wspr.service": 300.0})
        st = {"radiod_consumers": {"psk-recorder", "wspr-recorder"}}
        rc, out = _run_cmd(run, dry_run=True, staleness=st)
        self.assertIn("psk-recorder", out)
        self.assertIn("stale", out)
        self.assertIn("wspr-recorder", out)
        self.assertIn("fresh", out)
        self.assertIn("-100.0", out)   # psk started 100s BEFORE radiod
        self.assertIn("+100.0", out)   # wspr started 100s AFTER radiod

    def test_a_consumer_with_no_active_units_is_reported_not_running(self):
        run, _ = _fake_run(mono={"psk.service": None})
        st = {"radiod_consumers": {"psk-recorder"}}
        rc, out = _run_cmd(run, dry_run=True, staleness=st)
        self.assertIn("psk-recorder", out)
        self.assertIn("not running", out)


class RadiodCatalogGuardTests(unittest.TestCase):
    """final review / I-4: a catalog that fails to load must never let the
    hook fall back to "every running service is a consumer" — that would
    restart sigmond-rac, gpsdo-monitor, station-web, unattended."""

    def test_catalog_load_failure_restarts_nothing_and_exits_4(self):
        run, calls = _fake_run()
        args = argparse.Namespace(unit=RADIOD_UNIT, dry_run=False, wait_ready=90)
        with mock.patch.object(smd, "load_catalog", side_effect=RuntimeError("disk full")), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            rc = smd.cmd_radiod_restart_stale_consumers(args, run=run, sleep=lambda s: None)
        self.assertEqual(rc, 4)
        self.assertIn("catalog did not load", out.getvalue())
        self.assertIn("disk full", out.getvalue())
        self.assertFalse(calls)   # never even asked whether radiod is ready

    def test_catalog_is_passed_through_to_staleness(self):
        run, _ = _fake_run()
        args = argparse.Namespace(unit=RADIOD_UNIT, dry_run=True, wait_ready=90)
        sentinel = {"psk-recorder": object()}
        with mock.patch.object(smd, "load_catalog", return_value=sentinel), \
             mock.patch.object(smd, "_align_services", return_value=SERVICES), \
             mock.patch.object(smd, "_align_staleness",
                              return_value={"radiod_consumers": set()}) as staleness, \
             contextlib.redirect_stdout(io.StringIO()):
            smd.cmd_radiod_restart_stale_consumers(args, run=run, sleep=lambda s: None)
        self.assertEqual(staleness.call_args.kwargs.get("catalog"), sentinel)


class RadiodConsumerRetryPassesTests(unittest.TestCase):
    """final review / I-2: a radiod that restarts again while this run is
    restarting its consumers folds the trigger for THAT start into this
    still-running job — retry from the readiness check, up to 3 passes."""

    def _cmd(self, mono_sequence):
        run, _ = _fake_run()
        st = {"radiod_consumers": set()}
        args = argparse.Namespace(unit=RADIOD_UNIT, dry_run=False, wait_ready=90)
        with mock.patch.object(smd, "_align_services", return_value=SERVICES), \
             mock.patch.object(smd, "_align_staleness", return_value=st), \
             mock.patch.object(smd, "load_catalog", return_value={}), \
             mock.patch.object(smd, "lifecycle_lock",
                              lambda reason=None: contextlib.nullcontext()), \
             mock.patch.object(smd, "_radiod_unit_started_at_monotonic",
                              side_effect=mono_sequence), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            rc = smd.cmd_radiod_restart_stale_consumers(args, run=run, sleep=lambda s: None)
        return rc, out.getvalue()

    def test_radiod_restarting_once_more_gets_exactly_two_passes(self):
        # pass 1 reads 100 (its radiod_start); post-pass recheck reads 105
        # (changed -> retry). pass 2 reads 105 (its radiod_start); post-pass
        # recheck reads 105 again (same -> done). Two passes total.
        rc, out = self._cmd([100.0, 105.0, 105.0, 105.0])
        self.assertEqual(rc, 0)
        self.assertIn("pass 2/3", out)
        self.assertNotIn("pass 3/3", out)

    def test_radiod_that_keeps_changing_stops_after_three_passes(self):
        rc, out = self._cmd([100.0, 110.0, 110.0, 120.0, 120.0, 130.0])
        self.assertEqual(rc, 1)
        self.assertIn("pass 3/3", out)
        self.assertIn("giving up", out)

    def test_radiod_not_active_at_the_recheck_counts_as_changed(self):
        """R1: ``None`` at the post-pass recheck -- radiod inside its own
        start job, in ExecStartPost, or crashed again mid-pass -- must not
        read as "unchanged, done".  It means CHANGED, exactly like a
        different monotonic value, and earns its own pass (whose own
        readiness wait decides what that pass can do)."""
        rc, out = self._cmd([100.0, None, 150.0, 150.0])
        self.assertEqual(rc, 0)
        self.assertIn("pass 2/3", out)
        self.assertNotIn("pass 3/3", out)


class RadiodConsumerLockBudgetTests(unittest.TestCase):
    """R2: the 900s lifecycle-lock wait is a single RUN-WIDE budget shared
    by every pass, not reset each pass -- otherwise 3 passes could each
    wait up to 900s and blow well past the unit's TimeoutStartSec."""

    def test_pass_2_finds_the_run_wide_budget_spent_and_times_out(self):
        t = [0.0]
        busy = [True]
        flipped = [False]

        def sleep(s):
            t[0] += s
            if not flipped[0] and t[0] >= 800.0:
                busy[0] = False
                flipped[0] = True

        @contextlib.contextmanager
        def lock(reason=None):
            if busy[0]:
                raise SystemExit("busy")
            yield
            # released -- another smd operation grabs it right away, so
            # pass 2 finds it busy again with only the leftover budget.
            busy[0] = True

        run, calls = _fake_run()
        st = {"radiod_consumers": set()}
        args = argparse.Namespace(unit=RADIOD_UNIT, dry_run=False, wait_ready=90)
        with mock.patch.object(smd, "_align_services", return_value=SERVICES), \
             mock.patch.object(smd, "_align_staleness", return_value=st), \
             mock.patch.object(smd, "load_catalog", return_value={}), \
             mock.patch.object(smd, "lifecycle_lock", lock), \
             mock.patch.object(smd, "_radiod_unit_started_at_monotonic",
                              side_effect=[100.0, 150.0]), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            rc = smd.cmd_radiod_restart_stale_consumers(
                args, run=run, sleep=sleep, clock=lambda: t[0])
        text = out.getvalue()
        self.assertEqual(rc, 3)
        self.assertIn("pass 1/3", text)
        self.assertIn("pass 2/3", text)
        self.assertNotIn("pass 3/3", text)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])
        # The discriminating assertion: pass 2 must time out at the
        # SHARED deadline (~900s of simulated time total), not get a
        # fresh 900s budget of its own (~1700s) -- a per-pass reset would
        # still eventually time out (the lock is busy forever), so rc==3
        # alone does not prove the budget is run-wide; the elapsed clock
        # does.
        self.assertLess(t[0], 950.0)


class RadiodConsumerWorstRcTests(unittest.TestCase):
    """R3: the run returns the WORST rc across all passes, by precedence
    4 > 3 > 2 > 1 > 0 -- a failed restart in an early pass must not be
    erased by a later, clean pass."""

    def test_pass1_failed_restart_survives_a_later_clean_pass(self):
        run, calls = _fake_run(restart_rc=1)
        staleness_calls = [
            {"radiod_consumers": {"psk-recorder"}},
            {"radiod_consumers": set()},
        ]
        args = argparse.Namespace(unit=RADIOD_UNIT, dry_run=False, wait_ready=90)
        with mock.patch.object(smd, "_align_services", return_value=SERVICES), \
             mock.patch.object(smd, "_align_staleness", side_effect=staleness_calls), \
             mock.patch.object(smd, "load_catalog", return_value={}), \
             mock.patch.object(smd, "lifecycle_lock",
                              lambda reason=None: contextlib.nullcontext()), \
             mock.patch.object(smd, "_radiod_unit_started_at_monotonic",
                              side_effect=[100.0, 150.0, 150.0, 150.0]), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            rc = smd.cmd_radiod_restart_stale_consumers(args, run=run, sleep=lambda s: None)
        restarts = [c for c in calls if c[:2] == ["systemctl", "restart"]]
        self.assertEqual(restarts, [["systemctl", "restart", "psk.service"]])
        self.assertEqual(rc, 1)


class ConsumerHookUnitTests(unittest.TestCase):
    """Task 2: the companion unit and its wiring — static checks only."""

    UNIT = (REPO / "systemd" / "sigmond-radiod-consumers@.service")
    DROP = (REPO / "systemd" / "radiod-consumers.conf")

    def test_companion_runs_the_verb_after_radiod_and_returns_to_inactive(self):
        t = self.UNIT.read_text()
        self.assertIn("After=radiod@%i.service", t)
        self.assertIn("Type=oneshot", t)
        self.assertNotIn("RemainAfterExit=yes", t)
        self.assertIn("ExecStart=/usr/local/bin/smd admin radiod restart-stale-consumers "
                      "--unit radiod@%i.service", t)

    def test_drop_in_pulls_the_companion_on_every_radiod_start(self):
        self.assertIn("Wants=sigmond-radiod-consumers@%i.service", self.DROP.read_text())

    def test_install_sh_installs_both(self):
        t = (REPO / "install.sh").read_text()
        self.assertIn("systemd/radiod-consumers.conf", t)
        self.assertIn("radiod@.service.d/45-sigmond-consumers.conf", t)
        self.assertIn("systemd/sigmond-radiod-consumers@.service", t)

    def test_no_partof_anywhere(self):
        for p in (self.UNIT, self.DROP):
            self.assertNotIn("PartOf=", p.read_text())

    def test_drop_in_never_puts_radiods_start_job_behind_the_hook(self):
        """Binding controller note (Task 1 review): the drop-in must only
        Wants= the companion — never ExecStartPost, Requires=, or Before=,
        any of which would make radiod's own start job wait on a oneshot
        that can block up to 900 s on the lifecycle lock.  smd align holds
        that lock while it restarts radiod; if radiod's start waited on
        this hook, the two would deadlock."""
        t = self.DROP.read_text()
        for forbidden in ("ExecStartPost", "Requires=", "Before="):
            self.assertNotIn(forbidden, t)

    def test_output_is_unbuffered_under_systemd(self):
        """M-1: block-buffering under systemd meant nothing appeared in the
        journal until the process exited — a oneshot that can run for
        minutes needs its progress visible as it happens."""
        self.assertIn("Environment=PYTHONUNBUFFERED=1", self.UNIT.read_text())

    def test_timeout_covers_three_passes_plus_the_lock_wait(self):
        """R2/M-4: 3 passes x 2 readiness checks x (90s wait_ready + 30s
        buffer) + one RUN-WIDE 900s lock budget (shared across all passes,
        not reset each pass) + 3 passes' restart allowance (600s each) =
        3420s worst case; TimeoutStartSec must cover it."""
        t = self.UNIT.read_text()
        self.assertIn("TimeoutStartSec=3600", t)


if __name__ == "__main__":
    unittest.main()
