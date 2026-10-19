"""`smd admin uploader manifest`, reordered (tasks/spec-key-split-v373.md §5.2).

The step probes the installed hs-uploader, renders to ``pipelines.toml.new``,
asks ``migrate --check`` whether a migration waits, stops the daemon, runs
the migration, and only then lands the manifest and returns the daemon to
the state it found (or starts it, under ``--enable``).  A failure at any
point before the landing leaves the old manifest beside the old keys.

Every systemctl call and every migrate is faked; nothing here touches a
service.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond.commands import uploader as up_cmd
from sigmond import uploader_manifest as um

REPO = Path(__file__).resolve().parent.parent

OLD = '[[pipeline]]\nname = "psk-pskreporter"\n'
SPLIT = ('[[pipeline]]\nname = "psk-recorder-pskreporter"\n\n'
         '[[pipeline]]\nname = "meteor-scatter-pskreporter"\n')
MS_ONLY = '[[pipeline]]\nname = "meteor-scatter-pskreporter"\n'
DB = Path("/srv/state/watermarks.db")


class _Step(unittest.TestCase):
    """Run the command with the probe, the render, migrate and systemd faked.

    ``calls`` records, in order: "probe", "generate", "check", "migrate"
    and each systemctl verb ("systemctl stop", ...).  The fake daemon
    follows the systemctl calls, so ``final_active`` says whether it runs
    when the command returns."""

    def step(self, *, enable=False, installed=OLD, render=SPLIT, probe=2,
             pending=False, check_rc=None, migrate_rc=0, active=True,
             mode="upload", stop_rc=0):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        path = Path(d.name) / "pipelines.toml"
        if installed is not None:
            path.write_text(installed)
        calls, seen = [], {"migrate_kw": [], "generate_sv": [],
                           "suppressed_sv": []}
        state = {"active": active}

        def fake_probe(*a, **k):
            calls.append("probe")
            return probe

        def fake_generate(*a, store_version=None, **k):
            calls.append("generate")
            seen["generate_sv"].append(store_version)
            if store_version is None and mode in ("upload", "discard"):
                raise AssertionError("generate would have probed again")
            return render

        def fake_suppressed(*a, store_version=None, **k):
            seen["suppressed_sv"].append(store_version)
            return []

        def fake_migrate(*, db, sink, keep_retry_for, check=False):
            seen["migrate_kw"].append(dict(db=db, sink=sink,
                                           keep_retry_for=keep_retry_for,
                                           check=check))
            if check:
                calls.append("check")
                if check_rc is not None:
                    return check_rc
                return 3 if pending else 0
            calls.append("migrate")
            seen["manifest_at_migrate"] = (path.read_text()
                                           if path.exists() else None)
            seen["active_at_migrate"] = state["active"]
            return migrate_rc

        def fake_run(cmd):
            verb = " ".join(cmd[:2])
            calls.append(verb)
            if verb == "systemctl stop":
                if stop_rc:
                    return stop_rc
                state["active"] = False
            elif verb in ("systemctl start", "systemctl restart") or (
                    verb == "systemctl enable" and "--now" in cmd):
                state["active"] = True
            return 0

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(um, "hs_uploader_store_version",
                               side_effect=fake_probe), \
             mock.patch.object(um, "generate", side_effect=fake_generate), \
             mock.patch.object(um, "suppressed_pipelines",
                               side_effect=fake_suppressed), \
             mock.patch.object(um, "MANIFEST_PATH", path), \
             mock.patch.object(up_cmd, "_effective_mode", return_value=mode), \
             mock.patch.object(up_cmd.os, "geteuid", return_value=0), \
             mock.patch.object(up_cmd, "_ensure_daemon_installed",
                               return_value=True), \
             mock.patch.object(up_cmd, "_run_migrate",
                               side_effect=fake_migrate), \
             mock.patch.object(up_cmd, "_service_active",
                               side_effect=lambda: state["active"]), \
             mock.patch.object(up_cmd, "_started_before_manifest",
                               return_value=False), \
             mock.patch.object(up_cmd, "_daemon_store_path", return_value=DB), \
             mock.patch.object(up_cmd, "_run", side_effect=fake_run), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = up_cmd.cmd_uploader_manifest(
                types.SimpleNamespace(write=True, enable=enable))
        side = path.with_name(path.name + ".new")
        bak = path.with_name(path.name + ".bak")
        return types.SimpleNamespace(
            rc=rc, calls=calls, path=path, side=side, bak=bak,
            manifest=path.read_text() if path.exists() else None,
            final_active=state["active"], out=out.getvalue(),
            err=err.getvalue(), **seen)


class OrderTests(_Step):

    def test_order_stop_migrate_move_start(self):
        r = self.step(active=True)
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.calls, ["probe", "generate", "check",
                                   "systemctl stop", "migrate",
                                   "systemctl reset-failed",
                                   "systemctl start"])
        # The manifest lands only after migrate exits 0, and migrate runs
        # with the daemon stopped.
        self.assertEqual(r.manifest_at_migrate, OLD)
        self.assertFalse(r.active_at_migrate)
        self.assertEqual(r.manifest, SPLIT)
        self.assertEqual(r.bak.read_text(), OLD)
        self.assertFalse(r.side.exists())
        self.assertTrue(r.final_active)
        # One probe, its answer handed to both the render and the list.
        self.assertEqual(r.generate_sv, [2])
        self.assertEqual(r.suppressed_sv, [2])

    def test_enable_starts_a_stopped_daemon_after_landing(self):
        r = self.step(enable=True, active=False)
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.calls, ["probe", "generate", "check",
                                   "systemctl stop", "migrate",
                                   "systemctl reset-failed",
                                   "systemctl enable"])
        self.assertEqual(r.manifest, SPLIT)
        self.assertTrue(r.final_active)

    def test_write_leaves_a_stopped_daemon_stopped(self):
        r = self.step(active=False)
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.manifest, SPLIT)
        self.assertFalse(r.final_active)
        self.assertNotIn("systemctl start", r.calls)

    def test_migrate_gets_the_daemons_store_and_the_sink(self):
        r = self.step()
        check, run = r.migrate_kw
        self.assertEqual(run, dict(db=DB, sink=Path(um.SINK_PATH),
                                   keep_retry_for="psk-recorder-pskreporter",
                                   check=False))
        # The check asks the same question migrate will answer.
        self.assertEqual(check, dict(run, check=True))

    def test_a_stop_that_fails_migrates_nothing(self):
        r = self.step(active=True, stop_rc=1)
        self.assertEqual(r.rc, 1)
        self.assertNotIn("migrate", r.calls)
        self.assertEqual(r.manifest, OLD)
        self.assertFalse(r.side.exists())
        self.assertTrue(r.final_active)


class FailedMigrateTests(_Step):

    def test_failed_migrate_keeps_old_manifest_and_daemon_state(self):
        for enable in (False, True):
            for active in (True, False):
                with self.subTest(enable=enable, active=active):
                    r = self.step(enable=enable, active=active, migrate_rc=1)
                    self.assertEqual(r.rc, 1)
                    self.assertEqual(r.manifest, OLD)
                    self.assertFalse(r.side.exists())
                    self.assertFalse(r.bak.exists())
                    # As found: running stays running, stopped stays
                    # stopped, --enable or not.
                    self.assertEqual(r.final_active, active)
                    self.assertIn("migrate failed", r.err)

    def test_a_failed_check_stops_nothing(self):
        r = self.step(check_rc=1, active=True)
        self.assertEqual(r.rc, 1)
        self.assertNotIn("systemctl stop", r.calls)
        self.assertNotIn("migrate", r.calls)
        self.assertEqual(r.manifest, OLD)
        self.assertFalse(r.side.exists())


class UnchangedManifestTests(_Step):

    def test_unchanged_manifest_with_pending_migration_still_migrates(self):
        # Review B1: new hs-uploader code over a manifest that did not
        # change is the common upgrade, and its store still needs migrating.
        r = self.step(installed=SPLIT, render=SPLIT, pending=True, active=True)
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.calls, ["probe", "generate", "check",
                                   "systemctl stop", "migrate",
                                   "systemctl reset-failed",
                                   "systemctl start"])
        self.assertEqual(r.manifest, SPLIT)
        self.assertTrue(r.final_active)

    def test_unchanged_and_nothing_pending_returns_without_stop(self):
        r = self.step(installed=SPLIT, render=SPLIT, pending=False)
        self.assertEqual(r.rc, 0, r.err)
        self.assertEqual(r.calls, ["probe", "generate", "check"])
        self.assertFalse(r.side.exists())
        self.assertIn("already current", r.out)
        self.assertTrue(r.final_active)

    def test_unchanged_under_enable_still_makes_sure_the_daemon_runs(self):
        r = self.step(enable=True, installed=SPLIT, render=SPLIT,
                      active=False)
        self.assertEqual(r.rc, 0, r.err)
        self.assertNotIn("systemctl stop", r.calls)
        self.assertNotIn("migrate", r.calls)
        self.assertEqual(r.calls[-2:], ["systemctl reset-failed",
                                        "systemctl enable"])
        self.assertTrue(r.final_active)


class ProbeTests(_Step):

    def test_probe_failure_leaves_manifest_and_exits_nonzero(self):
        for enable in (False, True):
            with self.subTest(enable=enable):
                r = self.step(enable=enable, probe=None, active=True)
                self.assertNotEqual(r.rc, 0)
                self.assertEqual(r.calls, ["probe"])
                self.assertEqual(r.manifest, OLD)
                self.assertFalse(r.side.exists())
                self.assertTrue(r.final_active)
                self.assertIn("capabilities", r.err)

    def test_hold_renders_without_a_probe(self):
        hb = '[[pipeline]]\nname = "heartbeat"\n'
        r = self.step(mode="hold", probe=None, render=hb, installed=hb)
        self.assertEqual(r.rc, 0, r.err)
        self.assertNotIn("probe", r.calls)
        # Without a probe nothing says the daemon knows --sink.
        for kw in r.migrate_kw:
            self.assertIsNone(kw["sink"])
            self.assertIsNone(kw["keep_retry_for"])


class KeepRetryForTests(_Step):

    def test_keep_retry_for_names_a_rendered_pipeline(self):
        for render, probe, want in [
                (SPLIT, 2, "psk-recorder-pskreporter"),
                (MS_ONLY, 2, "meteor-scatter-pskreporter"),
                ('[[pipeline]]\nname = "psk-recorder-pskreporter"\n', 2,
                 "psk-recorder-pskreporter")]:
            with self.subTest(render=render):
                r = self.step(render=render, probe=probe)
                self.assertEqual(r.rc, 0, r.err)
                self.assertEqual({kw["keep_retry_for"] for kw in r.migrate_kw},
                                 {want})

    def test_no_split_rendered_names_no_pipeline(self):
        # An hs-uploader that lags sigmond renders the shared pipeline, and
        # its migrate knows neither --keep-retry-for nor --sink.
        r = self.step(render=OLD, installed=None, probe=1)
        self.assertEqual(r.rc, 0, r.err)
        for kw in r.migrate_kw:
            self.assertIsNone(kw["keep_retry_for"])
            self.assertIsNone(kw["sink"])

    def test_a_split_with_neither_pskreporter_pipeline_names_none(self):
        grape = '[[pipeline]]\nname = "grape-psws"\n'
        r = self.step(render=grape, probe=2)
        for kw in r.migrate_kw:
            self.assertIsNone(kw["keep_retry_for"])
            self.assertEqual(kw["sink"], Path(um.SINK_PATH))


class RunMigrateArgvTests(unittest.TestCase):
    """`_run_migrate` builds the daemon's own command and maps --check."""

    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.venv = Path(d.name) / "venv"
        self.exe = self.venv / "bin" / "hs-uploader"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_text("#!/bin/sh\n")

    def _migrate(self, result=(0, "", ""), **kw):
        seen = {}

        def fake_group(cmd, **k):
            seen["cmd"], seen["kw"] = cmd, k
            rc, so, se = result
            return subprocess.CompletedProcess(cmd, rc, so, se)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(up_cmd, "_VENV", self.venv), \
             mock.patch.object(up_cmd.shutil, "which", return_value="/x/runuser"), \
             mock.patch.object(up_cmd, "_run_in_group", side_effect=fake_group), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = up_cmd._run_migrate(**kw)
        return types.SimpleNamespace(rc=rc, cmd=seen.get("cmd"),
                                     out=out.getvalue(), err=err.getvalue())

    def test_argv_carries_db_sink_and_keep_retry_for(self):
        r = self._migrate(db=DB, sink=Path("/s/sink.db"),
                          keep_retry_for="meteor-scatter-pskreporter")
        self.assertEqual(r.cmd, ["/x/runuser", "-u", "hsupload", "--",
                                 str(self.exe), "migrate", "--db", str(DB),
                                 "--sink", "/s/sink.db", "--keep-retry-for",
                                 "meteor-scatter-pskreporter"])
        self.assertEqual(r.rc, 0)

    def test_argv_leaves_out_what_is_none_and_adds_check(self):
        r = self._migrate(db=DB, sink=None, keep_retry_for=None, check=True)
        self.assertEqual(r.cmd[4:], [str(self.exe), "migrate", "--db",
                                     str(DB), "--check"])

    def test_check_maps_pending_to_3_and_idle_to_0(self):
        kw = dict(db=DB, sink=None, keep_retry_for=None, check=True)
        for stdout, want in [
                ("watermarks.db: version 2\n  nothing to do\n", 0),
                ("watermarks.db: not found at /x; nothing to migrate\n", 0),
                ("watermarks.db: version 1\n  pending 2: split the "
                 "pskreporter send record\n", 3),
                ("watermarks.db: version 2\n  retry 7 (psk-pskreporter, "
                 "cursor_after 9): would move to psk-recorder-pskreporter "
                 "under x; y\n", 3),
                ("watermarks.db: version 2\n  retry 7 (psk-pskreporter, "
                 "cursor_after 9): would delete; the sink holds it\n", 3),
                ("watermarks.db: version 2\n  retry 7 (psk-pskreporter, "
                 "cursor_after 9): stays under psk-pskreporter; z, and no "
                 "--keep-retry-for names a pipeline to move it to\n", 0),
                ("watermarks.db: a crash left a journal to recover; run "
                 "hs-uploader migrate without --check\n", 3)]:
            with self.subTest(stdout=stdout):
                self.assertEqual(self._migrate((0, stdout, ""), **kw).rc, want)

    def test_a_failed_check_returns_1(self):
        r = self._migrate((1, "", "hs-uploader migrate: boom\n"), db=DB,
                          sink=None, keep_retry_for=None, check=True)
        self.assertEqual(r.rc, 1)
        self.assertIn("boom", r.err)


