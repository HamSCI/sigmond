"""`smd admin uploader manifest` names what the uploads policy suppresses
(sigmond#53)."""
import contextlib
import io
import subprocess
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
              render=None, calls=None, migrate=0, active=False):
        """Run the command with the daemon install, the render, the
        send-record migration and systemd faked.  ``install`` is what the
        install returns, or an exception it raises.  ``render`` replaces the
        rendered text.  ``migrate`` is what ``_run_migrate`` returns, and
        ``active`` whether the daemon already runs.  ``calls`` records
        "install", "generate", "migrate" and each systemctl verb, such as
        "systemctl enable", in the order they ran."""
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        path = Path(d.name) / "pipelines.toml"
        calls = [] if calls is None else calls
        seen = {}

        def fake_install():
            calls.append("install")
            if isinstance(install, BaseException):
                raise install
            return install

        def fake_generate():
            calls.append("generate")
            return render() if render else self.TEXT

        def fake_migrate():
            calls.append("migrate")
            seen["manifest"] = path.read_text() if path.exists() else None
            return migrate

        def fake_run(cmd):
            calls.append(" ".join(cmd[:2]))
            return 0

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(um, "generate", side_effect=fake_generate), \
             mock.patch.object(um, "suppressed_pipelines", return_value=[]), \
             mock.patch.object(um, "MANIFEST_PATH", path), \
             mock.patch.object(up_cmd.os, "geteuid", return_value=euid), \
             mock.patch.object(up_cmd, "_ensure_daemon_installed",
                               side_effect=fake_install), \
             mock.patch.object(up_cmd, "_run_migrate", side_effect=fake_migrate), \
             mock.patch.object(up_cmd, "_service_active", return_value=active), \
             mock.patch.object(up_cmd, "_run", side_effect=fake_run) as run, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = up_cmd.cmd_uploader_manifest(
                types.SimpleNamespace(write=write, enable=enable))
        return types.SimpleNamespace(
            rc=rc, calls=calls, path=path, run=run,
            manifest_at_migrate=seen.get("manifest", "never migrated"),
            out=out.getvalue(), err=err.getvalue())

    def test_root_enable_installs_before_generate(self):
        r = self._call()
        self.assertEqual(r.calls, ["install", "generate", "migrate",
                                   "systemctl enable"])
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
        self.assertEqual(r.calls, ["generate", "migrate"])
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

    # -- D10: `hs-uploader migrate` runs after the write, before any start --

    def test_migrate_runs_on_the_written_manifest_before_systemctl(self):
        r = self._call(active=True)
        self.assertEqual(r.calls, ["install", "generate", "migrate",
                                   "systemctl enable", "systemctl restart"])
        self.assertEqual(r.manifest_at_migrate, self.TEXT)
        self.assertEqual(r.rc, 0)

    def test_a_failed_migrate_starts_and_restarts_nothing(self):
        r = self._call(active=True, migrate=1)
        self.assertEqual(r.rc, 1)
        self.assertEqual(r.calls, ["install", "generate", "migrate"])
        r.run.assert_not_called()
        self.assertEqual(r.path.read_text(), self.TEXT)
        self.assertIn("neither started nor restarted", r.err)

    def test_write_alone_migrates_and_fails_when_migrate_fails(self):
        # smd align runs `--write` alone and restarts hs-uploader itself,
        # only when this step exits 0 (bin/smd, _align_apply).
        r = self._call(enable=False, migrate=1)
        self.assertEqual(r.calls, ["generate", "migrate"])
        self.assertEqual(r.rc, 1)
        r.run.assert_not_called()


class RunMigrateTests(unittest.TestCase):
    """`_run_migrate` runs the daemon's own `hs-uploader migrate` as hsupload
    (tasks/plan-sink-control.md D10).  subprocess.run is faked; nothing runs."""

    INVALID = ("usage: hs-uploader [-h] [--state STATE]\n"
               "                   {status,peek,reset-cursor,kick,serve} ...\n"
               "hs-uploader: error: argument cmd: invalid choice: 'migrate' "
               "(choose from 'status', 'peek', 'reset-cursor', 'kick', 'serve')\n")

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.venv = Path(d.name) / "venv"
        self.exe = self.venv / "bin" / "hs-uploader"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_text("#!/bin/sh\n")

    def _migrate(self, result=(0, "", ""), *, exc=None):
        """Run _run_migrate against the fake venv.  ``result`` is the faked
        (returncode, stdout, stderr); ``exc`` an exception to raise instead."""
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            if exc is not None:
                raise exc
            rc, so, se = result
            return subprocess.CompletedProcess(cmd, rc, so, se)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(up_cmd, "_VENV", self.venv), \
             mock.patch.object(up_cmd.subprocess, "run",
                               side_effect=fake_run) as run, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = up_cmd._run_migrate()
        return types.SimpleNamespace(rc=rc, run=run, cmd=seen.get("cmd"),
                                     kw=seen.get("kw"), out=out.getvalue(),
                                     err=err.getvalue())

    def test_runs_the_daemons_hs_uploader_as_hsupload(self):
        r = self._migrate((0, "watermarks.db: version 1\n"
                              "  applied 1: record the schema version; "
                              "changes no row and no table\n", ""))
        self.assertEqual(r.rc, 0)
        # Intent, not the bare name: runuser lives in /usr/sbin, off an
        # operator's PATH (AC0G-ND 2026-09-03).
        self.assertTrue(r.cmd[0].endswith("runuser"), r.cmd)
        self.assertEqual(r.cmd[1:], ["-u", "hsupload", "--", str(self.exe),
                                     "migrate"])
        self.assertGreater(r.kw["timeout"], 30)   # past migrate's busy timeout
        self.assertEqual(r.kw["cwd"], str(self.venv.parent))
        self.assertIn("uploader: watermarks.db: version 1", r.out)
        self.assertIn("uploader:   applied 1: record the schema version", r.out)

    def test_an_hs_uploader_without_migrate_counts_as_skipped(self):
        r = self._migrate((2, "", self.INVALID))
        self.assertEqual(r.rc, 0)
        self.assertIn("predates `migrate`", r.out)
        self.assertEqual(r.err, "")

    def test_a_usage_error_from_migrate_itself_fails(self):
        r = self._migrate((2, "", "usage: hs-uploader migrate [-h] [--db PATH] "
                                  "[--check]\nhs-uploader migrate: error: "
                                  "unrecognized arguments: --bogus\n"))
        self.assertEqual(r.rc, 1)
        self.assertIn("unrecognized arguments", r.err)

    def test_a_failed_migrate_returns_1_and_says_why(self):
        r = self._migrate((1, "", "hs-uploader migrate: /var/lib/hs-uploader/"
                                  "watermarks.db: OperationalError: database "
                                  "is locked\n"))
        self.assertEqual(r.rc, 1)
        self.assertIn("exit 1", r.err)
        self.assertIn("database is locked", r.err)

    def test_a_hung_migrate_returns_1(self):
        r = self._migrate(exc=subprocess.TimeoutExpired(["runuser"], 120))
        self.assertEqual(r.rc, 1)
        self.assertIn("did not finish", r.err)

    def test_a_runuser_that_cannot_start_returns_1(self):
        r = self._migrate(exc=FileNotFoundError(2, "No such file or directory",
                                                "/usr/sbin/runuser"))
        self.assertEqual(r.rc, 1)
        self.assertIn("could not run", r.err)

    def test_no_daemon_venv_skips_without_running_anything(self):
        self.exe.unlink()
        r = self._migrate()
        self.assertEqual(r.rc, 0)
        r.run.assert_not_called()
        self.assertIn("migrate skipped", r.out)
