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
    def test_active_unit_reads_its_start(self):
        run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, _show("active", 1790000000), "")
        self.assertEqual(smd._radiod_unit_started_at("radiod@x.service", run=run), 1790000000.0)

    def test_inactive_unit_reads_none(self):
        run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, _show("inactive", 1), "")
        self.assertIsNone(smd._radiod_unit_started_at("radiod@x.service", run=run))


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

    def _run(self, *, ready_rc=0, radiod_active=True):
        calls = []

        def run(argv, **kw):
            calls.append(list(argv))
            if argv[0] == "/usr/local/sbin/sigmond-radiod-ready":
                return subprocess.CompletedProcess(argv, ready_rc, "", "")
            if argv[:2] == ["systemctl", "show"] and "radiod@x.service" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, _show("active" if radiod_active else "inactive", 200), "")
            if argv[:2] == ["systemctl", "is-active"]:
                return subprocess.CompletedProcess(argv, 0, "active\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return run, calls

    def _cmd(self, run, *, dry_run=False, stale_started=None):
        started = stale_started or {"psk-recorder": 100.0, "wspr-recorder": 300.0}
        st = {"radiod_consumers": {"psk-recorder", "wspr-recorder"}, "started_at": started,
              "running": set(started)}
        args = argparse.Namespace(unit="radiod@x.service", dry_run=dry_run, wait_ready=90)
        with mock.patch.object(smd, "_align_services", return_value=self.SERVICES), \
             mock.patch.object(smd, "_align_staleness", return_value=st), \
             mock.patch.object(smd, "lifecycle_lock", lambda reason=None: contextlib.nullcontext()), \
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


if __name__ == "__main__":
    unittest.main()