class ProcessGroupTests(unittest.TestCase):

    def test_migrate_timeout_kills_process_group(self):
        proc = mock.Mock(pid=4242, returncode=-9)
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired(["runuser"], 1), ("", "")]
        with mock.patch.object(up_cmd.subprocess, "Popen",
                               return_value=proc) as popen, \
             mock.patch.object(up_cmd.os, "killpg") as killpg:
            with self.assertRaises(subprocess.TimeoutExpired):
                up_cmd._run_in_group(["runuser"], cwd="/", timeout=1)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        killpg.assert_called_once_with(4242, signal.SIGKILL)

    def test_a_grandchild_dies_with_the_group(self):
        # Through the real Popen: a stand-in runuser that forks (as the real
        # one does, to close the PAM session) and a migrate that spawns a
        # sleeper.  Killing only runuser would orphan the sleeper.
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        root = Path(d.name)
        pidfile = root / "pid"
        exe = root / "venv" / "bin" / "hs-uploader"
        exe.parent.mkdir(parents=True)
        exe.write_text(f"#!/bin/sh\nsleep 30 &\necho $! > {pidfile}\nwait\n")
        exe.chmod(0o755)
        runuser = root / "runuser"
        runuser.write_text('#!/bin/sh\nshift 3\n"$@"\nexit $?\n')
        runuser.chmod(0o755)
        err = io.StringIO()
        with mock.patch.object(up_cmd, "_VENV", root / "venv"), \
             mock.patch.object(up_cmd.shutil, "which", return_value=str(runuser)), \
             mock.patch.object(up_cmd, "_MIGRATE_TIMEOUT_S", 1.0), \
             contextlib.redirect_stderr(err), \
             contextlib.redirect_stdout(io.StringIO()):
            t0 = time.monotonic()
            rc = up_cmd._run_migrate(db=DB, sink=None, keep_retry_for=None)
            took = time.monotonic() - t0
        self.assertEqual(rc, 1)
        # A survivor holds the output pipes, so killing runuser alone would
        # leave smd waiting out the whole 30 s sleep.
        self.assertLess(took, 10, "the call waited on a surviving child")
        self.assertIn("did not finish", err.getvalue())
        pid = int(pidfile.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.05)
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)
            self.fail("the sleeper outlived the timeout: only runuser died")


