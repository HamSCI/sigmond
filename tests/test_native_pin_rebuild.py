"""2026-11-09: a native source off sigmond's pin gets its build, and the
build's git work never runs as root in a tree another user owns.

The v3.86 update test installed v3.85, where the image build had cloned
ubersdr_wsprd at 529ec5d, then rolled sigmond forward to a pin of 8719788.
`smd update --apply` left the checkout and the decoders at 529ec5d: the
planner said "the next build returns it", and nothing in the update ran
that build.  The same run refused to fetch four native checkouts (ft8_lib,
onion, ubersdr_wsprd, wsjtx) because they were root-owned: `smd install`
elevates itself, so each builder's `git clone` ran as root.

`smd admin native-build` is the step that returns a source to its pin: the
update runs it (as the smd on disk, so a freshly pulled pin applies), and
sigmond's install.sh runs it, which is how an OLDER smd that pulls a newer
sigmond still applies the newer pin in the same run.
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_native_pin", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_native_pin", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()
_REAL_RUN = subprocess.run


def _git(*args, cwd):
    return _REAL_RUN(["git", "-c", "user.email=t@example", "-c",
                      "user.name=t", *args],
                     cwd=cwd, check=True, capture_output=True, text=True)


class _SourceRig(unittest.TestCase):
    """base/ubersdr_wsprd: a real checkout with two commits; HEAD at the
    first, so the second can stand for a moved pin."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "base"
        self.src = self.base / "ubersdr_wsprd"
        self.src.mkdir(parents=True)
        _git("init", "-q", "-b", "main", cwd=self.src)
        (self.src / "a").write_text("a\n")
        _git("add", "-A", cwd=self.src)
        _git("commit", "-qm", "one", cwd=self.src)
        self.old = _git("rev-parse", "HEAD", cwd=self.src).stdout.strip()
        (self.src / "b").write_text("b\n")
        _git("add", "-A", cwd=self.src)
        _git("commit", "-qm", "two", cwd=self.src)
        self.new = _git("rev-parse", "HEAD", cwd=self.src).stdout.strip()
        _git("checkout", "-q", self.old, cwd=self.src)
        root = Path(self._tmp.name)
        self.decoders = root / "decoders"
        self.manifests = root / "manifests"
        self.failed = root / "native-build-failed"
        for name, path in (("_DECODER_DIR", self.decoders),
                           ("_BUILD_MANIFEST_DIR", self.manifests),
                           ("_NATIVE_BUILD_FAILED", self.failed)):
            p = mock.patch.object(smd, name, path)
            p.start()
            self.addCleanup(p.stop)

    def _pin(self, sha):
        p = mock.patch.object(smd, "_UBERSDR_COMMIT", sha)
        p.start()
        self.addCleanup(p.stop)

    def _verb(self, *builders, apply=False):
        args = types.SimpleNamespace(base=str(self.base), apply=apply,
                                     builders=list(builders))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            rc = smd.cmd_admin_native_build(args)
        return rc, out.getvalue()


class NativeRebuildsDueTests(_SourceRig):

    def test_a_source_off_its_pin_is_due_for_its_builder(self):
        self._pin(self.new)
        due = smd._native_rebuilds_due(self.base)
        self.assertEqual(due, [("ubersdr_wsprd", "ubersdr-decoders",
                                self.old, self.new)])

    def test_a_source_at_its_pin_is_not_due(self):
        self._pin(self.old)
        self.assertEqual(smd._native_rebuilds_due(self.base), [])

    def test_a_source_never_cloned_here_is_not_due(self):
        """No checkout: nothing was ever built here, so an update has
        nothing to return to a pin (align builds a missing source)."""
        self._pin(self.new)
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        self.assertEqual(smd._native_rebuilds_due(empty), [])


