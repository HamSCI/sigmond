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
    def _check(self, suppressed, mode="hold"):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        path = Path(d.name) / "pipelines.toml"
        path.write_text("[[pipeline]]\nname = \"heartbeat\"\n")
        out = io.StringIO()
        with mock.patch.object(um, "generate",
                               return_value="[[pipeline]]\nname = \"heartbeat\"\n"), \
             mock.patch.object(um, "suppressed_pipelines", return_value=suppressed), \
             mock.patch.object(um, "MANIFEST_PATH", path), \
             mock.patch.object(um, "effective_mode", return_value=mode), \
             contextlib.redirect_stdout(out):
            rc = up_cmd.cmd_uploader_manifest(types.SimpleNamespace(write=False))
        return rc, out.getvalue()

    def test_names_suppressed_pipelines(self):
        rc, out = self._check(["wspr-wsprdaemon", "psk-pskreporter"])
        self.assertEqual(rc, 0)
        self.assertIn("SITE SINK HOLD (LEGACY)", out)
        self.assertIn("wspr-wsprdaemon, psk-pskreporter", out)

    def test_discard_is_not_reported_as_held(self):
        # Seen on B4 2026-10-01: discard rendered every pipeline, yet this
        # line said "DISABLED BY POLICY ... suppressed", the hold wording.
        rc, out = self._check(["wspr-wsprdaemon"], mode="discard")
        self.assertEqual(rc, 0)
        self.assertIn("SITE SINK OFF", out)
        self.assertNotIn("HOLD", out)

    def test_silent_when_policy_enabled(self):
        rc, out = self._check([])
        self.assertEqual(rc, 0)
        self.assertNotIn("SITE SINK", out)




class EnableInstallsDaemonFirstTests(unittest.TestCase):
    """`--enable` installs the daemon BEFORE it renders the manifest.

    Found in the v3.69 pre-tag review.  A fresh install whose first bring-up
    saw no SDR wrote its first manifest from Stage 4 `--write --enable`.
    The render probed /opt/hs-uploader/venv for discard support before
    install.sh had made that venv.  So the site sink switch `off` rendered
    as the legacy hold."""

    TEXT = "[[pipeline]]\nname = \"heartbeat\"\n"

    def _call(self, *, enable=True, write=True, euid=0, install=True,
              render=None, calls=None):
        """Run the command with the daemon install, the render and systemd
        faked.  ``install`` is what the install returns, or an exception it
        raises.  ``render`` replaces the rendered text.  ``calls`` records
        "install" and "generate" in the order they ran."""
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        path = Path(d.name) / "pipelines.toml"
        calls = [] if calls is None else calls

        def fake_install():
            calls.append("install")
            if isinstance(install, BaseException):
                raise install
            return install

        def fake_generate():
            calls.append("generate")
            return render() if render else self.TEXT

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(um, "generate", side_effect=fake_generate), \
             mock.patch.object(um, "suppressed_pipelines", return_value=[]), \
             mock.patch.object(um, "MANIFEST_PATH", path), \
             mock.patch.object(up_cmd.os, "geteuid", return_value=euid), \
             mock.patch.object(up_cmd, "_ensure_daemon_installed",
                               side_effect=fake_install), \
             mock.patch.object(up_cmd, "_service_active", return_value=False), \
             mock.patch.object(up_cmd, "_run", return_value=0) as run, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = up_cmd.cmd_uploader_manifest(
                types.SimpleNamespace(write=write, enable=enable))
        return types.SimpleNamespace(
            rc=rc, calls=calls, path=path, run=run,
            out=out.getvalue(), err=err.getvalue())

    def test_root_enable_installs_before_generate(self):
        r = self._call()
        self.assertEqual(r.calls, ["install", "generate"])
        self.assertEqual(r.rc, 0)
        self.assertEqual(r.path.read_text(), self.TEXT)
        r.run.assert_any_call(["systemctl", "enable", "--now", up_cmd.SERVICE])

    def test_fresh_host_off_renders_discard_not_hold(self):
        # The symptom, through the real effective_mode: the probe finds the
        # venv only after the install has run.
        calls = []
        coord = types.SimpleNamespace(
            uploads=types.SimpleNamespace(mode="discard"))
        with mock.patch.object(um, "hs_uploader_supports_discard",
                               side_effect=lambda *a, **k: "install" in calls):
            r = self._call(calls=calls, render=lambda: (
                f"# mode = {um.effective_mode(coord)}\n"))
        self.assertEqual(r.rc, 0)
        self.assertEqual(r.path.read_text(), "# mode = discard\n")

    def test_write_without_enable_never_installs(self):
        r = self._call(enable=False)
        self.assertEqual(r.calls, ["generate"])
        self.assertEqual(r.rc, 0)
        self.assertEqual(r.path.read_text(), self.TEXT)
        r.run.assert_not_called()

    def test_check_never_installs(self):
        r = self._call(enable=False, write=False)
        self.assertEqual(r.calls, ["generate"])
        self.assertFalse(r.path.exists())

    def test_non_root_enable_refuses_without_installing(self):
        r = self._call(euid=1000)
        self.assertEqual(r.calls, ["generate"])
        self.assertEqual(r.rc, 1)
        self.assertIn("requires root", r.err)
        self.assertFalse(r.path.exists())
        r.run.assert_not_called()

    def test_failed_install_still_writes_and_returns_1(self):
        r = self._call(install=False)
        self.assertEqual(r.calls, ["install", "generate"])
        self.assertEqual(r.rc, 1)
        self.assertEqual(r.path.read_text(), self.TEXT)
        r.run.assert_not_called()  # no systemctl enable on a failed install

    def test_install_error_still_writes_and_returns_1(self):
        r = self._call(install=OSError("no bash"))
        self.assertEqual(r.calls, ["install", "generate"])
        self.assertEqual(r.rc, 1)
        self.assertEqual(r.path.read_text(), self.TEXT)
        self.assertIn("install failed: no bash", r.err)
        r.run.assert_not_called()
