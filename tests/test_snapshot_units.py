"""The snapshot units, their tmpfiles entry, and install.sh's part in them."""
import configparser
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SYSTEMD = REPO / "systemd"
INSTALL = REPO / "install.sh"


def _unit(name):
    path = SYSTEMD / name
    assert path.is_file(), f"missing {path}"
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string(path.read_text())
    return cp


class UnitFileTests(unittest.TestCase):

    def _assert_keys(self, name, section, expected):
        cp = _unit(name)
        self.assertIn(section, cp, f"{name}: no [{section}]")
        for key, value in expected.items():
            self.assertEqual(cp[section].get(key), value, f"{name} [{section}] {key}")

    def test_snapshot_service(self):
        self._assert_keys("sigmond-snapshot.service", "Service", {
            "Type": "oneshot", "User": "root", "Nice": "19", "IOWeight": "10",
            "ExecStart": "/usr/local/bin/smd admin snapshot write",
            "SyslogIdentifier": "sigmond-snapshot",
        })

    def test_snapshot_timer(self):
        self._assert_keys("sigmond-snapshot.timer", "Timer", {
            "OnCalendar": "*:0/5", "RandomizedDelaySec": "30",
            "Persistent": "false",
        })
        self._assert_keys("sigmond-snapshot.timer", "Install",
                          {"WantedBy": "timers.target"})

    def test_request_path(self):
        self._assert_keys("sigmond-snapshot-request.path", "Path", {
            "PathExistsGlob": "/run/sigmond/snapshot-request/*",
            "Unit": "sigmond-snapshot-request.service",
        })
        self.assertIn("Install", _unit("sigmond-snapshot-request.path"))

    def test_request_service(self):
        self._assert_keys("sigmond-snapshot-request.service", "Service", {
            "Type": "oneshot", "User": "root", "Nice": "19", "IOWeight": "10",
            "ExecStart": "/usr/local/bin/smd admin snapshot write --request",
            "SyslogIdentifier": "sigmond-snapshot",
        })

    def test_component_fetch_service(self):
        self._assert_keys("sigmond-component-fetch.service", "Service", {
            "Type": "oneshot", "User": "root", "Nice": "19",
            "ExecStart": "/usr/local/bin/smd component list --json",
            "StandardOutput": "null",
        })

    def test_component_fetch_timer(self):
        self._assert_keys("sigmond-component-fetch.timer", "Timer", {
            "OnCalendar": "daily", "RandomizedDelaySec": "3600",
            "Persistent": "true",
        })
        self._assert_keys("sigmond-component-fetch.timer", "Install",
                          {"WantedBy": "timers.target"})

    def test_tmpfiles_conf(self):
        lines = [l.split() for l in
                 (SYSTEMD / "sigmond-snapshot.conf").read_text().splitlines()
                 if l.strip() and not l.lstrip().startswith("#")]
        self.assertEqual(lines, [
            ["d", "/run/sigmond/snapshot", "0750", "root", "sigmond-status", "-"],
            ["d", "/run/sigmond/snapshot-request", "2770", "root", "sigmond-status", "-"],
        ])


class InstallShTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = INSTALL.read_text()

    def test_creates_the_sigmond_status_group(self):
        self.assertRegex(self.text, r"getent group sigmond-status")
        self.assertRegex(self.text, r"groupadd --system sigmond-status")

    def test_installs_path_units(self):
        loop = re.search(r"for _unit in [^\n]*; do", self.text)
        self.assertIsNotNone(loop)
        self.assertIn('"$REPO_DIR"/systemd/sigmond-*.path', loop.group(0))

    def test_installs_the_tmpfiles_conf(self):
        self.assertIn('"$REPO_DIR/systemd/sigmond-snapshot.conf" /etc/tmpfiles.d/',
                      self.text)
        self.assertIn("systemd-tmpfiles --create /etc/tmpfiles.d/sigmond-snapshot.conf",
                      self.text)

    def test_the_group_exists_before_the_tmpfiles_conf_needs_it(self):
        self.assertLess(self.text.index("groupadd --system sigmond-status"),
                        self.text.index("systemd-tmpfiles --create /etc/tmpfiles.d/sigmond-snapshot.conf"))

    def test_enables_the_three_units(self):
        for unit in ("sigmond-snapshot.timer", "sigmond-snapshot-request.path",
                     "sigmond-component-fetch.timer"):
            self.assertRegex(self.text,
                             rf"systemctl enable --now [^\n]*\b{re.escape(unit)}",
                             unit)


if __name__ == "__main__":
    unittest.main()
