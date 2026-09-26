"""`smd admin radiod restart-stale-consumers` — every system call faked.

Restarts the radiod consumers still running from before radiod's current
start (2026-09-26 plan: AC0G-ND's RX888 stalls make radiod crash; systemd
restarts it; recorders that keep running hold the old radiod's RTP->UTC
anchor and decode nothing). Run by sigmond-radiod-consumers@.service on
every radiod start (Task 2).
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
                                          clock=lambda: t[0]):
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
                                              clock=lambda: t[0]):
                    pass


class RestartStaleConsumersCmdTests(unittest.TestCase):
    """The verb end to end, with every system call faked."""
    SERVICES = {"ka9q-radio": ["radiod@x.service"], "psk-recorder": ["psk.service"],
                "wspr-recorder": ["wspr.service"]}

    def _run(self, *, ready_rc=0, radiod_active=True, ready_rcs=None, restart_rc=0):
        calls = []
        ready_iter = iter(ready_rcs) if ready_rcs is not None else None

        def run(argv, **kw):
            calls.append(list(argv))
            if argv[0] == "/usr/local/sbin/sigmond-radiod-ready":
                if ready_iter is not None:
                    rc = next(ready_iter, ready_rcs[-1])
                else:
                    rc = ready_rc
                return subprocess.CompletedProcess(argv, rc, "", "")
            if argv[:2] == ["systemctl", "show"] and "radiod@x.service" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, _show("active" if radiod_active else "inactive", 200), "")
            if argv[:2] == ["systemctl", "is-active"]:
                return subprocess.CompletedProcess(argv, 0, "active\n", "")
            if argv[:2] == ["systemctl", "restart"]:
                return subprocess.CompletedProcess(
                    argv, restart_rc, "", "boom" if restart_rc else "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return run, calls

    def _cmd(self, run, *, dry_run=False, stale_started=None, lock=None, staleness=None):
        args = argparse.Namespace(unit="radiod@x.service", dry_run=dry_run, wait_ready=90)
        lock_fn = lock if lock is not None else (lambda reason=None: contextlib.nullcontext())
        if callable(staleness):
            staleness_patch = mock.patch.object(smd, "_align_staleness", side_effect=staleness)
        else:
            started = stale_started or {"psk-recorder": 100.0, "wspr-recorder": 300.0}
            st = staleness or {"radiod_consumers": {"psk-recorder", "wspr-recorder"},
                               "started_at": started, "running": set(started)}
            staleness_patch = mock.patch.object(smd, "_align_staleness", return_value=st)
        with mock.patch.object(smd, "_align_services", return_value=self.SERVICES), \
             staleness_patch, \
             mock.patch.object(smd, "lifecycle_lock", lock_fn), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            rc = smd.cmd_radiod_restart_stale_consumers(args, run=run, sleep=lambda s: None)
        return rc, out.getvalue()

    def test_restarts_only_the_stale_consumer_after_readiness(self):
        run, calls = self._run()
        rc, out = self._cmd(run)
        self.assertEqual(rc, 0)
        restarts = [c for c in calls if c[:2] == ["systemctl", "restart"]]
        self.assertEqual(restarts, [["systemctl", "restart", "psk.service"]])
        ready = next(i for i, c in enumerate(calls) if c[0] == "/usr/local/sbin/sigmond-radiod-ready")
        self.assertLess(ready, calls.index(restarts[0]))
        self.assertNotIn(["systemctl", "restart", "radiod@x.service"], calls)

    def test_radiod_not_ready_restarts_nothing(self):
        run, calls = self._run(ready_rc=1)
        rc, _ = self._cmd(run)
        self.assertEqual(rc, 2)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])

    def test_dry_run_restarts_nothing_and_names_the_stale(self):
        run, calls = self._run()
        rc, out = self._cmd(run, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertIn("psk-recorder", out)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])

    def test_nothing_stale_is_a_quiet_no_op(self):
        run, calls = self._run()
        rc, out = self._cmd(run, stale_started={"psk-recorder": 300.0, "wspr-recorder": 300.0})
        self.assertEqual(rc, 0)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])

    def test_radiod_not_ready_on_the_post_lock_recheck_restarts_nothing(self):
        """Fix round 1 / finding 1: radiod can crash-restart again while
        this run waited for the lock — the post-lock recheck must catch
        that and refuse, not trust a start time nobody confirmed ready."""
        run, calls = self._run(ready_rcs=[0, 1])
        rc, _ = self._cmd(run)
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

        def fake_staleness(base, services, run=None):
            self.assertIn("acquired", order)
            return {"radiod_consumers": {"psk-recorder"},
                    "started_at": {"psk-recorder": 100.0}, "running": {"psk-recorder"}}

        run, calls = self._run()
        rc, _ = self._cmd(run, lock=lock, staleness=fake_staleness)
        self.assertEqual(rc, 0)
        self.assertIn("acquired", order)
        self.assertEqual([c for c in calls if c[:2] == ["systemctl", "restart"]],
                         [["systemctl", "restart", "psk.service"]])

    def test_zero_restarts_when_the_post_lock_read_finds_nothing_stale(self):
        """Fix round 1 / finding 2b: only the read taken after the lock
        decides — a fresh look (e.g. another align already restarted the
        consumer while this run waited) wins over anything read earlier."""
        def fake_staleness(base, services, run=None):
            return {"radiod_consumers": {"psk-recorder"},
                    "started_at": {"psk-recorder": 200.0}, "running": {"psk-recorder"}}

        run, calls = self._run()
        rc, _ = self._cmd(run, staleness=fake_staleness)
        self.assertEqual(rc, 0)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])

    def test_lock_timeout_restarts_nothing(self):
        """Fix round 1 / finding 2c."""
        @contextlib.contextmanager
        def timeout_lock(reason, sleep=None):
            raise TimeoutError("still held")
            yield

        run, calls = self._run()
        with mock.patch.object(smd, "_wait_lifecycle_lock", timeout_lock):
            rc, _ = self._cmd(run)
        self.assertEqual(rc, 3)
        self.assertFalse([c for c in calls if c[:2] == ["systemctl", "restart"]])

    def test_a_failed_restart_returns_1(self):
        """Fix round 1 / finding 2d."""
        run, calls = self._run(restart_rc=1)
        rc, _ = self._cmd(run)
        self.assertEqual(rc, 1)
        self.assertTrue([c for c in calls if c[:2] == ["systemctl", "restart"]])


if __name__ == "__main__":
    unittest.main()
