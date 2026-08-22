"""sigmond#52 — `smd disable` must leave nothing systemd-ENABLED: a reboot
resurrected wspr-recorder@DASI002 after a disable on B4 (2026-08-21)."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

from sigmond.lifecycle import UnitRef

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader("smd_under_test_disable", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_disable", loader)
    mod = importlib.util.module_from_spec(spec); loader.exec_module(mod)
    return mod


smd = _load_smd()


def _unit(name, kind="service"):
    return UnitRef(component="wspr-recorder", unit=name, template=None, instance=None,
                   kind=kind, source="deploy.toml:x")


class StopComponentUnitsTests(unittest.TestCase):
    def _run_capture(self):
        calls = []
        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            import subprocess
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return calls, fake_run

    def test_disable_stops_and_disables_every_unit(self):
        calls, fake_run = self._run_capture()
        units = [_unit("wspr-recorder@DASI002.service"), _unit("wspr-uploader.timer", "timer")]
        with mock.patch.object(smd, "_run", side_effect=fake_run), \
             mock.patch.object(smd, "resolve_units", return_value=units), \
             mock.patch.object(smd, "_load_topology", return_value=object()), \
             mock.patch.object(smd, "order_units", side_effect=lambda u, coordination=None: u), \
             mock.patch.object(smd, "load_coordination", return_value=None):
            smd._stop_component_units("wspr-recorder")
        stopped = [c[2] for c in calls if c[:2] == ["systemctl", "stop"]]
        disabled = [c[2] for c in calls if c[:2] == ["systemctl", "disable"]]
        for u in ("wspr-recorder@DASI002.service", "wspr-uploader.timer"):
            self.assertIn(u, stopped, calls)
            self.assertIn(u, disabled)
        # stop before disable for the same unit (stop first leaves nothing running)
        self.assertLess(calls.index(["systemctl", "stop", "wspr-recorder@DASI002.service"]),
                        calls.index(["systemctl", "disable", "wspr-recorder@DASI002.service"]))

    def test_fallback_path_also_disables(self):
        """No topology entry any more (disable/remove after the fact): units
        come from deploy.toml directly — still stop AND disable."""
        calls, fake_run = self._run_capture()
        import sigmond.component_state as cs
        with mock.patch.object(smd, "_run", side_effect=fake_run), \
             mock.patch.object(smd, "resolve_units", side_effect=RuntimeError("not in topology")), \
             mock.patch.object(smd, "_load_topology", return_value=object()), \
             mock.patch.object(cs, "_read_deploy_toml", return_value={"systemd": {"units": ["psk-recorder.service"]}}), \
             mock.patch.object(cs, "_systemd_unit_names", return_value=["psk-recorder.service"]), \
             mock.patch.object(cs, "_expand_running_instances", return_value=[]):
            smd._stop_component_units("psk-recorder")
        self.assertIn(["systemctl", "stop", "psk-recorder.service"], calls)
        self.assertIn(["systemctl", "disable", "psk-recorder.service"], calls)
