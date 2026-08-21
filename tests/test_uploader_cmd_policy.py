"""`smd admin uploader manifest` names what the uploads policy suppresses
(sigmond#53)."""
import contextlib
import io
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from sigmond.commands import uploader as up_cmd
from sigmond import uploader_manifest as um


class SuppressedPipelinesLineTests(unittest.TestCase):
    def _check(self, suppressed):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        path = Path(d.name) / "pipelines.toml"
        path.write_text("[[pipeline]]\nname = \"heartbeat\"\n")
        out = io.StringIO()
        with mock.patch.object(um, "generate",
                               return_value="[[pipeline]]\nname = \"heartbeat\"\n"), \
             mock.patch.object(um, "suppressed_pipelines", return_value=suppressed), \
             mock.patch.object(um, "MANIFEST_PATH", path), \
             contextlib.redirect_stdout(out):
            rc = up_cmd.cmd_uploader_manifest(types.SimpleNamespace(write=False))
        return rc, out.getvalue()

    def test_names_suppressed_pipelines(self):
        rc, out = self._check(["wspr-wsprdaemon", "psk-pskreporter"])
        self.assertEqual(rc, 0)
        self.assertIn("DISABLED BY POLICY", out)
        self.assertIn("wspr-wsprdaemon, psk-pskreporter", out)

    def test_silent_when_policy_enabled(self):
        rc, out = self._check([])
        self.assertEqual(rc, 0)
        self.assertNotIn("POLICY", out)