def _alive(pid: int) -> bool:
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return False
    return "\nState:\tZ" not in status


class GenerateHoldNoProbeTests(unittest.TestCase):
    """Ruling 9: a render that holds every client pipeline needs no probe,
    so a failed probe cannot block it."""

    def test_hold_generate_never_probes(self):
        from sigmond.coordination import Coordination, Host, Uploads

        class _Topo:
            def enabled_components(self, only=None):
                return []

        coord = Coordination(host=Host(call="AC0G", grid="EM38ww"),
                             uploads=Uploads(enabled=False))
        with mock.patch.object(um, "hs_uploader_store_version",
                               side_effect=AssertionError("probed")), \
             mock.patch.object(um, "host_key_file", return_value="/k"):
            text = um.generate(_Topo(), coord)
        self.assertIn("SITE SINK HOLD", text)


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_manifest_step", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_manifest_step",
                                           loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class ApplyTests(unittest.TestCase):

    def test_apply_reports_manifest_step_failure(self):
        smd = _load_smd()
        for rc, want in [(1, False), (0, True)]:
            with self.subTest(rc=rc):
                out = io.StringIO()
                with mock.patch.object(up_cmd, "cmd_uploader_manifest",
                                       return_value=rc) as step, \
                     contextlib.redirect_stdout(out), \
                     contextlib.redirect_stderr(out):
                    self.assertIs(smd._apply_uploader_manifest(), want)
                args = step.call_args.args[0]
                self.assertTrue(args.write and args.enable)
                if not want:
                    self.assertIn("uploader manifest", out.getvalue())
                    self.assertIn("exit 1", out.getvalue())

    def test_apply_folds_the_step_into_its_result(self):
        # cmd_apply is too wide to drive whole; this pins the one line that
        # turns the helper's answer into apply's exit.
        import inspect
        src = inspect.getsource(_load_smd().cmd_apply)
        self.assertIn("if not _apply_uploader_manifest():\n            ok = False",
                      src)

    def test_bringup_never_starts_a_daemon_the_step_left_stopped(self):
        argvs = [s.argv for s in _bringup_plan().steps]
        self.assertIn(["systemctl", "try-restart", "hs-uploader.service"],
                      argvs)
        self.assertNotIn(["systemctl", "restart", "hs-uploader.service"],
                         argvs)


def _bringup_plan():
    sys.path.insert(0, str(REPO / "tests"))
    from test_bringup_no_sdr import _dasi2
    from sigmond.bringup import build_plan
    return build_plan(_dasi2(), local_radiod=True)


if __name__ == "__main__":
    unittest.main()
