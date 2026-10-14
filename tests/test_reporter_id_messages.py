"""Text a person reads names the instance AC0G/B4 (tasks/spec-reporter-id-
v372.md §4; inventory rows S9, S14, S17, S20-S22, and the restart hint at
commands/config.py that the inventory missed)."""
import argparse
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import harmonize as H  # noqa: E402
from sigmond import instance as I  # noqa: E402
from sigmond import upload  # noqa: E402
from sigmond.commands import config as C  # noqa: E402


def _rooted(tmp):
    real = Path

    def P(*parts):
        p = real(*parts)
        s = str(p)
        if s in ("/etc", "/opt") or s.startswith(("/etc/", "/opt/")):
            return real(tmp + s)
        return p
    return P


class StubFileTests(unittest.TestCase):
    """S9: the comments sigmond writes into new files."""

    def test_sources_stub(self):
        s = I._sources_stub("wspr-recorder", "AC0G=B4")
        self.assertIn("for wspr-recorder@AC0G/B4", s)
        self.assertIn("smd admin sources add wspr-recorder@AC0G/B4 <kind>:<id>", s)
        self.assertIn("smd admin sources remove wspr-recorder@AC0G/B4 <kind>:<id>", s)
        self.assertNotIn("AC0G=B4", s)

    def test_config_stub(self):
        s = I._config_stub("wspr-recorder", "AC0G=B4")
        self.assertIn("smd admin sources add wspr-recorder@AC0G/B4 <kind>:<id>", s)
        self.assertIn("# wspr-recorder AC0G/B4` to invoke", s)
        self.assertNotIn("AC0G=B4", s)

    def test_env_stub_keeps_the_storage_path(self):
        s = I._env_stub("wspr-recorder", "AC0G=B4")
        self.assertIn("# Loaded by the wspr-recorder unit for AC0G/B4 via", s)
        self.assertIn("EnvironmentFile=-/etc/wspr-recorder/env/AC0G=B4.env", s)

    def test_migration_header(self):
        s = I._migration_config_header("wspr-recorder", "my-rx888", "AC0G=B4")
        self.assertIn("# Per-instance config for wspr-recorder@AC0G/B4", s)


class HarmonizeMessageTests(unittest.TestCase):
    """S14: the recorder-only warning."""

    def test_recorder_only_warning_uses_the_display_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "etc" / "wspr-recorder" / "env").mkdir(parents=True)
            out = "wspr-recorder@AC0G\\x3dB4.service loaded active running WSPR\n"
            with mock.patch.object(H, "Path", _rooted(tmp)), \
                 mock.patch("subprocess.run", lambda argv, **kw:
                            subprocess.CompletedProcess(argv, 0, out, "")):
                r = H.rule_wspr_decode_enabled(None)
        self.assertEqual(r.severity, "warn")
        self.assertIn("AC0G/B4", r.message)
        self.assertNotIn("\\x3d", r.message)


class ConfigCommandTests(unittest.TestCase):
    """S20, S21, S22."""

    def test_restart_location_consumers(self):
        calls = []

        def run(argv, **kw):
            calls.append(list(argv))
            out = ("psk-recorder@AC0G\\x3dB4.service loaded active running PSK\n"
                   if argv[:2] == ["systemctl", "list-units"] and argv[-1] == "psk-recorder@*"
                   else "")
            return subprocess.CompletedProcess(argv, 0, out, "")
        err = io.StringIO()
        with mock.patch("subprocess.run", run), contextlib.redirect_stderr(err):
            C._restart_location_consumers()
        self.assertIn("restarting: psk-recorder@AC0G/B4.service", err.getvalue())
        self.assertIn(["systemctl", "restart", "psk-recorder@AC0G\\x3dB4.service"], calls)

    def test_upload_status_lists_instances_in_display_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "etc" / "wspr-recorder" / "env"
            env.mkdir(parents=True)
            (env / "AC0G=S.env").write_text("WSPR_USE_HS_UPLOADER=1\n")
            out = io.StringIO()
            with mock.patch.object(C, "Path", _rooted(tmp)), \
                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = C.cmd_config_upload(argparse.Namespace(
                    client="wspr-recorder", instance=None, on=False, off=False, via=None))
        self.assertEqual(rc, 0)
        self.assertIn("AC0G/S", out.getvalue())
        self.assertNotIn("AC0G=S", out.getvalue())

    def test_upload_change_names_the_instance_and_a_working_restart(self):
        fake = (lambda c, i, on, base="/etc", delivery=None:
                (Path("/etc/wspr-recorder/env/AC0G=S.env"), "WSPR_USE_HS_UPLOADER",
                 ["wsprnet.org"], None))
        for typed in ("AC0G/S", "AC0G\\x3dS"):
            with self.subTest(typed=typed):
                err = io.StringIO()
                with mock.patch.object(upload, "apply_enable", fake), \
                     contextlib.redirect_stderr(err):
                    rc = C.cmd_config_upload(argparse.Namespace(
                        client="wspr-recorder", instance=typed, on=True, off=False, via=None))
                self.assertEqual(rc, 0)
                self.assertIn("upload for wspr-recorder@AC0G/S", err.getvalue())
                self.assertIn("sudo systemctl restart 'wspr-recorder@AC0G\\x3dS.service'",
                              err.getvalue())


if __name__ == "__main__":
    unittest.main()