class StaleBuildAtThePinTests(_SourceRig):
    """Review of 69f036a, finding 2: a build that fails after its checkout
    leaves HEAD at the new pin and the old binaries installed."""

    def _provenance(self, sha):
        m = self.decoders / "ubersdr" / ".provenance"
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(f"ubersdr-decoders {sha} version=x parity=verified\n")

    def test_at_the_pin_with_a_stale_provenance_is_due(self):
        self._pin(self.old)                  # HEAD == pin
        self._provenance(self.new)           # but the build came from elsewhere
        self.assertEqual(smd._native_rebuilds_due(self.base),
                         [("ubersdr_wsprd", "ubersdr-decoders",
                           self.new, self.old)])

    def test_at_the_pin_with_a_matching_provenance_is_held(self):
        self._pin(self.old)
        self._provenance(self.old)
        self.assertEqual(smd._native_rebuilds_due(self.base), [])

    def test_at_the_pin_with_no_build_record_is_held(self):
        """A host that never built UberSDR's decoders must not retry the
        build on every update."""
        self._pin(self.old)
        self.assertEqual(smd._native_rebuilds_due(self.base), [])

    def test_the_build_manifest_is_the_fallback_record(self):
        self._pin(self.old)
        self.manifests.mkdir()
        (self.manifests / "ubersdr-decoders.toml").write_text(
            '[[upstream]]\nname = "ubersdr_wsprd"\n'
            f'sha = "{self.new}"\n')
        self.assertEqual(smd._native_built_from("ubersdr_wsprd"), self.new)
        self.assertEqual(len(smd._native_rebuilds_due(self.base)), 1)

    def test_diag_checks_the_decoder_builds_against_their_pins(self):
        pins = smd._expected_build_pins()
        self.assertEqual(pins["ubersdr-decoders"],
                         {"ubersdr_wsprd": smd._UBERSDR_COMMIT})
        self.assertEqual(pins["wsjtx-decoders"],
                         {"wsjtx": smd._WSJTX_COMMIT})


