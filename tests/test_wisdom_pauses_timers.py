"""`smd admin wisdom plan` pauses sigmond's own timers while fftwf-wisdom
measures, and restarts exactly those afterwards (rob, 2026-11-12)."""
import importlib.machinery
import importlib.util
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_wisdom_timers", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_wisdom_timers", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class Timers(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def run(argv, **kw):
            self.calls.append(list(argv))
            out = ""
            if argv[:2] == ["systemctl", "list-units"]:
                out = ("sigmond-storage.timer loaded active waiting x\n"
                       "sigmond-snapshot.timer loaded active waiting y\n")
            return subprocess.CompletedProcess(argv, 0, out, "")
        for p in (mock.patch.object(smd, "_run", side_effect=run),
                  mock.patch.object(smd, "_info"), mock.patch.object(smd, "_warn")):
            p.start(); self.addCleanup(p.stop)

    def test_pause_then_resume_the_same_timers(self):
        t = smd._pause_sigmond_timers()
        self.assertEqual(t, ["sigmond-storage.timer", "sigmond-snapshot.timer"])
        smd._resume_sigmond_timers(t)
        acts = [c for c in self.calls if c[1] in ("stop", "start")]
        self.assertEqual(acts, [["systemctl", "stop", *t], ["systemctl", "start", *t]])
        self.assertIn("sigmond-*.timer", self.calls[0])

    def test_nothing_to_resume_does_nothing(self):
        smd._resume_sigmond_timers([])
        self.assertEqual(self.calls, [])

    def test_plan_resumes_in_its_finally(self):
        src = (REPO / "bin" / "smd").read_text()
        i = src.index("def cmd_wisdom_plan(")
        body = src[i:src.index("\ndef ", i + 10)]
        a = body.index("paused_timers = _pause_sigmond_timers()")
        b = body.index("subprocess.Popen(")
        c = body.index("finally:\n        _resume_sigmond_timers(paused_timers)")
        self.assertLess(a, b)
        self.assertLess(b, c)


if __name__ == "__main__":
    unittest.main()
