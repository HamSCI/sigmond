"""`smd admin sources apply` renders a one-radiod selection into clients
that read a single radiod from their [[radiod]]/[radiod] table.  AI6VN-PM
2026-10-31: psk-recorder@AI6VN-SE was left on omni-status.local, doubled
omni's load and failed every decode on the shared state dir."""
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_smd():
    spec = importlib.util.spec_from_loader("smd_mod_sources", loader=None)
    smd = importlib.util.module_from_spec(spec)
    smd.__dict__["__file__"] = "bin/smd"
    src = Path(__file__).resolve().parent.parent / "bin" / "smd"
    with mock.patch.dict(os.environ, {"SIGMOND_NO_VENV_REEXEC": "1"}):
        exec(compile(src.read_text(), str(src), "exec"), smd.__dict__)
    return smd


PSK = """[instance]
reporter_id = "AI6VN-SE"
sources = []

# [timing]
# chain_delay_ns = 0

[[radiod]]
# REQUIRED: the mDNS control/status multicast name of the radiod to
# consume from.
status = "omni-status.local"

[radiod.ft8]
sample_rate = 12000
status_file = "status.json"
"""

HFDL = """[radiod]
status = 'omni-status.local'   # trailing comment
[output]
status = "unrelated"
"""


class SetClientRadiodStatusTests(unittest.TestCase):
    def setUp(self):
        self.smd = _load_smd()

    def test_rewrites_only_the_radiod_table(self):
        out = self.smd._set_client_radiod_status(PSK, "se-status.local")
        self.assertIn('status = "se-status.local"', out)
        self.assertNotIn("omni-status.local", out)
        self.assertIn('status_file = "status.json"', out)      # [radiod.ft8] untouched
        self.assertIn("# consume from.", out)                   # comments kept

    def test_single_quoted_and_other_tables(self):
        out = self.smd._set_client_radiod_status(HFDL, "se-status.local")
        self.assertIn('status = "se-status.local"   # trailing comment', out)
        self.assertIn('status = "unrelated"', out)

    def test_no_change_when_already_set(self):
        text = PSK.replace("omni-status.local", "se-status.local")
        self.assertEqual(self.smd._set_client_radiod_status(text, "se-status.local"), text)


class ApplySingleRadiodTests(unittest.TestCase):
    def setUp(self):
        self.smd = _load_smd()
        self.etc = Path(tempfile.mkdtemp())
        (self.etc / "psk-recorder").mkdir()
        self.cfg = self.etc / "psk-recorder" / "AI6VN-SE.toml"
        self.cfg.write_text(PSK)
        self.cfg.chmod(0o640)

    def test_renders_the_selected_radiod(self):
        changed, msg = self.smd._apply_single_radiod_to_instance(
            "psk-recorder", "AI6VN-SE", ["radiod:se-status.local"], etc_root=self.etc)
        self.assertTrue(changed)
        self.assertIn("se-status.local", msg)
        self.assertIn('status = "se-status.local"', self.cfg.read_text())
        self.assertEqual(self.cfg.stat().st_mode & 0o7777, 0o640)

    def test_dry_run_changes_nothing(self):
        changed, msg = self.smd._apply_single_radiod_to_instance(
            "psk-recorder", "AI6VN-SE", ["radiod:se-status.local"],
            dry_run=True, etc_root=self.etc)
        self.assertTrue(changed)
        self.assertIn("dry-run", msg)
        self.assertIn("omni-status.local", self.cfg.read_text())

    def test_second_run_reports_already_on(self):
        self.smd._apply_single_radiod_to_instance(
            "psk-recorder", "AI6VN-SE", ["radiod:se-status.local"], etc_root=self.etc)
        changed, msg = self.smd._apply_single_radiod_to_instance(
            "psk-recorder", "AI6VN-SE", ["radiod:se-status.local"], etc_root=self.etc)
        self.assertFalse(changed)
        self.assertIn("already on se-status.local", msg)

    def test_two_radiods_or_a_kiwi_are_deferred(self):
        for sel in (["radiod:a-status.local", "radiod:b-status.local"],
                    ["kiwisdr:10.20.23.73:8073"]):
            changed, msg = self.smd._apply_single_radiod_to_instance(
                "psk-recorder", "AI6VN-SE", sel, etc_root=self.etc)
            self.assertFalse(changed)
            self.assertIn("deferred", msg)
        self.assertIn("omni-status.local", self.cfg.read_text())

    def test_missing_config_says_run_instance_add(self):
        changed, msg = self.smd._apply_single_radiod_to_instance(
            "psk-recorder", "NOPE", ["radiod:se-status.local"], etc_root=self.etc)
        self.assertFalse(changed)
        self.assertIn("instance add psk-recorder NOPE", msg)


if __name__ == "__main__":
    unittest.main()
