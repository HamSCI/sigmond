"""`smd admin viewers --json`: the station's web viewers, ports and conflicts."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import viewers  # noqa: E402


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_viewers", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ONE = [("ka9q-web.service", "bee1-status.local", 8081)]
TWO = ONE + [("ka9q-web@b.service", "bee2-status.local", 8083)]


def _active(unit):
    return "active"


class ListViewersTests(unittest.TestCase):

    def test_one_radiod_and_gmag(self):
        rows = viewers.list_viewers(ONE, True, _active)
        self.assertEqual([r["name"] for r in rows], ["ka9q-web", "gmag-webui"])
        self.assertEqual(rows[0]["label"],
                         "Spectrum and waterfall (bee1-status.local)")
        self.assertEqual(rows[0]["port"], 8081)
        self.assertEqual(rows[0]["unit"], "ka9q-web.service")
        self.assertEqual(rows[1]["label"], "Magnetometer")
        self.assertEqual(rows[1]["port"], 8082)
        self.assertEqual(rows[1]["unit"], "gmag-webui.service")
        self.assertEqual(rows[1]["active"], "active")
        for r in rows:
            self.assertEqual(set(r), {"name", "label", "port", "unit",
                                      "active", "description"})

    def test_two_radiods_name_the_second_with_a_suffix(self):
        rows = viewers.list_viewers(TWO, False, _active)
        self.assertEqual([r["name"] for r in rows],
                         ["ka9q-web", "ka9q-web-2"])

    def test_nothing_installed_is_empty(self):
        self.assertEqual(viewers.list_viewers([], False, _active), [])

    def test_gmag_not_installed_is_left_out(self):
        rows = viewers.list_viewers(ONE, False, _active)
        self.assertEqual([r["name"] for r in rows], ["ka9q-web"])

    def test_is_active_raising_reads_unknown(self):
        def boom(unit):
            raise OSError("no systemctl")
        rows = viewers.list_viewers(ONE, True, boom)
        self.assertEqual([r["active"] for r in rows], ["unknown", "unknown"])


class ConflictTests(unittest.TestCase):

    def test_port_conflicts_empty_with_two_radiods_and_gmag(self):
        rows = viewers.list_viewers(TWO, True, _active)
        self.assertEqual(viewers.port_conflicts(rows), [])

    def test_two_viewers_on_one_port_are_reported(self):
        rows = viewers.list_viewers(
            ONE + [("ka9q-web@b.service", "bee2-status.local", 8082)],
            True, _active)
        self.assertEqual(viewers.port_conflicts(rows),
                         [{"port": 8082, "names": ["ka9q-web-2", "gmag-webui"]}])

    def test_no_conflict_when_ports_differ(self):
        rows = viewers.list_viewers(ONE, True, _active)
        self.assertEqual(viewers.port_conflicts(rows), [])


class SystemctlActiveTests(unittest.TestCase):

    def _proc(self, out, rc=3):
        return subprocess.CompletedProcess([], rc, stdout=out, stderr="")

    def test_word_is_read_despite_nonzero_exit(self):
        with mock.patch("subprocess.run", return_value=self._proc("inactive\n")):
            self.assertEqual(viewers.systemctl_is_active("x.service"), "inactive")

    def test_empty_output_is_unknown(self):
        with mock.patch("subprocess.run", return_value=self._proc("")):
            self.assertEqual(viewers.systemctl_is_active("x.service"), "unknown")

    def test_timeout_is_unknown(self):
        with mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired("systemctl", 5)):
            self.assertEqual(viewers.systemctl_is_active("x.service"), "unknown")


class CliTests(unittest.TestCase):

    def test_json_output(self):
        smd = _load_smd()
        out = io.StringIO()
        with mock.patch.object(smd, "_ka9q_web_targets", return_value=TWO), \
                mock.patch("sigmond.component_state.compute_state",
                           return_value=mock.Mock(installed=True)), \
                mock.patch("subprocess.run",
                           return_value=subprocess.CompletedProcess(
                               [], 0, stdout="active\n", stderr="")), \
                contextlib.redirect_stdout(out):
            rc = smd.cmd_admin_viewers(argparse.Namespace(json=True))
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertEqual([v["name"] for v in data["viewers"]],
                         ["ka9q-web", "ka9q-web-2", "gmag-webui"])
        self.assertEqual(data["conflicts"], [])


if __name__ == "__main__":
    unittest.main()