class NativeBuildVerbTests(_SourceRig):

    def setUp(self):
        super().setUp()
        p = mock.patch.object(smd, "_build_ubersdr_decoders",
                              return_value=True)
        self.build = p.start()
        self.addCleanup(p.stop)
        r = mock.patch.object(smd, "_record_native_build")
        self.record = r.start()
        self.addCleanup(r.stop)
        # Root creates the marker directory (install.sh's run); these
        # tests are not root, so it stands ready, as on a station.
        self.failed.mkdir()

    def test_dry_run_names_the_rebuild_and_builds_nothing(self):
        self._pin(self.new)
        rc, text = self._verb()
        self.assertEqual(rc, 0, text)
        self.assertIn(f"rebuild ubersdr-decoders: pin {self.old[:7]} → "
                      f"{self.new[:7]}", text)
        self.build.assert_not_called()

    def test_apply_runs_the_builder(self):
        self._pin(self.new)
        rc, text = self._verb(apply=True)
        self.assertEqual(rc, 0, text)
        self.build.assert_called_once()
        self.record.assert_called_once()

    def test_at_the_pin_nothing_is_built(self):
        self._pin(self.old)
        rc, text = self._verb(apply=True)
        self.assertEqual(rc, 0, text)
        self.build.assert_not_called()
        self.assertIn("at sigmond's pin", text)

    def test_a_failed_build_exits_nonzero(self):
        self._pin(self.new)
        self.build.return_value = False
        rc, text = self._verb(apply=True)
        self.assertEqual(rc, 1, text)
        self.record.assert_not_called()

    def _marker(self, builder="ubersdr-decoders"):
        return self.failed / builder

    def _plant(self, builder="ubersdr-decoders",
               text='component = "ubersdr-decoders"\n'):
        self.failed.mkdir(exist_ok=True)
        self._marker(builder).write_text(text)

    def test_a_failed_build_leaves_a_record_doctor_reports(self):
        """install.sh cannot fail its install over this, so on the v3.85 →
        main path nothing else says it (review of 69f036a, finding 3)."""
        self._pin(self.new)
        self.build.return_value = False
        self._verb(apply=True)
        self.assertTrue(self._marker().is_file())
        found = smd._native_build_failed_findings()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].kind, "native-build-failed")
        self.assertEqual(found[0].component, "ubersdr-decoders")
        self.assertIn("smd admin native-build --apply", found[0].detail)

    def test_a_successful_build_clears_the_record(self):
        self._pin(self.new)
        self.build.return_value = False
        self._verb(apply=True)
        self.build.return_value = True
        rc, _text = self._verb(apply=True)
        self.assertEqual(rc, 0)
        self.assertFalse(self._marker().exists())
        self.assertEqual(smd._native_build_failed_findings(), [])

    def test_a_run_with_nothing_due_clears_the_record(self):
        self._plant()
        self._pin(self.old)
        self._verb(apply=True)
        self.assertFalse(self._marker().exists())

    def test_a_dry_run_leaves_the_record(self):
        self._plant()
        self._pin(self.old)
        self._verb()
        self.assertTrue(self._marker().exists())

    # -- one marker per builder (re-review of 68c0ce8) ----------------------

    def test_another_builders_success_leaves_the_ubersdr_failure(self):
        """ubersdr fails, ka9q-web then succeeds: the ubersdr finding
        stays."""
        self._pin(self.new)
        self.build.return_value = False
        self._verb(apply=True)
        with mock.patch.object(smd, "_native_rebuilds_due", return_value=[
                ("onion", "ka9q-web", "a" * 40, "b" * 40)]), \
                mock.patch.object(smd, "_build_ka9q_web_with_onion",
                                  return_value=True):
            rc, text = self._verb("ka9q-web", apply=True)
        self.assertEqual(rc, 0, text)
        self.assertEqual([f.component for f in
                          smd._native_build_failed_findings()],
                         ["ubersdr-decoders"])

    def test_a_second_failure_does_not_overwrite_the_first(self):
        smd._mark_native_build_failed("ubersdr-decoders", "parity")
        smd._mark_native_build_failed("wsjtx-decoders", "cmake")
        found = {f.component: f.detail for f in
                 smd._native_build_failed_findings()}
        self.assertEqual(sorted(found), ["ubersdr-decoders",
                                         "wsjtx-decoders"])
        self.assertIn("parity", found["ubersdr-decoders"])
        self.assertIn("cmake", found["wsjtx-decoders"])

    def test_a_scoped_success_does_not_clear_the_install_marker(self):
        self._plant("install", 'reason = "died"\n')
        self._pin(self.new)
        rc, _ = self._verb("ubersdr-decoders", apply=True)
        self.assertEqual(rc, 0)
        self.assertTrue(self._marker("install").exists())

    def test_an_unscoped_success_clears_the_install_marker(self):
        self._plant("install", 'reason = "died"\n')
        self._pin(self.new)
        rc, _ = self._verb(apply=True)
        self.assertEqual(rc, 0)
        self.assertFalse(self._marker("install").exists())

    def test_an_unscoped_run_clears_a_builder_that_is_not_due(self):
        self._plant("wsjtx-decoders")
        self._pin(self.new)                  # ubersdr due; wsjtx is not
        self._verb(apply=True)
        self.assertFalse(self._marker("wsjtx-decoders").exists())

    def test_mark_failed_marks_install_only_when_nothing_says_more(self):
        args = types.SimpleNamespace(base=str(self.base), apply=False,
                                     builders=[], mark_failed="died")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = smd.cmd_admin_native_build(args)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn('reason = "died"', self._marker("install").read_text())
        # A builder's own marker already names the failure: no `install`.
        self._marker("install").unlink()
        self._plant()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            smd.cmd_admin_native_build(args)
        self.assertFalse(self._marker("install").exists())

    # -- root only, and by descriptor (security review of bb15419) ----------

    def test_a_non_root_clear_goes_through_smd_as_root_never_rm(self):
        self._plant()
        calls = []

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(smd, "_run", side_effect=fake_run), \
                mock.patch.object(smd.os, "unlink",
                                  side_effect=PermissionError(13, "denied")):
            smd._clear_native_build_failed("ubersdr-decoders")
        self.assertEqual(len(calls), 1, calls)
        argv = calls[0]
        self.assertEqual(argv[:4], ["sudo", "-n", "env",
                                    "SIGMOND_ALLOW_SUDO=1"])
        self.assertIn("--clear-failed", argv)
        self.assertEqual(argv[-1], "ubersdr-decoders")
        self.assertFalse(any(w in ("rm", "/bin/rm", "/usr/bin/rm")
                             for w in argv), argv)
        self.assertFalse(any(str(self.failed) in w for w in argv), argv)

    def test_a_non_root_mark_with_no_directory_goes_through_root(self):
        """Only root creates the directory, so an operator in the sigmond
        group never ends up owning it."""
        self.failed.rmdir()
        calls = []

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(smd, "_run", side_effect=fake_run):
            smd._mark_native_build_failed("ubersdr-decoders", "parity")
        self.assertFalse(os.path.lexists(self.failed))
        self.assertEqual(calls[0][:4], ["sudo", "-n", "env",
                                        "SIGMOND_ALLOW_SUDO=1"])
        self.assertIn("--mark-failed", calls[0])

    def test_clearing_through_a_symlinked_directory_leaves_its_target(self):
        elsewhere = Path(self._tmp.name) / "elsewhere-dir2"
        elsewhere.mkdir()
        (elsewhere / "ubersdr-decoders").write_text("precious\n")
        self.failed.rmdir()
        self.failed.symlink_to(elsewhere)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            smd._clear_native_build_failed("ubersdr-decoders")
        self.assertEqual((elsewhere / "ubersdr-decoders").read_text(),
                         "precious\n")

    def test_clear_failed_option_unlinks_by_descriptor(self):
        """The root half of a non-root clear."""
        self._plant()
        args = types.SimpleNamespace(base=str(self.base), apply=False,
                                     builders=["ubersdr-decoders"],
                                     mark_failed=None, clear_failed=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = smd.cmd_admin_native_build(args)
        self.assertEqual(rc, 0)
        self.assertFalse(self._marker().exists())

    def test_root_makes_the_directory_0755_under_a_restrictive_umask(self):
        """A root umask of 077 would leave 0700, and a non-root `smd
        doctor` could not read it (re-review of bb15419)."""
        self.failed.rmdir()
        real_fstat = os.fstat

        def fstat_as_root_owned(fd):
            st = real_fstat(fd)
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev,
                                   st.st_nlink, 0, 0, st.st_size,
                                   st.st_atime, st.st_mtime, st.st_ctime))

        old = os.umask(0o077)
        try:
            with mock.patch.object(smd.os, "geteuid", return_value=0), \
                    mock.patch.object(smd.os, "fstat",
                                      side_effect=fstat_as_root_owned):
                os.close(smd._native_failed_dir_fd(create=True))
        finally:
            os.umask(old)
        self.assertEqual(os.stat(self.failed).st_mode & 0o7777, 0o755)

    def test_root_refuses_a_directory_it_does_not_own(self):
        with mock.patch.object(smd.os, "geteuid", return_value=0):
            with self.assertRaises(OSError):
                smd._native_failed_dir_fd(create=True)
            found = smd._native_build_failed_findings()
        self.assertEqual(len(found), 1)
        self.assertIn("unsafe", found[0].detail)

    # -- the marker directory is group-writable: no link is ever followed ---

    def test_a_symlink_at_the_marker_is_replaced_not_followed(self):
        """/var/lib/sigmond is group-writable by sigmond, and root writes
        the marker: a planted link must not make root write its target
        (security review of 68c0ce8)."""
        victim = Path(self._tmp.name) / "elsewhere" / "victim"
        victim.parent.mkdir()
        victim.write_text("precious\n")
        self._marker().symlink_to(victim)
        smd._mark_native_build_failed("ubersdr-decoders", "parity")
        self.assertEqual(victim.read_text(), "precious\n")
        self.assertFalse(self._marker().is_symlink())
        self.assertTrue(self._marker().is_file())
        self.assertIn("ubersdr-decoders", self._marker().read_text())

    def test_a_dangling_symlink_at_the_marker_creates_nothing_elsewhere(self):
        target = Path(self._tmp.name) / "created-by-root"
        self._marker().symlink_to(target)
        smd._mark_native_build_failed("ubersdr-decoders", "parity")
        self.assertFalse(target.exists())
        self.assertFalse(self._marker().is_symlink())

    def test_a_symlinked_marker_directory_is_refused(self):
        elsewhere = Path(self._tmp.name) / "elsewhere-dir"
        elsewhere.mkdir()
        self.failed.rmdir()
        self.failed.symlink_to(elsewhere)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            smd._mark_native_build_failed("ubersdr-decoders", "parity")
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertIn("could not record", out.getvalue())

    def test_clearing_unlinks_a_symlink_and_leaves_its_target(self):
        victim = Path(self._tmp.name) / "victim2"
        victim.write_text("precious\n")
        self._marker().symlink_to(victim)
        smd._clear_native_build_failed("ubersdr-decoders")
        self.assertFalse(os.path.lexists(self._marker()))
        self.assertEqual(victim.read_text(), "precious\n")

    def test_doctor_does_not_read_through_a_symlink(self):
        victim = Path(self._tmp.name) / "victim3"
        victim.write_text('component = "secret"\nreason = "leak"\n')
        self._marker().symlink_to(victim)
        found = smd._native_build_failed_findings()
        self.assertFalse(any("leak" in f.detail or f.component == "secret"
                             for f in found), found)
        self.assertEqual(len(found), 1)
        self.assertIn("not a regular file", found[0].detail)

    def test_a_ka9q_web_build_says_to_restart_the_viewer(self):
        with mock.patch.object(smd, "_ONION_COMMIT", "f" * 40), \
                mock.patch.object(smd, "_build_ka9q_web_with_onion",
                                  return_value=True):
            onion = self.base / "onion"
            onion.mkdir()
            _git("init", "-q", "-b", "master", cwd=onion)
            (onion / "x").write_text("x\n")
            _git("add", "-A", cwd=onion)
            _git("commit", "-qm", "x", cwd=onion)
            self._pin(self.old)
            rc, text = self._verb("ka9q-web", apply=True)
        self.assertEqual(rc, 0, text)
        self.assertIn("restart ka9q-web", text)

    def test_a_named_builder_limits_the_run(self):
        self._pin(self.new)
        rc, text = self._verb("wsjtx-decoders", apply=True)
        self.assertEqual(rc, 0, text)
        self.build.assert_not_called()

    def test_onion_rebuilds_ka9q_web_by_force(self):
        """ka9q-web's own freshness check compares the ka9q-web binary
        only; onion off its pin would pass it untouched."""
        with mock.patch.object(smd, "_build_ka9q_web_with_onion",
                               return_value=True) as kw:
            ok = smd._run_native_builder("ka9q-web", self.base)
        self.assertTrue(ok)
        kw.assert_called_once_with(self.base / "ka9q-web", force=True)


