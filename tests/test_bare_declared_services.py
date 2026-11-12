"""A bare `smd stop|start|restart` covers hs-uploader.service (catalog
`services`, outside topology) when it is enabled on the host: stop before the
topology services, start/restart after them (rob, 2026-11-12)."""
import importlib.machinery
import importlib.util
import os
import subprocess
import unittest
from types import SimpleNamespace as NS
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_bare_declared", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_bare_declared", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()
CAT = {"hs-uploader": NS(name="hs-uploader", services=("hs-uploader.service",)),
       "psk-recorder": NS(name="psk-recorder", services=())}


class Bare(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.enabled = {"hs-uploader.service"}

        def run(argv, **kw):
            self.calls.append(list(argv))
            out = ""
            if argv[:2] == ["systemctl", "is-enabled"]:
                out = "enabled\n" if argv[2] in self.enabled else "disabled\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        for p in (mock.patch.object(smd, "_run", side_effect=run),
                  mock.patch.object(smd, "load_catalog", return_value=CAT),
                  mock.patch.object(smd, "_load_topology", return_value={}),
                  mock.patch.object(smd, "_enabled_components", return_value=["psk-recorder"]),
                  mock.patch.object(smd, "_ok"), mock.patch.object(smd, "_info")):
            p.start(); self.addCleanup(p.stop)

    def acts(self):
        return [c[1:] for c in self.calls if c[0] == "systemctl"
                and c[1] in ("start", "stop", "restart")]

    def test_bare_stop_stops_it(self):
        smd._act_declared_services("stop", NS(names=[], components=None))
        self.assertEqual(self.acts(), [["stop", "hs-uploader.service"]])

    def test_bare_start_waits_for_the_after_pass(self):
        smd._act_declared_services("start", NS(names=[], components=None))
        self.assertEqual(self.acts(), [])
        smd._act_declared_services("start", NS(names=[], components=None), bare_pass=True)
        self.assertEqual(self.acts(), [["start", "hs-uploader.service"]])

    def test_bare_restart_after_pass(self):
        smd._act_declared_services("restart", NS(names=["all"], components=None), bare_pass=True)
        self.assertEqual(self.acts(), [["restart", "hs-uploader.service"]])

    def test_not_enabled_on_this_host_is_left_alone(self):
        self.enabled = set()
        smd._act_declared_services("stop", NS(names=[], components=None))
        self.assertEqual(self.acts(), [])

    def test_named_verbs_ignore_the_after_pass(self):
        smd._act_declared_services("restart", NS(names=["psk-recorder"], components=None),
                                   bare_pass=True)
        self.assertEqual(self.acts(), [])

    def test_restart_and_start_call_the_after_pass(self):
        src = (REPO / "bin" / "smd").read_text()
        for v in ("start", "restart"):
            i = src.index(f"def cmd_{v}(args)")
            body = src[i:src.index("\ndef ", i + 10)]
            self.assertIn(f"_act_declared_services('{v}', args, bare_pass=True)", body)


if __name__ == "__main__":
    unittest.main()
