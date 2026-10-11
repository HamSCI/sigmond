"""The guest agent's drop-in (v3.71): the 128 MB cap stays, a killed child no
longer takes the agent with it, and `smd doctor` keeps the file current.

The cap covers every process a `qm guest exec` starts.  Under systemd's
default OOMPolicy=stop, a child that outgrew it made systemd stop the whole
unit, and the agent restarted under its caller (the update rig, 2026-10-08
23:46:26Z).  OOMPolicy=continue lets the kernel kill the child alone.

install.sh writes the drop-in only when install.sh runs, and `smd update`
re-runs install.sh only when install.sh, pyproject.toml or uv.lock changed.
So doctor reports a stale copy and --fix rewrites it, as for radiod's.
"""
import importlib.machinery
import importlib.util
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
CONF = REPO / "systemd" / "qemu-guest-agent-selfheal.conf"
REL = "qemu-guest-agent.service.d/50-sigmond-selfheal.conf"


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_qga_dropin", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_qga_dropin", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def service_settings(text):
    """KEY=VALUE lines under [Service], comments and blanks dropped."""
    out, section = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line
            continue
        if section == "[Service]":
            out.append(line)
    return out


def fake_run(calls, agent_installed=True):
    """Stands in for smd's _run: answers `systemctl cat` and `daemon-reload`,
    and performs `install` without the chown.  Anything else fails the test."""
    def run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[:2] == ["systemctl", "cat"]:
            return types.SimpleNamespace(returncode=0 if agent_installed else 1,
                                         stdout="", stderr="")
        if cmd[:2] == ["systemctl", "daemon-reload"]:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd and cmd[0] == "install":
            if "-d" in cmd:
                Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
            else:
                Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[-1]).write_bytes(Path(cmd[-2]).read_bytes())
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command {cmd}")
    return run


class ShippedDropinTests(unittest.TestCase):
    def test_the_cap_stays_at_128M(self):
        self.assertIn("MemoryMax=128M", service_settings(CONF.read_text()))

    def test_a_killed_child_leaves_the_agent_running(self):
        self.assertIn("OOMPolicy=continue", service_settings(CONF.read_text()))

    def test_it_sets_nothing_else(self):
        self.assertEqual(sorted(service_settings(CONF.read_text())),
                         ["MemoryMax=128M", "OOMPolicy=continue"])

    def test_the_file_holds_ascii_only(self):
        CONF.read_bytes().decode("ascii")

    def test_doctor_writes_it_where_install_sh_does(self):
        self.assertEqual(smd._QGA_DROPIN, ("qemu-guest-agent-selfheal.conf", REL))
        self.assertIn("/etc/systemd/system/" + REL, (REPO / "install.sh").read_text())


class EnsureAndFindingTests(unittest.TestCase):
    def setUp(self):
        self.tdir = Path(tempfile.mkdtemp())
        self.src = self.tdir / "src"
        self.src.mkdir()
        (self.src / "qemu-guest-agent-selfheal.conf").write_bytes(
            b"[Service]\nMemoryMax=128M\nOOMPolicy=continue\n")
        self.etc = self.tdir / "etc"
        self.etc.mkdir()

    def kw(self, calls, **k):
        return dict(systemd_dir=self.etc, src_dir=self.src, run=fake_run(calls, **k))

    def test_no_agent_unit_writes_nothing_and_finds_nothing(self):
        calls = []
        self.assertEqual(smd._ensure_qga_dropin(**self.kw(calls, agent_installed=False)), [])
        self.assertEqual(smd._qga_dropin_findings(**self.kw(calls, agent_installed=False)), [])
        self.assertEqual(list(self.etc.rglob("*")), [])

    def test_the_gate_asks_about_the_agent_unit(self):
        calls = []
        smd._qga_dropin_findings(**self.kw(calls))
        self.assertIn(["systemctl", "cat", "qemu-guest-agent.service"], calls)
        calls = []
        smd._ensure_qga_dropin(**self.kw(calls))
        self.assertIn(["systemctl", "cat", "qemu-guest-agent.service"], calls)

    def test_a_missing_dropin_gets_written_then_one_reload(self):
        calls = []
        written = smd._ensure_qga_dropin(**self.kw(calls))
        self.assertEqual(written, [str(self.etc / REL)])
        self.assertEqual((self.etc / REL).read_bytes(),
                         (self.src / "qemu-guest-agent-selfheal.conf").read_bytes())
        self.assertEqual(sum(c[:2] == ["systemctl", "daemon-reload"] for c in calls), 1)

    def test_a_current_dropin_writes_nothing_and_reloads_nothing(self):
        smd._ensure_qga_dropin(**self.kw([]))
        calls = []
        self.assertEqual(smd._ensure_qga_dropin(**self.kw(calls)), [])
        self.assertFalse(any(c[:2] == ["systemctl", "daemon-reload"] for c in calls))

    def test_ensure_never_restarts_or_stops_the_agent(self):
        calls = []
        smd._ensure_qga_dropin(**self.kw(calls))
        for c in calls:
            self.assertNotIn("restart", c)
            self.assertNotIn("stop", c)

    def test_a_stale_dropin_makes_one_fixable_finding_naming_its_path(self):
        (self.etc / REL).parent.mkdir(parents=True)
        (self.etc / REL).write_bytes(b"[Service]\nMemoryMax=128M\n")
        (f,) = smd._qga_dropin_findings(**self.kw([]))
        self.assertEqual(f.kind, "qga-dropin-stale")
        self.assertTrue(f.fixable)
        self.assertIn(str(self.etc / REL), f.detail)
        self.assertIn("smd doctor --fix", f.detail)

    def test_a_current_dropin_makes_no_finding(self):
        smd._ensure_qga_dropin(**self.kw([]))
        self.assertEqual(smd._qga_dropin_findings(**self.kw([])), [])


class DoctorWiringTests(unittest.TestCase):
    def test_fix_calls_ensure_qga_dropin(self):
        calls = []
        args = types.SimpleNamespace(base=tempfile.mkdtemp(), fix=True)
        with mock.patch.object(smd, "_ensure_radiod_dropins", return_value=[]), \
             mock.patch.object(smd, "_ensure_qga_dropin",
                               side_effect=lambda **kw: calls.append(True) or []), \
             mock.patch.object(smd, "collect_findings", return_value=(True, [])):
            smd.cmd_doctor(args)
        self.assertEqual(calls, [True])

    def test_collect_findings_runs_the_qga_family(self):
        from sigmond.doctor import Finding
        f = Finding("sigmond", "qga-dropin-stale", "x", fixable=True)
        with mock.patch.object(smd, "_qga_dropin_findings", return_value=[f]):
            _, findings = smd.collect_findings(base=tempfile.mkdtemp(), strict=False)
        self.assertIn(f, findings)
