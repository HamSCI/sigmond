"""smd's instance and lifecycle verbs show a person AC0G/B4 and hand
systemctl AC0G\\x3dB4 (tasks/spec-reporter-id-v372.md §4; inventory rows
S52, S53, plus the lifecycle verbs, which print UnitRef.unit and so began
to carry systemd's spelling when Task 3 escaped it)."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_instance_display", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class InstanceVerbTests(unittest.TestCase):

    def test_disable_shows_the_display_form_and_sends_the_systemd_form(self):
        calls = []

        def run(argv, **kw):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "", "")
        out = io.StringIO()
        with mock.patch.object(smd, "_need_root", lambda *a, **k: False), \
             mock.patch("subprocess.run", run), contextlib.redirect_stdout(out):
            rc = smd.cmd_instance_disable(
                argparse.Namespace(client="psk-recorder", reporter_id="AC0G/B4"))
        self.assertEqual(rc, 0)
        self.assertIn(["systemctl", "stop", "psk-recorder@AC0G\\x3dB4.service"], calls)
        self.assertIn(["systemctl", "disable", "psk-recorder@AC0G\\x3dB4.service"], calls)
        self.assertIn("psk-recorder@AC0G/B4 stopped and disabled", out.getvalue())
        self.assertNotIn("\\x3d", out.getvalue())

    def test_show_names_the_instance_and_prints_a_working_journal_command(self):
        inst = smd._instance_module()
        found = inst.Instance(paths=inst.instance_paths("psk-recorder", "AC0G=B4"),
                              has_config=True, has_env=True, has_sources=True)
        out = io.StringIO()
        with mock.patch.object(inst, "get_instance", lambda c, r: found), \
             mock.patch("subprocess.run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0, "active\n", "")), \
             contextlib.redirect_stdout(out):
            rc = smd.cmd_instance_show(
                argparse.Namespace(client="psk-recorder", reporter_id="AC0G\\x3dB4"))
        text = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("unit:     psk-recorder@AC0G/B4", text)
        # A printed command uses the form that command accepts (spec §4).
        self.assertIn("journal:  journalctl -u 'psk-recorder@AC0G\\x3dB4.service'", text)


class LifecycleVerbOutputTests(unittest.TestCase):

    def test_no_lifecycle_message_prints_a_raw_unit_name(self):
        src = (REPO / "bin" / "smd").read_text()
        raw = re.findall(r"\{(?:unit|u|unit_ref)\.unit\}", src)
        self.assertEqual(raw, [], "smd prints UnitRef.unit without _display_unit()")
        self.assertNotIn("following journal for: {\", \".join(unit_names)}", src)

    def test_display_unit_is_the_instance_helper(self):
        from sigmond.instance import display_unit_name
        self.assertIs(smd._display_unit, display_unit_name)


if __name__ == "__main__":
    unittest.main()
