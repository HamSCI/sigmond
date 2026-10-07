"""`smd admin uploader manifest` names what the uploads policy suppresses
(sigmond#53)."""
import contextlib
import io
import os
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
              render=None, calls=None, migrate=0, active=False,
              installed=None, path=None, started="after"):
        """Run the command with the daemon install, the render, the
        send-record migration and systemd faked.  ``install`` is what the
        install returns, or an exception it raises.  ``render`` replaces the
        rendered text.  ``migrate`` is what ``_run_migrate`` returns, and
        ``active`` whether the daemon already runs.  ``installed`` is the
        manifest already on disk before the call.  ``path`` names a manifest
        file that persists across calls, for a rerun.  ``started`` says when
        the running daemon began, relative to the manifest file's mtime at the
        time systemd is asked: "after", "before", or None when systemd does
        not say.  ``calls`` records "install", "generate", "migrate" and each
        systemctl verb, such as "systemctl enable", in the order they ran."""
        if path is None:
            d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
            path = Path(d.name) / "pipelines.toml"
        if installed is not None:
            path.write_text(installed)
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

        def fake_started_at():
            if started is None:
                return None
            mtime = path.stat().st_mtime
            return mtime + 100 if started == "after" else mtime - 100

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(um, "generate", side_effect=fake_generate), \
             mock.patch.object(um, "suppressed_pipelines", return_value=[]), \
             mock.patch.object(um, "MANIFEST_PATH", path), \
             mock.patch.object(up_cmd.os, "geteuid", return_value=euid), \
             mock.patch.object(up_cmd, "_ensure_daemon_installed",
                               side_effect=fake_install), \
             mock.patch.object(up_cmd, "_run_migrate", side_effect=fake_migrate), \
             mock.patch.object(up_cmd, "_service_active", return_value=active), \
             mock.patch.object(up_cmd, "_service_started_at",
                               side_effect=fake_started_at), \
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

    # -- migrate runs whether or not this call changed the manifest: new
    # hs-uploader code over an unchanged manifest is the common upgrade --

    def test_migrate_runs_when_the_manifest_is_already_current(self):
        r = self._call(enable=False, installed=self.TEXT)
        self.assertEqual(r.calls, ["generate", "migrate"])
        self.assertEqual(r.rc, 0)
        self.assertIn("already current", r.out)

    def test_migrate_runs_before_enable_when_the_manifest_is_already_current(self):
        r = self._call(installed=self.TEXT, active=True)
        self.assertEqual(r.calls, ["install", "generate", "migrate",
                                   "systemctl enable"])
        self.assertEqual(r.rc, 0)

    def test_a_failed_migrate_fails_on_a_current_manifest_too(self):
        for enable in (False, True):
            with self.subTest(enable=enable):
                r = self._call(enable=enable, installed=self.TEXT, migrate=1,
                               active=True)
                self.assertEqual(r.rc, 1)
                r.run.assert_not_called()

    # -- the daemon reads its manifest once, at start.  Restart an active
    # daemon that started before the manifest file's last write, even when
    # this run changed nothing: an earlier run may have written it, failed
    # to migrate and restarted nothing --

    def test_a_rerun_after_a_failed_migrate_restarts_the_stale_daemon(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        path = Path(d.name) / "pipelines.toml"
        first = self._call(path=path, active=True, migrate=1, started="before")
        self.assertEqual(first.rc, 1)
        first.run.assert_not_called()
        self.assertEqual(path.read_text(), self.TEXT)
        second = self._call(path=path, active=True, started="before")
        self.assertEqual(second.rc, 0)
        self.assertEqual(second.calls, ["install", "generate", "migrate",
                                        "systemctl enable",
                                        "systemctl restart"])
        self.assertIn("started before the manifest", second.out)

    def test_a_current_manifest_older_than_the_daemon_start_restarts_nothing(self):
        r = self._call(installed=self.TEXT, active=True, started="after")
        self.assertEqual(r.calls, ["install", "generate", "migrate",
                                   "systemctl enable"])
        self.assertNotIn("restarting", r.out)

    def test_an_unreadable_start_time_restarts_an_active_daemon(self):
        r = self._call(installed=self.TEXT, active=True, started=None)
        self.assertEqual(r.calls, ["install", "generate", "migrate",
                                   "systemctl enable", "systemctl restart"])

    def test_an_inactive_daemon_is_started_never_restarted(self):
        r = self._call(installed=self.TEXT, active=False, started="before")
        self.assertEqual(r.calls, ["install", "generate", "migrate",
                                   "systemctl enable"])

    def test_a_changed_manifest_restarts_an_active_daemon(self):
        r = self._call(installed="[[pipeline]]\nname = \"old\"\n",
                       active=True, started="after")
        self.assertEqual(r.calls, ["install", "generate", "migrate",
                                   "systemctl enable", "systemctl restart"])
        self.assertIn("manifest changed", r.out)


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

    def _migrate(self, result=(0, "", ""), *, exc=None, which="/x/runuser"):
        """Run _run_migrate against the fake venv.  ``result`` is the faked
        (returncode, stdout, stderr); ``exc`` an exception to raise instead;
        ``which`` what the PATH lookup of runuser finds (None for nothing)."""
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            if exc is not None:
                raise exc
            rc, so, se = result
            return subprocess.CompletedProcess(cmd, rc, so, se)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(up_cmd, "_VENV", self.venv), \
             mock.patch.object(up_cmd.shutil, "which",
                               return_value=which) as found, \
             mock.patch.object(up_cmd.subprocess, "run",
                               side_effect=fake_run) as run, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = up_cmd._run_migrate()
        return types.SimpleNamespace(rc=rc, run=run, found=found,
                                     cmd=seen.get("cmd"), kw=seen.get("kw"),
                                     out=out.getvalue(), err=err.getvalue())

    def test_runs_the_daemons_hs_uploader_as_hsupload(self):
        r = self._migrate((0, "watermarks.db: version 1\n"
                              "  applied 1: record the schema version; "
                              "changes no row and no table\n", ""))
        self.assertEqual(r.rc, 0)
        self.assertEqual(r.cmd, ["/x/runuser", "-u", "hsupload", "--",
                                 str(self.exe), "migrate"])
        self.assertIn("uploader: watermarks.db: version 1", r.out)
        self.assertIn("uploader:   applied 1: record the schema version", r.out)

    def test_runuser_comes_from_the_path_or_from_usr_sbin(self):
        # runuser lives in /usr/sbin, off an operator's PATH (AC0G-ND
        # 2026-09-03): the bare name would raise FileNotFoundError there.
        r = self._migrate(which="/opt/x/runuser")
        r.found.assert_called_once_with("runuser")
        self.assertEqual(r.cmd[0], "/opt/x/runuser")
        r = self._migrate(which=None)
        self.assertEqual(r.cmd[0], "/usr/sbin/runuser")

    def test_the_subprocess_options_are_exactly_these(self):
        # Each option carries weight: capture_output + text let the old
        # hs-uploader's stderr reach the skip test, stdin=DEVNULL keeps a
        # prompt from hanging an unattended run, errors="replace" keeps a
        # stray byte from raising, and check=False leaves a non-zero exit
        # to the code that reports it.  No shell, no env.
        r = self._migrate()
        self.assertEqual(r.kw, dict(
            capture_output=True, text=True, errors="replace",
            stdin=subprocess.DEVNULL, cwd=str(self.venv.parent),
            timeout=up_cmd._MIGRATE_TIMEOUT_S, check=False))
        self.assertIs(r.kw["stdin"], subprocess.DEVNULL)

    def test_the_timeout_is_120_seconds(self):
        # Past hs-uploader migrate's own 30 s wait for a lock, and inside the
        # 240 s that smd align allows the whole manifest step.
        self.assertEqual(up_cmd._MIGRATE_TIMEOUT_S, 120)

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
        r = self._migrate((1, "watermarks.db: version 0\n",
                           "hs-uploader migrate: /var/lib/hs-uploader/"
                           "watermarks.db: OperationalError: database "
                           "is locked\n"))
        self.assertEqual(r.rc, 1)
        self.assertIn("exit 1", r.err)
        self.assertIn("database is locked", r.err)
        # What migrate printed before it failed still shows.
        self.assertIn("uploader: watermarks.db: version 0", r.out)

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


class RunMigrateRealSubprocessTests(unittest.TestCase):
    """`_run_migrate` through the real `subprocess.run`: a stand-in `runuser`
    (drops `-u hsupload --` and runs the rest) and a stub `hs-uploader` shell
    script in a temporary venv.  Only the real call shows that the pipes, the
    decoding and the timeout work together."""

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        root = Path(d.name)
        self.venv = root / "venv"
        self.exe = self.venv / "bin" / "hs-uploader"
        self.exe.parent.mkdir(parents=True)
        self.runuser = root / "runuser"
        self.runuser.write_text('#!/bin/sh\nshift 3\nexec "$@"\n')
        self.runuser.chmod(0o755)

    def _stub(self, body):
        self.exe.write_text("#!/bin/sh\n" + body)
        self.exe.chmod(0o755)

    def _migrate(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(up_cmd, "_VENV", self.venv), \
             mock.patch.object(up_cmd.shutil, "which",
                               return_value=str(self.runuser)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = up_cmd._run_migrate()
        return types.SimpleNamespace(rc=rc, out=out.getvalue(),
                                     err=err.getvalue())

    def test_a_done_migrate_echoes_its_lines_prefixed(self):
        self._stub('echo "watermarks.db: version 1"\n'
                   'echo "  applied 1: record the schema version"\n')
        r = self._migrate()
        self.assertEqual(r.rc, 0)
        self.assertEqual(r.out, "uploader: watermarks.db: version 1\n"
                                "uploader:   applied 1: record the schema "
                                "version\n")
        self.assertEqual(r.err, "")

    def test_an_old_hs_uploader_exits_2_and_counts_as_skipped(self):
        self._stub("echo \"hs-uploader: error: argument cmd: invalid choice: "
                   "'migrate' (choose from status, serve)\" >&2\nexit 2\n")
        r = self._migrate()
        self.assertEqual(r.rc, 0)
        self.assertIn("predates `migrate`", r.out)
        self.assertEqual(r.err, "")

    def test_a_failure_reports_stdout_and_stderr(self):
        self._stub('echo "watermarks.db: version 0"\n'
                   'echo "hs-uploader migrate: database is locked" >&2\n'
                   "exit 1\n")
        r = self._migrate()
        self.assertEqual(r.rc, 1)
        self.assertIn("uploader: watermarks.db: version 0", r.out)
        self.assertIn("exit 1", r.err)
        self.assertIn("database is locked", r.err)

    def test_a_byte_that_is_not_utf8_cannot_raise(self):
        self._stub("printf 'bad \\351 byte\\n' >&2\nexit 1\n")
        r = self._migrate()
        self.assertEqual(r.rc, 1)
        self.assertIn("bad", r.err)

    def test_a_migrate_that_outlives_the_timeout_returns_1(self):
        self._stub("exec sleep 30\n")
        with mock.patch.object(up_cmd, "_MIGRATE_TIMEOUT_S", 0.5):
            r = self._migrate()
        self.assertEqual(r.rc, 1)
        self.assertIn("did not finish", r.err)


class ServiceStartTimeTests(unittest.TestCase):
    """When did the running daemon start, and does that predate the
    manifest?  The answer decides a restart, so a doubt means restart."""

    def _show(self, result=(0, "@1790000000\n", ""), *, exc=None):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            if exc is not None:
                raise exc
            rc, so, se = result
            return subprocess.CompletedProcess(cmd, rc, so, se)

        with mock.patch.object(up_cmd.subprocess, "run", side_effect=fake_run):
            value = up_cmd._service_started_at()
        return value, seen

    def test_reads_activeentertimestamp_as_unix_seconds(self):
        value, seen = self._show()
        self.assertEqual(value, 1790000000.0)
        self.assertEqual(seen["cmd"], [
            "systemctl", "show", "-p", "ActiveEnterTimestamp",
            "--timestamp=unix", "--value", "hs-uploader.service"])
        self.assertIs(seen["kw"]["stdin"], subprocess.DEVNULL)
        self.assertTrue(seen["kw"]["capture_output"])
        self.assertTrue(seen["kw"]["text"])
        self.assertFalse(seen["kw"].get("shell"))
        self.assertGreater(seen["kw"]["timeout"], 0)

    def test_every_doubt_reads_as_unknown(self):
        for label, kwargs in [
                ("empty", dict(result=(0, "\n", ""))),
                ("not a time", dict(result=(0, "n/a\n", ""))),
                ("systemctl failed", dict(result=(1, "", "no such unit"))),
                ("cannot run", dict(exc=FileNotFoundError("systemctl"))),
                ("hung", dict(exc=subprocess.TimeoutExpired("systemctl", 15)))]:
            with self.subTest(label):
                self.assertIsNone(self._show(**kwargs)[0])

    def _before(self, started, mtime):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        path = Path(d.name) / "pipelines.toml"
        path.write_text("x")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        else:
            path.unlink()
        with mock.patch.object(up_cmd, "_service_started_at",
                               return_value=started):
            return up_cmd._started_before_manifest(path)

    def test_a_start_before_the_manifest_write_is_stale(self):
        self.assertTrue(self._before(started=1000.0, mtime=2000.0))

    def test_a_start_after_the_manifest_write_is_current(self):
        self.assertFalse(self._before(started=3000.0, mtime=2000.0))

    def test_an_unknown_start_or_a_missing_manifest_counts_as_stale(self):
        self.assertTrue(self._before(started=None, mtime=2000.0))
        self.assertTrue(self._before(started=3000.0, mtime=None))
