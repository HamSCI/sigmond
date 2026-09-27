"""Tests for scripts/netguard.py's `--enforce` path.

M5: netguard runs `smd stop` under systemd (sigmond-netguard.service) with
`subprocess.run(..., timeout=600)`, but `smd` may now wait up to
SIGMOND_LOCK_WAIT_S (default 900s) for the lifecycle lock before giving up
(lib/sigmond/lifecycle.py, lifecycle_lock's systemd wait). netguard's own
600s subprocess timeout would then fire and kill `smd stop` mid-wait,
which is worse than the immediate refusal it used to get. Cap the wait
netguard is willing to tolerate to something safely under its own 600s
budget by passing SIGMOND_LOCK_WAIT_S=540 in that subprocess's environment.
"""
import importlib.util
import os
import pathlib
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "netguard_under_test", REPO / "scripts" / "netguard.py")
netguard = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = netguard
_spec.loader.exec_module(netguard)


def _fake_public_addrs():
    return ([("eth0", "203.0.113.5")], [])


class TestNetguardEnforceLockWaitEnv:
    def test_smd_stop_gets_bounded_sigmond_lock_wait_s(self, tmp_path, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((list(cmd), kwargs))
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(netguard, "public_addrs", _fake_public_addrs)
        monkeypatch.setattr(netguard.subprocess, "run", fake_run)
        monkeypatch.setattr(netguard, "OVERRIDE", tmp_path / "no-such-override")

        # netguard hardcodes "/usr/local/bin/smd"; make that path resolve
        # as present by patching Path.exists narrowly for it (subprocess.run
        # is faked above, so nothing actually executes it).
        real_exists = pathlib.Path.exists

        def fake_exists(self):
            if str(self) == "/usr/local/bin/smd":
                return True
            return real_exists(self)

        monkeypatch.setattr(pathlib.Path, "exists", fake_exists)
        monkeypatch.setattr(sys, "argv", ["netguard.py", "--enforce"])

        rc = netguard.main()

        assert rc == 1
        smd_calls = [c for c in calls if c[0][:2] == ["/usr/local/bin/smd", "stop"]]
        assert len(smd_calls) == 1
        _, kwargs = smd_calls[0]
        env = kwargs.get("env")
        assert env is not None, "smd stop must be called with an explicit env"
        assert env.get("SIGMOND_LOCK_WAIT_S") == "540"
        # Must still carry the rest of the process environment (copy, not
        # replace) — spot-check PATH survived.
        assert env.get("PATH") == os.environ.get("PATH")