class NativeGitOwnershipTests(unittest.TestCase):
    """The root-owned case.  v3.85's image build ran `smd install`, which
    elevates itself, so each builder's `git clone` ran as root and left a
    root-owned tree under the sigmond-owned /opt/git/sigmond."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = Path(self._tmp.name) / "ubersdr_wsprd"
        (self.src / ".git").mkdir(parents=True)
        self.calls = []

        def fake_run(cmd, **kw):
            self.calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        for attr, fake in (("_run", fake_run),
                           ("_run_git_net",
                            lambda cmd, verb: fake_run(cmd))):
            p = mock.patch.object(smd, attr, side_effect=fake)
            p.start()
            self.addCleanup(p.stop)

    def _as_root(self, owner_action, user=None, sigmond=True):
        for p in (mock.patch.object(smd.os, "geteuid", return_value=0),
                  mock.patch.object(smd, "_native_owner_resolve",
                                    return_value=(owner_action, user)),
                  mock.patch.object(smd, "_sigmond_user_exists",
                                    return_value=sigmond)):
            p.start()
            self.addCleanup(p.stop)

    def _git_calls(self):
        return [c for c in self.calls if "git" in c]

    def test_root_owned_tree_goes_to_sigmond_then_git_runs_as_sigmond(self):
        self._as_root("root-ok")
        smd._native_git(self.src, "fetch", "--quiet", "origin")
        self.assertEqual(self.calls[0],
                         ["chown", "-R", "sigmond:sigmond", str(self.src)])
        git = self._git_calls()
        self.assertEqual(len(git), 1, self.calls)
        self.assertEqual(git[0][:5], ["sudo", "-u", "sigmond", "-H", "--"])
        self.assertIn("fetch", git[0])

    def test_a_fetch_is_bounded_by_the_network_timeout(self):
        """install.sh runs these builds; a black-holed fetch must fail it,
        not hang it."""
        self._as_root("drop", "sigmond")
        with mock.patch.object(smd, "_run_git_net",
                               return_value=subprocess.CompletedProcess(
                                   [], 0, "", "")) as net:
            smd._native_git(self.src, "fetch", "--quiet", "origin")
            smd._native_git(self.src, "checkout", "--quiet", "abc")
        self.assertEqual(net.call_count, 1)
        self.assertIn("fetch", net.call_args.args[0])

    def test_a_tree_owned_by_a_user_runs_git_as_that_user(self):
        self._as_root("drop", "sigmond")
        smd._native_git(self.src, "checkout", "--quiet", "abc")
        self.assertFalse([c for c in self.calls if c[0] == "chown"])
        self.assertEqual(self._git_calls()[0][:5],
                         ["sudo", "-u", "sigmond", "-H", "--"])

    def test_split_ownership_is_repaired_too(self):
        """worktree and .git owned apart: the source is sigmond's own, so
        it goes back to one owner rather than being skipped forever."""
        self._as_root("refuse")
        smd._native_git(self.src, "fetch")
        self.assertEqual(self.calls[0][0], "chown")

    def test_root_with_no_sigmond_user_runs_git_as_root_in_a_root_tree(self):
        """A dev box with no sigmond account: a consistently root-owned
        tree stays consistent (gitowner ROOT_OK)."""
        self._as_root("root-ok", sigmond=False)
        smd._native_git(self.src, "fetch")
        self.assertFalse([c for c in self.calls if c[0] == "chown"])
        self.assertEqual(self._git_calls()[0][0], "git")

    def test_root_with_no_sigmond_user_refuses_a_split_tree(self):
        self._as_root("refuse", sigmond=False)
        r = smd._native_git(self.src, "fetch")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self._git_calls(), [])

    def test_clone_as_root_clones_as_sigmond(self):
        """The builder's own rule: clone unprivileged."""
        self._as_root("root-ok")
        smd._native_clone("https://example/x.git", self.src)
        self.assertEqual(self.calls[-1][:5],
                         ["sudo", "-u", "sigmond", "-H", "--"])
        self.assertIn("clone", self.calls[-1])

    def test_clone_unprivileged_runs_as_ourselves(self):
        with mock.patch.object(smd.os, "geteuid", return_value=1000):
            smd._native_clone("https://example/x.git", self.src)
        self.assertEqual(self.calls[-1][:2], ["git", "clone"])


class BuildersUseTheOwnerAwareGitTests(unittest.TestCase):
    """Each builder's fetch and checkout go through _native_git, never a
    bare `git -C <src> fetch` that runs as whoever smd is."""

    def _drive(self, builder, src, pin_const, *extra_patches):
        git_calls = []

        def fake_git(path, *args, **kw):
            git_calls.append((Path(path), args))
            # Fail the checkout so the builder stops before any compile.
            rc = 1 if args and args[0] == "checkout" else 0
            return subprocess.CompletedProcess(args, rc, "", "stop here")

        run_calls = []

        def fake_run(cmd, **kw):
            run_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        patches = [
            mock.patch.object(smd, "_native_git", side_effect=fake_git),
            mock.patch.object(smd, "_run", side_effect=fake_run),
            mock.patch.object(smd, "_git_head", return_value="0" * 40),
            mock.patch.object(smd, "_ensure_native_build_deps",
                              return_value=True),
            mock.patch.object(smd, "_apt",
                              return_value=subprocess.CompletedProcess(
                                  [], 0, "", "")),
            mock.patch.object(smd, pin_const, "f" * 40),
            *extra_patches,
        ]
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            st.enter_context(contextlib.redirect_stdout(io.StringIO()))
            st.enter_context(contextlib.redirect_stderr(io.StringIO()))
            ok = builder()
        self.assertFalse(ok)
        verbs = [a[0] for _p, a in git_calls]
        self.assertIn("fetch", verbs)
        self.assertIn("checkout", verbs)
        bare = [c for c in run_calls
                if c[:1] == ["git"] and ("fetch" in c or "checkout" in c)]
        self.assertEqual(bare, [])

    def _existing(self, name):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / name
        src.mkdir()
        return src

    def test_ubersdr(self):
        src = self._existing("ubersdr_wsprd")
        dest = Path(tempfile.mkdtemp())
        self._drive(smd._build_ubersdr_decoders, src, "_UBERSDR_COMMIT",
                    mock.patch.object(smd, "_UBERSDR_SRC", src),
                    mock.patch.object(smd, "_DECODER_DIR", dest))

    def test_wsjtx(self):
        src = self._existing("wsjtx")
        dest = Path(tempfile.mkdtemp())
        self._drive(smd._build_wsjtx_decoders, src, "_WSJTX_COMMIT",
                    mock.patch.object(smd, "_WSJTX_SRC", src),
                    mock.patch.object(smd, "_DECODER_DIR", dest),
                    mock.patch.object(smd, "_adopt_legacy_decoders"))

    def test_onion(self):
        src = self._existing("onion")
        web = self._existing("ka9q-web")
        self._drive(lambda: smd._build_ka9q_web_with_onion(web, force=True),
                    src, "_ONION_COMMIT",
                    mock.patch.object(smd, "_ONION_SRC", src),
                    mock.patch.object(smd, "_install_radiod_deps",
                                      return_value=True))


if __name__ == "__main__":
    unittest.main()


class InstallShMarkerTests(unittest.TestCase):
    """install.sh records a dead verb through smd's one safe writer, never
    by writing into /var/lib/sigmond from bash (security review of 68c0ce8)."""

    def setUp(self):
        text = (REPO / "install.sh").read_text()
        start = text.index("Checking native sources against sigmond's pins")
        self.block = text[start:text.index("# ─── done", start)]

    def test_the_fallback_goes_through_mark_failed(self):
        self.assertIn("admin native-build", self.block)
        self.assertIn("--mark-failed", self.block)

    def test_no_shell_write_into_the_marker_path(self):
        self.assertNotIn("native-build-failed", self.block)
        self.assertNotIn("tee", self.block)
        self.assertNotIn(">", self.block.replace("2>/dev/null", ""))
