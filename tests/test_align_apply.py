"""Tests for sigmond.align_apply — git is faked; nothing touches a real checkout."""
import inspect
import json
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from sigmond import align, align_apply

C = "4a92fbdbcce497035994e76636689d84f7ffe967"


def rel(commit=C, tag="v3.53"):
    return align.Release(tag=tag, manifest_text="", appliance_commit=commit,
                         components={"sigmond": "daba1f6"})


def ls_remote(lines):
    def run(argv, **kw):
        return types.SimpleNamespace(returncode=0, stdout="".join(lines), stderr="")
    return run


class VerifyReleaseTests(unittest.TestCase):
    def test_peeled_tag_matches(self):
        align_apply.verify_release(rel(), ls_remote([
            f"1111111111111111111111111111111111111111\trefs/tags/v3.53\n",
            f"{C}\trefs/tags/v3.53^{{}}\n"]))

    def test_mismatch_refuses(self):
        with self.assertRaises(align_apply.ApplyError) as cm:
            align_apply.verify_release(rel(), ls_remote(["2" * 40 + "\trefs/tags/v3.53^{}\n"]))
        self.assertIn("does not point at", str(cm.exception))

    def test_missing_tag_refuses(self):
        with self.assertRaises(align_apply.ApplyError):
            align_apply.verify_release(rel(), ls_remote([]))

    def test_manifest_without_commit_refuses(self):
        with self.assertRaises(align_apply.ApplyError):
            align_apply.verify_release(rel(commit=None), ls_remote([f"{C}\trefs/tags/v3.53\n"]))


class _FakeGit:
    """Records every argv it's called with; answers by the git subcommand
    (found by scanning for the literal "git" token, then skipping any
    "-c value" / "-C value" pairs — this also copes with argv wrapped by
    an as_owner, e.g. runuser -u <owner> -- git ...)."""
    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}  # subcommand -> (returncode, stdout, stderr)

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        sub = self._subcommand(argv)
        rc, out, err = self.answers.get(sub, (0, "", ""))
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    @staticmethod
    def _subcommand(argv):
        try:
            i = argv.index("git")
        except ValueError:
            return None
        i += 1
        while i < len(argv) and argv[i] in ("-c", "-C", "--no-optional-locks"):
            i += 1 if argv[i] == "--no-optional-locks" else 2
        return argv[i] if i < len(argv) else None


class ParseFetchBytesTests(unittest.TestCase):
    def test_mib(self):
        stderr = "Receiving objects: 100% (412/412), 1.23 MiB | 2.00 MiB/s, done.\n"
        self.assertEqual(align_apply.parse_fetch_bytes(stderr), 1289748)

    def test_kib(self):
        stderr = "Receiving objects:  80% (330/412),  56.50 KiB | 500.00 KiB/s\n"
        self.assertEqual(align_apply.parse_fetch_bytes(stderr), 57856)

    def test_empty_is_none(self):
        self.assertIsNone(align_apply.parse_fetch_bytes(""))


class ResolveTests(unittest.TestCase):
    def test_success_returns_the_sha(self):
        sha = "a" * 40
        fake = _FakeGit({"rev-parse": (0, sha + "\n", "")})
        self.assertEqual(align_apply.resolve("/repo", "abc123", run=fake), sha)

    def test_failure_raises_naming_the_sha(self):
        fake = _FakeGit({"rev-parse": (128, "", "fatal: ambiguous argument\n")})
        with self.assertRaises(align_apply.ApplyError) as cm:
            align_apply.resolve("/repo", "abc123", run=fake)
        self.assertIn("abc123", str(cm.exception))


class IsAncestorTests(unittest.TestCase):
    def test_rc0_is_true(self):
        fake = _FakeGit({"merge-base": (0, "", "")})
        self.assertIs(align_apply.is_ancestor("/repo", "a1", "b2", run=fake), True)

    def test_rc1_is_false(self):
        fake = _FakeGit({"merge-base": (1, "", "")})
        self.assertIs(align_apply.is_ancestor("/repo", "a1", "b2", run=fake), False)

    def test_other_rc_is_none(self):
        fake = _FakeGit({"merge-base": (128, "", "fatal: not a valid object\n")})
        self.assertIsNone(align_apply.is_ancestor("/repo", "a1", "b2", run=fake))


class InOriginHistoryTests(unittest.TestCase):
    def test_present_is_true(self):
        fake = _FakeGit({"for-each-ref": (0, "refs/remotes/origin/main\n", "")})
        self.assertTrue(align_apply.in_origin_history("/repo", "a" * 40, run=fake))

    def test_empty_is_false(self):
        fake = _FakeGit({"for-each-ref": (0, "", "")})
        self.assertFalse(align_apply.in_origin_history("/repo", "a" * 40, run=fake))

    def test_argv_checks_origin_remote_only_not_tags(self):
        # A tag created locally (or left from another remote) must never
        # make an unpublished commit look published.
        fake = _FakeGit({"for-each-ref": (0, "", "")})
        align_apply.in_origin_history("/repo", "a" * 40, run=fake)
        argv = fake.calls[-1]
        self.assertIn("refs/remotes/origin", argv)
        self.assertNotIn("refs/tags", argv)


class DirtyFilesTests(unittest.TestCase):
    def test_parses_porcelain_paths(self):
        fake = _FakeGit({"status": (0, " M uv.lock\n?? build/\n", "")})
        self.assertEqual(align_apply.dirty_files("/repo", run=fake), ["uv.lock", "build/"])


class CheckoutTests(unittest.TestCase):
    def test_wraps_via_as_owner_and_records_argv(self):
        full = "b" * 40
        fake = _FakeGit({"checkout": (0, "", "")})
        align_apply.checkout("/repo", "sigmond", full, run=fake)
        argv = fake.calls[-1]
        self.assertEqual(argv[:4], ["runuser", "-u", "sigmond", "--"])
        self.assertIn("checkout", argv)
        self.assertEqual(argv[-2:], ["--detach", full])

    def test_failure_raises(self):
        fake = _FakeGit({"checkout": (1, "", "error: pathspec did not match\n")})
        with self.assertRaises(align_apply.ApplyError):
            align_apply.checkout("/repo", "sigmond", "b" * 40, run=fake)


class FetchTests(unittest.TestCase):
    def test_never_passes_a_sha_argv_ends_with_origin(self):
        fake = _FakeGit({"fetch": (0, "", "")})
        align_apply.fetch("/repo", "sigmond", run=fake)
        self.assertEqual(fake.calls[-1][-1], "origin")

    def test_returns_bytes_parsed_from_stderr(self):
        stderr = "Receiving objects: 100% (412/412), 1.23 MiB | 2.00 MiB/s, done.\n"
        fake = _FakeGit({"fetch": (0, "", stderr)})
        got = align_apply.fetch("/repo", "sigmond", run=fake)
        self.assertEqual(got, 1289748)

    def test_nonzero_exit_raises(self):
        fake = _FakeGit({"fetch": (1, "", "fatal: could not read from remote\n")})
        with self.assertRaises(align_apply.ApplyError):
            align_apply.fetch("/repo", "sigmond", run=fake)


class WritePinTests(unittest.TestCase):
    def test_pin_holds_the_sha_and_exclude_gains_one_line_even_twice(self):
        full = "c" * 40
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".git" / "info").mkdir(parents=True)

            align_apply.write_pin(str(repo), full)
            self.assertEqual((repo / ".pin").read_text(), full + "\n")
            exclude = (repo / ".git" / "info" / "exclude").read_text()
            self.assertEqual(exclude.count(".pin"), 1)

            align_apply.write_pin(str(repo), full)
            exclude_again = (repo / ".git" / "info" / "exclude").read_text()
            self.assertEqual(exclude_again.count(".pin"), 1)


class EnsurePinExcludedTests(unittest.TestCase):
    def test_adds_the_line_and_reports_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".git" / "info").mkdir(parents=True)
            added = align_apply.ensure_pin_excluded(str(repo))
            self.assertTrue(added)
            self.assertIn(".pin", (repo / ".git" / "info" / "exclude").read_text().splitlines())

    def test_already_present_reports_false_and_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".git" / "info").mkdir(parents=True)
            (repo / ".git" / "info" / "exclude").write_text(".pin\n")
            added = align_apply.ensure_pin_excluded(str(repo))
            self.assertFalse(added)
            exclude = (repo / ".git" / "info" / "exclude").read_text()
            self.assertEqual(exclude.count(".pin"), 1)


class NormalizeRepoTests(unittest.TestCase):
    def test_ssh_and_https_forms_are_equal(self):
        self.assertEqual(
            align_apply.normalize_repo("git@github.com:HamSCI/sigmond.git"),
            align_apply.normalize_repo("https://github.com/hamsci/sigmond/"))


class _MultiFakeGit:
    """Fakes git across several component checkouts for apply_plan tests —
    one table per git subcommand, keyed by the component name (the repo
    path's last segment, robust to an as_owner wrapper in front of "git").
    Records every argv called."""

    def __init__(self):
        self.calls = []
        self.dirty = {}            # name -> list of dirty files
        self.unresolvable = set()  # names whose rev-parse fails
        self.changed = {}          # name -> set of changed files (git diff)
        self.not_in_origin = set() # names for which in_origin_history is False
        self.fetch_stderr = {}     # name -> git's progress stderr
        self.raise_on = {}         # subcommand -> exception to raise
        self.fail_checkout_to = set()  # SHAs a --detach checkout to fails
        self.head = {}             # name -> SHA the last --detach checkout set
        self.branch = {}           # name -> branch symbolic-ref reports
        self.branch_tip = {}       # name -> SHA `checkout <branch>` lands on
        self.resolve_map = {}      # short ref (as passed to rev-parse) -> full SHA

    @staticmethod
    def _name(argv):
        return Path(argv[argv.index("-C") + 1]).name

    @staticmethod
    def _subcommand(argv):
        i = argv.index("git") + 1
        while argv[i] in ("-c", "-C", "--no-optional-locks"):
            i += 1 if argv[i] == "--no-optional-locks" else 2
        return argv[i]

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        name = self._name(argv)
        sub = self._subcommand(argv)
        if sub in self.raise_on:
            raise self.raise_on[sub]
        ok = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if sub == "fetch":
            return types.SimpleNamespace(returncode=0, stdout="",
                                         stderr=self.fetch_stderr.get(name, ""))
        if sub == "rev-parse":
            if name in self.unresolvable:
                return types.SimpleNamespace(returncode=128, stdout="",
                                             stderr="fatal: ambiguous argument\n")
            sha = argv[-1].split("^")[0]
            if sha == "HEAD":   # where the last --detach checkout left it
                sha = self.head.get(name, "0" * 40)
            else:
                sha = self.resolve_map.get(sha, sha)
            return types.SimpleNamespace(returncode=0, stdout=sha + "\n", stderr="")
        if sub == "for-each-ref":
            present = name not in self.not_in_origin
            return types.SimpleNamespace(
                returncode=0, stdout="refs/remotes/origin/main\n" if present else "", stderr="")
        if sub == "status":
            files = self.dirty.get(name, [])
            return types.SimpleNamespace(returncode=0,
                                         stdout="".join(f" M {f}\n" for f in files), stderr="")
        if sub == "symbolic-ref":
            b = self.branch.get(name)
            return types.SimpleNamespace(returncode=0 if b else 1,
                                         stdout=(b + "\n") if b else "", stderr="")
        if sub == "checkout":
            if argv[-1] == self.branch.get(name):
                self.head[name] = self.branch_tip.get(name, "0" * 40)
                return ok
            if "--detach" in argv and argv[-1] in self.fail_checkout_to:
                return types.SimpleNamespace(returncode=1, stdout="",
                                             stderr="error: cannot checkout\n")
            if "--detach" in argv:
                self.head[name] = argv[-1]
            return ok
        if sub == "diff":
            files = self.changed.get(name, set())
            return types.SimpleNamespace(returncode=0,
                                         stdout="".join(f"{f}\n" for f in files), stderr="")
        return ok


def _catalog(names):
    return {n: types.SimpleNamespace(repo=f"https://github.com/HamSCI/{n}") for n in names}


def _ctx(base, git, names, **overrides):
    catalog = overrides.pop("catalog", None) or _catalog(names)
    origins = overrides.pop("origins", None) or {n: catalog[n].repo for n in names if n in catalog}
    kwargs = dict(
        base=base,
        run=git,
        as_owner=lambda o, a: ["as", o, *a],
        owner_of=lambda p: "sigmond",
        catalog=catalog,
        origins=origins,
        say=lambda msg: None,
    )
    kwargs.update(overrides)
    return align_apply.Ctx(**kwargs)


def _repo(base, name):
    """A tmp checkout directory with .git/info, so write_pin has somewhere real
    to write .pin and the exclude file."""
    d = Path(base) / name
    (d / ".git" / "info").mkdir(parents=True, exist_ok=True)
    return d


class ApplyPlanTests(unittest.TestCase):
    def test_forward_move_checks_out_and_writes_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            ctx = _ctx(base, git, ["sigmond"])
            live, target = "1" * 40, "2" * 40
            items = [align.Item("sigmond", "forward", live, target)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "moved")
            checkout_calls = [c for c in git.calls if git._subcommand(c) == "checkout"
                              and "--detach" in c]
            self.assertTrue(checkout_calls)
            self.assertIn(target, checkout_calls[-1])
            self.assertEqual((base / "sigmond" / ".pin").read_text(), target + "\n")

    def test_ahead_without_flag_is_left_no_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            ctx = _ctx(base, git, ["sigmond"])
            items = [align.Item("sigmond", "ahead", "2" * 40, "1" * 40, align.AHEAD_NOTE)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "left")
            self.assertEqual(steps[0].detail, align.AHEAD_NOTE)
            self.assertFalse(any(git._subcommand(c) == "checkout" for c in git.calls))

    def test_ahead_with_allow_rollback_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            ctx = _ctx(base, git, ["sigmond"], allow_rollback=True)
            items = [align.Item("sigmond", "ahead", "2" * 40, "1" * 40, align.AHEAD_NOTE)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "moved")
            self.assertTrue(any(git._subcommand(c) == "checkout" and "--detach" in c
                                for c in git.calls))

    def test_uvlock_only_dirt_resets_before_the_detach_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.dirty["sigmond"] = ["uv.lock"]
            ctx = _ctx(base, git, ["sigmond"])
            items = [align.Item("sigmond", "forward", "1" * 40, "2" * 40)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "moved")
            reset_idx = next(i for i, c in enumerate(git.calls)
                             if git._subcommand(c) == "checkout" and "uv.lock" in c)
            detach_idx = next(i for i, c in enumerate(git.calls)
                              if git._subcommand(c) == "checkout" and "--detach" in c)
            self.assertLess(reset_idx, detach_idx)

    def test_other_dirt_refuses_no_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.dirty["sigmond"] = ["foo.py"]
            ctx = _ctx(base, git, ["sigmond"])
            items = [align.Item("sigmond", "forward", "1" * 40, "2" * 40)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "refused")
            self.assertIn("dirty working tree", steps[0].detail)
            self.assertIn("foo.py", steps[0].detail)
            self.assertFalse(any(git._subcommand(c) == "checkout" for c in git.calls))

    def test_origin_mismatch_refuses_no_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            catalog = _catalog(["sigmond"])
            origins = {"sigmond": "https://github.com/someone-else/sigmond"}
            ctx = _ctx(base, git, ["sigmond"], catalog=catalog, origins=origins)
            items = [align.Item("sigmond", "forward", "1" * 40, "2" * 40)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "refused")
            self.assertIn("origin points at", steps[0].detail)
            self.assertFalse(any(git._subcommand(c) == "fetch" for c in git.calls))

    def test_resolve_failure_on_second_of_three_stops_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            names = ["sigmond", "hf-timestd", "wspr-recorder"]
            for n in names:
                _repo(base, n)
            git = _MultiFakeGit()
            git.unresolvable.add("hf-timestd")
            ctx = _ctx(base, git, names)
            items = [
                align.Item("sigmond", "forward", "1" * 40, "2" * 40),
                align.Item("hf-timestd", "forward", "3" * 40, "4" * 40),
                align.Item("wspr-recorder", "forward", "5" * 40, "6" * 40),
            ]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual([s.outcome for s in steps], ["moved", "failed", "skipped"])
            self.assertIn("stopped after hf-timestd failed", steps[2].detail)

    def test_pyproject_toml_change_triggers_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.changed["sigmond"] = {"pyproject.toml"}
            calls = []
            ctx = _ctx(base, git, ["sigmond"],
                      run_install=lambda repo: calls.append(repo) or 0)
            items = [align.Item("sigmond", "forward", "1" * 40, "2" * 40)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "moved")
            self.assertEqual(calls, [base / "sigmond"])

    def test_readme_only_change_skips_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.changed["sigmond"] = {"README.md"}
            calls = []
            ctx = _ctx(base, git, ["sigmond"],
                      run_install=lambda repo: calls.append(repo) or 0)
            items = [align.Item("sigmond", "forward", "1" * 40, "2" * 40)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "moved")
            self.assertEqual(calls, [])

    def test_byte_budget_exceeded_skips_later_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            names = ["sigmond", "hf-timestd"]
            for n in names:
                _repo(base, n)
            git = _MultiFakeGit()
            git.fetch_stderr["sigmond"] = \
                "Receiving objects: 100% (1/1), 2.00 MiB | 2.00 MiB/s, done.\n"
            ctx = _ctx(base, git, names, max_bytes=1_000_000)
            items = [
                align.Item("sigmond", "forward", "1" * 40, "2" * 40),
                align.Item("hf-timestd", "forward", "3" * 40, "4" * 40),
            ]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "moved")
            self.assertEqual(steps[1].outcome, "skipped")
            self.assertIn("byte budget", steps[1].detail)

    def test_missing_with_installer_installs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            git = _MultiFakeGit()
            calls = []
            ctx = _ctx(base, git, ["sigmond"],
                      install_missing=lambda name, target: calls.append((name, target)))
            items = [align.Item("sigmond", "missing", None, "2" * 40, "no checkout on this station")]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "installed")
            self.assertEqual(calls, [("sigmond", "2" * 40)])

    def test_missing_without_installer_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            git = _MultiFakeGit()
            ctx = _ctx(base, git, ["sigmond"])
            items = [align.Item("sigmond", "missing", None, "2" * 40, "no checkout on this station")]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "refused")
            self.assertIn("no installer wired", steps[0].detail)

    def test_fetch_argv_never_contains_a_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            ctx = _ctx(base, git, ["sigmond"])
            live, target = "1" * 40, "2" * 40
            items = [align.Item("sigmond", "forward", live, target)]
            align_apply.apply_plan(rel(), items, ctx)
            fetch_calls = [c for c in git.calls if git._subcommand(c) == "fetch"]
            self.assertTrue(fetch_calls)
            for c in fetch_calls:
                self.assertNotIn(live, c)
                self.assertNotIn(target, c)

    # --- Controller rulings ---

    def test_prefetched_component_skips_fetch_but_its_bytes_still_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            names = ["sigmond", "hf-timestd"]
            for n in names:
                _repo(base, n)
            git = _MultiFakeGit()
            ctx = _ctx(base, git, names, max_bytes=1_000_000,
                      prefetched={"sigmond": 2_000_000})
            items = [
                align.Item("sigmond", "forward", "1" * 40, "2" * 40),
                align.Item("hf-timestd", "forward", "3" * 40, "4" * 40),
            ]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "moved")
            self.assertEqual(steps[1].outcome, "skipped")
            self.assertIn("byte budget", steps[1].detail)
            self.assertFalse(any(git._subcommand(c) == "fetch" and git._name(c) == "sigmond"
                                 for c in git.calls))

    def test_chown_applied_to_pin_and_exclude(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            chown_calls = []
            ctx = _ctx(base, git, ["sigmond"], chown=lambda p, o: chown_calls.append((p, o)))
            items = [align.Item("sigmond", "forward", "1" * 40, "2" * 40)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "moved")
            repo = base / "sigmond"
            self.assertIn((repo / ".pin", "sigmond"), chown_calls)
            self.assertIn((repo / ".git" / "info" / "exclude", "sigmond"), chown_calls)

    def test_pin_alone_is_not_dirt(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.dirty["sigmond"] = [".pin"]
            ctx = _ctx(base, git, ["sigmond"])
            items = [align.Item("sigmond", "forward", "1" * 40, "2" * 40)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "moved")

    # --- current / stray / refuse / diverged pass through untouched ---

    def test_current_and_stray_do_nothing(self):
        base = Path(tempfile.mkdtemp())
        (_repo(base, "sigmond") / ".pin").write_text("1" * 40 + "\n")
        git = _MultiFakeGit()
        ctx = _ctx(base, git, ["sigmond", "old-client"])
        items = [
            align.Item("sigmond", "current", "1" * 40, "1" * 40),
            align.Item("old-client", "stray", "9" * 40, None,
                      "not in the release manifest; left alone"),
        ]
        steps = align_apply.apply_plan(rel(), items, ctx)
        self.assertEqual(steps[0].outcome, "current")
        self.assertEqual(steps[1].outcome, "left")
        self.assertEqual(git.calls, [])

    def test_refuse_and_diverged_pass_through_the_note(self):
        base = Path(tempfile.mkdtemp())
        git = _MultiFakeGit()
        ctx = _ctx(base, git, ["sigmond"])
        items = [align.Item("sigmond", "diverged", "1" * 40, "2" * 40, align.DIVERGED_NOTE)]
        steps = align_apply.apply_plan(rel(), items, ctx)
        self.assertEqual(steps[0].outcome, "refused")
        self.assertEqual(steps[0].detail, align.DIVERGED_NOTE)

    # --- restart messaging belongs to the CLI's restart stage, not apply_plan ---

    def test_apply_plan_leaves_restart_messaging_to_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            messages = []
            ctx = _ctx(base, git, ["sigmond"], say=messages.append)
            items = [align.Item("sigmond", "forward", "1" * 40, "2" * 40)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "moved")
            self.assertFalse(any("Plan 2b" in m or "old code" in m for m in messages))

    def test_no_say_notice_without_a_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            git = _MultiFakeGit()
            messages = []
            ctx = _ctx(base, git, ["sigmond"], say=messages.append)
            items = [align.Item("sigmond", "current", "1" * 40, "1" * 40)]
            align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(messages, [])

    # --- Fix round 1 ---

    def test_a_refused_item_does_not_stop_the_plan(self):
        """Regression: only `failed` (and the byte budget) stops the plan.
        A business-rule `refused` component is skipped over, not fatal."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            names = ["sigmond", "hf-timestd"]
            for n in names:
                _repo(base, n)
            git = _MultiFakeGit()
            git.dirty["sigmond"] = ["foo.py"]
            ctx = _ctx(base, git, names)
            items = [
                align.Item("sigmond", "forward", "1" * 40, "2" * 40),
                align.Item("hf-timestd", "forward", "3" * 40, "4" * 40),
            ]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual([s.outcome for s in steps], ["refused", "moved"])
            checkout_calls = [c for c in git.calls if git._subcommand(c) == "checkout"
                              and "--detach" in c]
            self.assertTrue(any(git._name(c) == "hf-timestd" for c in checkout_calls))

    def test_uvlock_plus_other_dirt_carries_the_hint_and_touches_neither_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.dirty["sigmond"] = ["uv.lock", "foo.py"]
            ctx = _ctx(base, git, ["sigmond"])
            items = [align.Item("sigmond", "forward", "1" * 40, "2" * 40)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "refused")
            self.assertIn("uv.lock is regenerated by install.sh", steps[0].detail)
            self.assertIn("`git checkout -- uv.lock` then retry", steps[0].detail)
            reset_calls = [c for c in git.calls if git._subcommand(c) == "checkout"
                          and "uv.lock" in c]
            detach_calls = [c for c in git.calls if git._subcommand(c) == "checkout"
                            and "--detach" in c]
            self.assertEqual(reset_calls, [])
            self.assertEqual(detach_calls, [])

    def test_install_missing_exception_fails_and_stops_the_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "hf-timestd")
            git = _MultiFakeGit()

            def boom(name, target):
                raise RuntimeError("disk full")

            ctx = _ctx(base, git, ["sigmond", "hf-timestd"], install_missing=boom)
            items = [
                align.Item("sigmond", "missing", None, "2" * 40, "no checkout on this station"),
                align.Item("hf-timestd", "forward", "3" * 40, "4" * 40),
            ]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual([s.outcome for s in steps], ["failed", "skipped"])
            self.assertIn("install failed", steps[0].detail)
            self.assertIn("disk full", steps[0].detail)
            self.assertEqual(git.calls, [])


def _detach_targets(git):
    """The SHA each `checkout --detach` was sent to, in call order."""
    return [c[-1] for c in git.calls
            if _MultiFakeGit._subcommand(c) == "checkout" and "--detach" in c]


LIVE, TARGET = "1" * 40, "2" * 40


class FinalReviewMoveTests(unittest.TestCase):
    """Final whole-branch review: C1, I3, I7, I8, and the minors in _move."""

    def _one(self, base, git, **over):
        ctx = _ctx(base, git, ["sigmond"], **over)
        items = [align.Item("sigmond", "forward", LIVE, TARGET)]
        return align_apply.apply_plan(rel(), items, ctx)

    # --- C1: a failed install.sh is resumable ---

    def test_install_failure_rolls_back_the_checkout_and_restores_the_prior_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _repo(base, "sigmond")
            (repo / ".pin").write_text(LIVE + "\n")
            git = _MultiFakeGit()
            git.changed["sigmond"] = {"pyproject.toml"}
            steps = self._one(base, git, run_install=lambda r: 1)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertEqual(steps[0].detail, "install.sh exit 1 — rolled back to 11111111")
            self.assertEqual(_detach_targets(git), [TARGET, LIVE])
            rollback = [c for c in git.calls if git._subcommand(c) == "checkout"][-1]
            self.assertEqual(rollback[:2], ["as", "sigmond"])   # as the owner
            self.assertEqual((repo / ".pin").read_text(), LIVE + "\n")

    def test_install_failure_with_no_prior_pin_removes_the_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.changed["sigmond"] = {"pyproject.toml"}
            steps = self._one(base, git, run_install=lambda r: 3)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertIn("install.sh exit 3", steps[0].detail)
            self.assertFalse((repo / ".pin").exists())

    def test_install_exception_also_rolls_back(self):
        def boom(repo):
            raise RuntimeError("no bash")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.changed["sigmond"] = {"install.sh"}
            steps = self._one(base, git, run_install=boom)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertIn("no bash", steps[0].detail)
            self.assertIn("rolled back to 11111111", steps[0].detail)
            self.assertEqual(_detach_targets(git), [TARGET, LIVE])

    def test_a_failed_rollback_says_so_and_names_where_the_checkout_sits(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.changed["sigmond"] = {"pyproject.toml"}
            git.fail_checkout_to.add(LIVE)
            steps = self._one(base, git, run_install=lambda r: 1)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertIn("install.sh exit 1 — rollback FAILED:", steps[0].detail)
            self.assertIn("checkout left at 22222222", steps[0].detail)

    def test_says_running_install_before_it_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.changed["sigmond"] = {"pyproject.toml"}
            events = []
            self._one(base, git, say=events.append,
                      run_install=lambda r: events.append("INSTALL") or 0)
            self.assertIn("  sigmond: running install.sh …", events)
            self.assertLess(events.index("  sigmond: running install.sh …"),
                            events.index("INSTALL"))

    def test_rollback_onto_a_branch_whose_tip_moved_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.changed["sigmond"] = {"pyproject.toml"}
            git.branch["sigmond"] = "main"
            git.branch_tip["sigmond"] = "7" * 40      # not LIVE
            steps = self._one(base, git, run_install=lambda r: 1)
            self.assertIn("rollback FAILED: main HEAD is 77777777 after the rollback, "
                          "not 11111111", steps[0].detail)

    def test_rollback_onto_the_branch_says_which(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.changed["sigmond"] = {"pyproject.toml"}
            git.branch["sigmond"] = "main"
            git.branch_tip["sigmond"] = LIVE
            steps = self._one(base, git, run_install=lambda r: 1)
            self.assertEqual(steps[0].detail, "install.sh exit 1 — rolled back to 11111111 on main")
            back = [c for c in git.calls if git._subcommand(c) == "checkout"][-1]
            self.assertEqual(back[:2], ["as", "sigmond"])
            self.assertEqual(back[-1], "main")

    # --- T2 / T4 ---

    def test_pin_not_in_origin_history_refuses_without_a_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.not_in_origin.add("sigmond")
            steps = self._one(base, git)
            self.assertEqual(steps[0].outcome, "refused")
            self.assertIn("not in origin's history", steps[0].detail)
            self.assertEqual(_detach_targets(git), [])

    def test_a_uvlock_only_change_triggers_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.changed["sigmond"] = {"uv.lock"}
            calls = []
            steps = self._one(base, git, run_install=lambda r: calls.append(r) or 0)
            self.assertEqual(steps[0].outcome, "moved")
            self.assertEqual(calls, [base / "sigmond"])

    # --- B4 finding: a short `live` SHA must not shorten the "moved"
    # detail to fewer than 8 chars on the left side. ---

    def test_moved_detail_shortens_a_short_live_sha_to_eight(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            live_short, live_full = "1" * 7, "1" * 40
            git.resolve_map[live_short] = live_full
            ctx = _ctx(base, git, ["sigmond"])
            items = [align.Item("sigmond", "forward", live_short, "2" * 40)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "moved")
            self.assertEqual(steps[0].detail, "11111111 -> 22222222")

    # --- minor: .git/info chowned too ---

    def test_move_chowns_git_info_as_well(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _repo(base, "sigmond")
            git = _MultiFakeGit()
            chowned = []
            self._one(base, git, chown=lambda p, o: chowned.append(p))
            self.assertIn(repo / ".git" / "info", chowned)

    # --- Task 5: radiod rebuilds when ka9q-radio moves ---

    def test_ka9q_radio_forward_moves_and_calls_the_build_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _repo(base, "ka9q-radio")
            git = _MultiFakeGit()
            calls = []
            ctx = _ctx(base, git, ["ka9q-radio"],
                      build=lambda name, r: calls.append((name, r)) or 0)
            items = [align.Item("ka9q-radio", "forward", LIVE, TARGET)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "moved")
            self.assertEqual(calls, [("ka9q-radio", repo)])

    # --- Fix round 1 / I1: _build_ka9q_radio is not atomic (make clean,
    # make, sudo make install, THEN the rx888.so check) — a failed build
    # can leave a new-commit radiod installed even though the checkout
    # rolled back. Rebuild at the rolled-back commit too. ---

    def test_ka9q_radio_build_failure_then_successful_rebuild_at_old_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "ka9q-radio")
            git = _MultiFakeGit()
            calls = []
            results = iter([1, 0])

            def build(name, r):
                calls.append((name, r))
                return next(results)
            ctx = _ctx(base, git, ["ka9q-radio"], build=build)
            items = [align.Item("ka9q-radio", "forward", LIVE, TARGET)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertEqual(steps[0].detail,
                             "build exit 1 — rolled back to 11111111 and rebuilt it")
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(_detach_targets(git), [TARGET, LIVE])

    def test_ka9q_radio_build_failure_and_rebuild_at_old_commit_also_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "ka9q-radio")
            git = _MultiFakeGit()
            ctx = _ctx(base, git, ["ka9q-radio"], build=lambda name, r: 1)
            items = [align.Item("ka9q-radio", "forward", LIVE, TARGET)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertEqual(
                steps[0].detail,
                "build exit 1; rebuild at 11111111 ALSO failed — installed radiod "
                "may not match the checkout; rebuild by hand, do NOT restart radiod")
            self.assertEqual(_detach_targets(git), [TARGET, LIVE])

    def test_ka9q_radio_rollback_failure_says_do_not_restart_radiod(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "ka9q-radio")
            git = _MultiFakeGit()
            git.fail_checkout_to.add(LIVE)
            ctx = _ctx(base, git, ["ka9q-radio"], build=lambda name, r: 1)
            items = [align.Item("ka9q-radio", "forward", LIVE, TARGET)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertIn("rollback FAILED", steps[0].detail)
            self.assertIn("do NOT restart radiod", steps[0].detail)

    def test_ka9q_radio_with_no_builder_wired_fails_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "ka9q-radio")
            git = _MultiFakeGit()
            ctx = _ctx(base, git, ["ka9q-radio"])  # build defaults to None
            items = [align.Item("ka9q-radio", "forward", LIVE, TARGET)]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertIn("no builder wired", steps[0].detail)
            self.assertEqual(_detach_targets(git), [TARGET, LIVE])

    def test_missing_ka9q_radio_is_still_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            git = _MultiFakeGit()
            ctx = _ctx(base, git, ["ka9q-radio"])
            items = [align.Item("ka9q-radio", "missing", None, TARGET,
                                "no checkout on this station")]
            steps = align_apply.apply_plan(rel(), items, ctx)
            self.assertEqual(steps[0].outcome, "refused")
            self.assertEqual(git.calls, [])

    # --- I3 ---

    def test_git_argv_carries_no_optional_locks_right_after_git(self):
        self.assertEqual(align_apply.git("/r", "status")[:2], ["git", "--no-optional-locks"])

    # --- I2 ---

    def test_dirty_files_ignores_untracked(self):
        fake = _FakeGit({"status": (0, " M uv.lock\n", "")})
        align_apply.dirty_files("/repo", run=fake)
        self.assertIn("--untracked-files=no", fake.calls[-1])

    # --- I7 ---

    def test_a_fetch_timeout_is_a_failed_step_not_a_traceback(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.raise_on["fetch"] = subprocess.TimeoutExpired(["git", "fetch"], 300)
            steps = self._one(base, git)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertIn("timed out", steps[0].detail)
            self.assertIn(str(base / "sigmond"), steps[0].detail)

    def test_an_oserror_from_git_is_a_failed_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.raise_on["status"] = FileNotFoundError("git")
            steps = self._one(base, git)
            self.assertEqual(steps[0].outcome, "failed")

    def test_fetch_never_waits_on_a_credential_prompt(self):
        seen = {}

        def run(argv, **kw):
            seen.update(kw)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        align_apply.fetch("/repo", "sigmond", run=run)
        import subprocess
        self.assertEqual(seen.get("stdin"), subprocess.DEVNULL)
        self.assertEqual(seen["env"].get("GIT_TERMINAL_PROMPT"), "0")
        self.assertIn("PATH", seen["env"])

    def test_write_pin_oserror_is_failed_and_rolls_back(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            with mock.patch("sigmond.align_apply.write_pin", side_effect=PermissionError("ro")):
                steps = self._one(base, git)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertIn("ro", steps[0].detail)
            self.assertEqual(_detach_targets(git), [TARGET, LIVE])

    def test_chown_oserror_is_failed(self):
        def bad_chown(p, o):
            raise PermissionError("chown denied")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            steps = self._one(base, git, chown=bad_chown)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertIn("chown denied", steps[0].detail)

    def test_owner_lookup_failure_is_failed(self):
        def no_owner(p):
            raise KeyError("uid 1234")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _repo(base, "sigmond")
            git = _MultiFakeGit()
            steps = self._one(base, git, owner_of=no_owner)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertEqual(git.calls, [])

    # --- I8: current components get their .pin refreshed ---

    def _current(self, base, git, **over):
        ctx = _ctx(base, git, ["sigmond"], **over)
        items = [align.Item("sigmond", "current", TARGET, TARGET)]
        return align_apply.apply_plan(rel(), items, ctx)

    def test_current_without_a_pin_gets_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _repo(base, "sigmond")
            git = _MultiFakeGit()
            chowned = []
            steps = self._current(base, git, chown=lambda p, o: chowned.append(p))
            self.assertEqual(steps[0].outcome, "current")
            self.assertEqual(steps[0].detail, "pin refreshed")
            self.assertEqual((repo / ".pin").read_text(), TARGET + "\n")
            self.assertEqual(set(chowned), {repo / ".pin", repo / ".git" / "info" / "exclude",
                                            repo / ".git" / "info"})

    def test_current_with_a_pin_naming_another_commit_is_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _repo(base, "sigmond")
            (repo / ".pin").write_text("9" * 40 + "\n")
            git = _MultiFakeGit()
            steps = self._current(base, git)
            self.assertEqual(steps[0].detail, "pin refreshed")
            self.assertEqual((repo / ".pin").read_text(), TARGET + "\n")

    def test_current_with_the_right_pin_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _repo(base, "sigmond")
            (repo / ".pin").write_text(TARGET + "\n")
            (repo / ".git" / "info" / "exclude").write_text(".pin\n")
            git = _MultiFakeGit()
            steps = self._current(base, git)
            self.assertEqual(steps[0].outcome, "current")
            self.assertEqual(steps[0].detail, "")
            self.assertEqual(git.calls, [])

    # --- B4 finding: a matching pin whose exclude line was never written
    # (e.g. a checkout pinned before write_pin grew the exclude step) must
    # still get it — otherwise `.pin` shows as untracked forever. ---

    def test_current_with_matching_pin_but_missing_exclude_gets_it_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _repo(base, "sigmond")
            (repo / ".pin").write_text(TARGET + "\n")
            git = _MultiFakeGit()
            chowned = []
            steps = self._current(base, git, chown=lambda p, o: chowned.append(p))
            self.assertEqual(steps[0].outcome, "current")
            self.assertEqual(steps[0].detail, "pin excluded from git")
            exclude = (repo / ".git" / "info" / "exclude").read_text()
            self.assertIn(".pin", exclude.splitlines())
            self.assertIn(repo / ".pin", chowned)

    def test_current_whose_target_will_not_resolve_is_left_and_says_why(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = _repo(base, "sigmond")
            git = _MultiFakeGit()
            git.unresolvable.add("sigmond")
            steps = self._current(base, git)
            self.assertEqual(steps[0].outcome, "current")
            self.assertIn("pin not refreshed", steps[0].detail)
            self.assertIn("does not resolve", steps[0].detail)
            self.assertFalse((repo / ".pin").exists())


class RollbackRealGitTests(unittest.TestCase):
    """Fix round 2 (N1, N2): _roll_back against a REAL tmp git repo — an
    install.sh that regenerates uv.lock and then fails, and a checkout
    that sat on a branch."""

    def _git(self, repo, *args):
        import subprocess
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def _two_commits(self, base):
        """sigmond/ with commit A (uv.lock "a") on main and commit B
        (uv.lock "b") on `next`; origin/main -> B so origin vouches for it.
        Returns (repo, A, B) with main checked out at A."""
        repo = base / "sigmond"
        repo.mkdir()
        self._git(repo, "init", "-q", "-b", "main")
        self._git(repo, "config", "user.name", "T")
        self._git(repo, "config", "user.email", "t@example.com")
        (repo / "uv.lock").write_text("a\n")
        self._git(repo, "add", "uv.lock")
        self._git(repo, "commit", "-q", "-m", "A")
        a = self._git(repo, "rev-parse", "HEAD")
        self._git(repo, "checkout", "-q", "-b", "next")
        (repo / "uv.lock").write_text("b\n")
        self._git(repo, "commit", "-q", "-am", "B")
        b = self._git(repo, "rev-parse", "HEAD")
        self._git(repo, "checkout", "-q", "main")
        self._git(repo, "update-ref", "refs/remotes/origin/main", b)
        return repo, a, b

    def _apply(self, base, a, b):
        import subprocess

        def failing_install(repo):
            (repo / "uv.lock").write_text("regenerated by uv sync\n")
            return 1
        ctx = align_apply.Ctx(base=base, run=subprocess.run, as_owner=lambda o, argv: argv,
                              owner_of=lambda p: "me", catalog={}, origins={},
                              prefetched={"sigmond": None}, run_install=failing_install,
                              say=lambda m: None)
        return align_apply.apply_plan(rel(), [align.Item("sigmond", "forward", a, b)], ctx)

    def test_install_that_regenerates_uvlock_then_fails_still_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, a, b = self._two_commits(base)
            self._git(repo, "checkout", "-q", "--detach", a)
            (repo / ".pin").write_text(a + "\n")
            steps = self._apply(base, a, b)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertIn("rolled back to", steps[0].detail)
            self.assertIn("reset uv.lock", steps[0].detail)
            self.assertNotIn("FAILED", steps[0].detail)
            self.assertEqual(self._git(repo, "rev-parse", "HEAD"), a)
            self.assertEqual((repo / ".pin").read_text(), a + "\n")

    def test_a_checkout_on_a_branch_returns_to_that_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, a, b = self._two_commits(base)          # on main, at A
            steps = self._apply(base, a, b)
            self.assertEqual(steps[0].outcome, "failed")
            self.assertIn("rolled back to", steps[0].detail)
            self.assertEqual(self._git(repo, "symbolic-ref", "-q", "--short", "HEAD"), "main")
            self.assertEqual(self._git(repo, "rev-parse", "HEAD"), a)
            self.assertFalse((repo / ".pin").exists())

    def test_a_rollback_that_cannot_complete_says_install_will_not_be_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, a, b = self._two_commits(base)
            self._git(repo, "checkout", "-q", "--detach", a)
            import subprocess

            def install_dirties_more(r):
                (r / "uv.lock").write_text("regenerated\n")
                (r / "extra.txt").write_text("x")
                self._git(r, "add", "extra.txt")          # tracked dirt beyond uv.lock
                return 1
            ctx = align_apply.Ctx(base=base, run=subprocess.run,
                                  as_owner=lambda o, argv: argv, owner_of=lambda p: "me",
                                  catalog={}, origins={}, prefetched={"sigmond": None},
                                  run_install=install_dirties_more, say=lambda m: None)
            steps = align_apply.apply_plan(rel(), [align.Item("sigmond", "forward", a, b)], ctx)
            d = steps[0].detail
            self.assertIn("rollback FAILED:", d)
            self.assertIn(f"checkout left at {b[:8]}", d)
            self.assertIn("a re-run will NOT retry install.sh — run "
                          f"{repo}/install.sh (or scripts/install.sh) by hand, then re-run", d)
            self.assertFalse((repo / ".pin").exists())    # .pin restored before the checkout


class RecordTests(unittest.TestCase):
    def test_clean_run_writes_manifest_aligned_json_and_prev(self):
        release = align.Release(tag="v3.53", manifest_text="sigmond: 2222222\n",
                                appliance_commit=C,
                                components={"sigmond": "2" * 40, "hf-timestd": "4" * 40})
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.txt"
            manifest_path.write_text("old manifest\n")
            aligned_path = Path(tmp) / "aligned.json"
            history_calls = []
            steps = [
                align_apply.Step("sigmond", "moved", "11111111 -> 22222222", 100),
                align_apply.Step("hf-timestd", "current"),
            ]
            align_apply.record(release, steps, manifest_path=manifest_path,
                               aligned_path=aligned_path,
                               history=history_calls.append, now=lambda: "2026-09-24T00:00:00Z")

            self.assertEqual(manifest_path.read_text(), release.manifest_text)
            prev_path = manifest_path.with_name(manifest_path.name + ".prev")
            self.assertEqual(prev_path.read_text(), "old manifest\n")

            data = json.loads(aligned_path.read_text())
            self.assertEqual(data["release"], release.tag)
            self.assertEqual(data["appliance_commit"], release.appliance_commit)
            self.assertEqual(data["at"], "2026-09-24T00:00:00Z")
            self.assertEqual(data["components"], release.components)

            self.assertEqual(len(history_calls), 1)
            self.assertEqual(history_calls[0]["at"], "2026-09-24T00:00:00Z")
            self.assertEqual(history_calls[0]["what"],
                             "smd align --apply v3.53: sigmond moved 11111111 -> 22222222")

    def test_no_prev_written_when_manifest_path_did_not_exist(self):
        release = align.Release(tag="v3.53", manifest_text="sigmond: 2222222\n",
                                appliance_commit=C, components={"sigmond": "2" * 40})
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.txt"
            aligned_path = Path(tmp) / "aligned.json"
            steps = [align_apply.Step("sigmond", "moved", "11111111 -> 22222222", 100)]
            align_apply.record(release, steps, manifest_path=manifest_path,
                               aligned_path=aligned_path,
                               history=lambda e: None, now=lambda: "t")
            self.assertTrue(manifest_path.exists())
            self.assertFalse(manifest_path.with_name(manifest_path.name + ".prev").exists())

    def test_a_failed_step_writes_neither_file_but_still_logs_the_failure(self):
        release = align.Release(tag="v3.53", manifest_text="sigmond: 2222222\n",
                                appliance_commit=C, components={"sigmond": "2" * 40})
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.txt"
            manifest_path.write_text("old manifest\n")
            aligned_path = Path(tmp) / "aligned.json"
            history_calls = []
            steps = [
                align_apply.Step("sigmond", "moved", "11111111 -> 22222222", 100),
                align_apply.Step("hf-timestd", "failed", "boom"),
                align_apply.Step("wspr-recorder", "skipped", "stopped after hf-timestd failed"),
            ]
            align_apply.record(release, steps, manifest_path=manifest_path,
                               aligned_path=aligned_path,
                               history=history_calls.append, now=lambda: "t")

            self.assertEqual(manifest_path.read_text(), "old manifest\n")
            self.assertFalse(aligned_path.exists())
            whats = [h["what"] for h in history_calls]
            self.assertEqual(len(whats), 2)
            self.assertIn("smd align --apply v3.53: sigmond moved 11111111 -> 22222222", whats)
            self.assertIn("smd align --apply v3.53: hf-timestd failed boom", whats)

    def test_installed_step_also_logs_a_history_line(self):
        release = align.Release(tag="v3.53", manifest_text="", appliance_commit=C,
                                components={"sigmond": "2" * 40})
        with tempfile.TemporaryDirectory() as tmp:
            history_calls = []
            steps = [align_apply.Step("sigmond", "installed")]
            align_apply.record(release, steps,
                               manifest_path=Path(tmp) / "manifest.txt",
                               aligned_path=Path(tmp) / "aligned.json",
                               history=history_calls.append, now=lambda: "t")
            self.assertEqual(len(history_calls), 1)
            self.assertEqual(history_calls[0]["what"], "smd align --apply v3.53: sigmond installed ")

    def test_left_refused_and_current_steps_are_not_logged(self):
        release = align.Release(tag="v3.53", manifest_text="", appliance_commit=C,
                                components={"sigmond": "2" * 40})
        with tempfile.TemporaryDirectory() as tmp:
            history_calls = []
            steps = [
                align_apply.Step("sigmond", "current"),
                align_apply.Step("old-client", "left"),
                align_apply.Step("hf-timestd", "refused", "diverged"),
            ]
            align_apply.record(release, steps,
                               manifest_path=Path(tmp) / "manifest.txt",
                               aligned_path=Path(tmp) / "aligned.json",
                               history=history_calls.append, now=lambda: "t")
            self.assertEqual(history_calls, [])

    def test_default_aligned_path_is_the_appliance_state_file(self):
        self.assertEqual(align_apply.ALIGNED_RECORD, Path("/etc/sigmond-appliance/aligned.json"))

    def test_left_steps_are_named_in_aligned_json_not_folded_into_components(self):
        # aligned.json must not overstate alignment: a component left ahead
        # of the pin is not "aligned to its target" the way components is
        # read, so it gets its own key, naming the reason.
        release = align.Release(tag="v3.53", manifest_text="text\n", appliance_commit=C,
                                components={"sigmond": "2" * 40, "hf-timestd": "4" * 40})
        with tempfile.TemporaryDirectory() as tmp:
            steps = [
                align_apply.Step("sigmond", "moved", "11111111 -> 22222222", 100),
                align_apply.Step("hf-timestd", "left", align.AHEAD_NOTE),
            ]
            aligned_path = Path(tmp) / "aligned.json"
            align_apply.record(release, steps,
                               manifest_path=Path(tmp) / "manifest.txt",
                               aligned_path=aligned_path,
                               history=lambda e: None, now=lambda: "t")
            data = json.loads(aligned_path.read_text())
            self.assertEqual(data["left"], {"hf-timestd": align.AHEAD_NOTE})
            self.assertEqual(data["components"], release.components)

    # --- Final review: I5 / I6 ---

    def _rec(self, tmp, steps, manifest_text="m\n", history=None):
        release = align.Release(tag="v3.53", manifest_text=manifest_text, appliance_commit=C,
                                components={"sigmond": "2" * 40})
        mp, ap = Path(tmp) / "manifest.txt", Path(tmp) / "aligned.json"
        align_apply.record(release, steps, manifest_path=mp, aligned_path=ap,
                           history=history or (lambda e: None), now=lambda: "t")
        return mp, ap

    def test_a_refused_step_writes_neither_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp, ap = self._rec(tmp, [align_apply.Step("sigmond", "moved", "a -> b"),
                                     align_apply.Step("hf-timestd", "refused", "dirty")])
            self.assertFalse(mp.exists())
            self.assertFalse(ap.exists())

    def test_a_skipped_step_alone_writes_neither_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp, ap = self._rec(tmp, [align_apply.Step("sigmond", "moved", "a -> b"),
                                     align_apply.Step("hf-timestd", "skipped", "byte budget reached")])
            self.assertFalse(mp.exists())
            self.assertFalse(ap.exists())

    def test_an_unchanged_manifest_leaves_prev_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp = Path(tmp) / "manifest.txt"
            prev = Path(tmp) / "manifest.txt.prev"
            mp.write_text("m\n")
            prev.write_text("older\n")
            self._rec(tmp, [align_apply.Step("sigmond", "current")], manifest_text="m\n")
            self.assertEqual(mp.read_text(), "m\n")
            self.assertEqual(prev.read_text(), "older\n")

    def test_an_unchanged_manifest_creates_no_prev(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp = Path(tmp) / "manifest.txt"
            mp.write_text("m\n")
            self._rec(tmp, [align_apply.Step("sigmond", "current")], manifest_text="m\n")
            self.assertFalse((Path(tmp) / "manifest.txt.prev").exists())

    def test_record_history_writes_the_same_lines_record_does_and_nothing_else(self):
        release = align.Release(tag="v3.53", manifest_text="m\n", appliance_commit=C,
                                components={"sigmond": "2" * 40})
        steps = [align_apply.Step("sigmond", "moved", "11111111 -> 22222222", 100)]
        via_record, via_history = [], []
        with tempfile.TemporaryDirectory() as tmp:
            align_apply.record(release, steps, manifest_path=Path(tmp) / "m",
                               aligned_path=Path(tmp) / "a", history=via_record.append,
                               now=lambda: "t")
        with tempfile.TemporaryDirectory() as tmp:
            align_apply.record_history(release, steps, history=via_history.append,
                                       now=lambda: "t")
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertEqual(via_history, via_record)
        self.assertEqual(len(via_history), 1)

    def test_record_never_names_the_version_file(self):
        # ALIGNED_RECORD is aligned.json, a separate record; record() must
        # never touch /etc/sigmond-appliance/version — check by construction,
        # not just by behaviour: no path in its source ever names it.
        src = inspect.getsource(align_apply.record)
        self.assertNotIn("version", src.lower())


class _FakeHTTPResponse:
    """A urlopen(...) context-manager stand-in carrying a status and body."""
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RefreshImageFileTests(unittest.TestCase):
    """align_apply.refresh_image_file — the one function in this module that
    writes a plain file rather than running git; Task 6 of Plan 2b."""

    def test_200_writes_mode_0o755_and_removes_the_tmp_file(self):
        body = b"#!/bin/sh\necho hi\n"
        seen = []

        def urlopen(url, timeout=None):
            seen.append((url, timeout))
            return _FakeHTTPResponse(200, body)

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sigmond-site-timing"
            with mock.patch("os.chown") as m_chown:
                step = align_apply.refresh_image_file(
                    "v3.53", "sigmond-site-timing", dest, urlopen=urlopen)
            self.assertEqual(step.outcome, "refreshed")
            self.assertEqual(step.component, str(dest))
            self.assertEqual(dest.read_bytes(), body)
            self.assertEqual(dest.stat().st_mode & 0o777, 0o755)
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])
            m_chown.assert_called_once()
            self.assertEqual(m_chown.call_args.args[1:], (0, 0))
        self.assertEqual(seen, [(f"{align.RAW_BASE}/v3.53/sigmond-site-timing", 20.0)])

    def test_404_is_failed_and_dest_is_untouched(self):
        def urlopen(url, timeout=None):
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sigmond-site-timing"
            step = align_apply.refresh_image_file(
                "v3.53", "sigmond-site-timing", dest, urlopen=urlopen)
            self.assertEqual(step.outcome, "failed")
            self.assertFalse(dest.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    # --- Fix round 1 / I2: a 200 body that isn't a script must never be
    # installed root:root 0755 at a path cron/systemd will execute. ---

    def test_non_script_body_is_refused_and_dest_untouched(self):
        html = b"<html><body>404 rate limited</body></html>\n"

        def urlopen(url, timeout=None):
            return _FakeHTTPResponse(200, html)

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sigmond-site-timing"
            with mock.patch("os.chown") as m_chown:
                step = align_apply.refresh_image_file(
                    "v3.53", "sigmond-site-timing", dest, urlopen=urlopen)
            self.assertEqual(step.outcome, "failed")
            self.assertIn("not a script", step.detail)
            self.assertFalse(dest.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])
            m_chown.assert_not_called()

    def test_leading_whitespace_before_shebang_is_still_accepted(self):
        # lstrip only — a script fetched through a proxy that prepends a
        # blank line is still a script, not "not a script".
        body = b"\n#!/bin/sh\necho hi\n"

        def urlopen(url, timeout=None):
            return _FakeHTTPResponse(200, body)

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sigmond-site-timing"
            with mock.patch("os.chown"):
                step = align_apply.refresh_image_file(
                    "v3.53", "sigmond-site-timing", dest, urlopen=urlopen)
            self.assertEqual(step.outcome, "refreshed")

    # --- Fix round 1 / M3: a write-phase failure after the tmp file was
    # created must not leave it behind at the real /usr/local/sbin path. ---

    def test_replace_failure_removes_tmp_and_reports_failed(self):
        body = b"#!/bin/sh\necho hi\n"

        def urlopen(url, timeout=None):
            return _FakeHTTPResponse(200, body)

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sigmond-site-timing"
            with mock.patch("os.chown"), \
                 mock.patch("os.replace", side_effect=OSError("cross-device link")):
                step = align_apply.refresh_image_file(
                    "v3.53", "sigmond-site-timing", dest, urlopen=urlopen)
            self.assertEqual(step.outcome, "failed")
            self.assertFalse(dest.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()


class RecordLiveTests(unittest.TestCase):
    """Task 7: record_live merges {"live": …} into this run's aligned.json."""

    def _aligned(self, tmp):
        release = align.Release(tag="v3.53", manifest_text="m\n", appliance_commit=C,
                                components={"sigmond": "2" * 40, "hf-timestd": "4" * 40})
        ap = Path(tmp) / "aligned.json"
        align_apply.record(release, [align_apply.Step("sigmond", "moved", "a -> b"),
                                     align_apply.Step("hf-timestd", "left", align.AHEAD_NOTE)],
                           manifest_path=Path(tmp) / "manifest.txt", aligned_path=ap,
                           history=lambda e: None, now=lambda: "2026-09-24T12:00:00Z")
        return release, ap

    def test_merges_live_keeping_components_and_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            release, ap = self._aligned(tmp)
            live = {"at": "2026-09-24T12:05:00Z", "restarted": ["hf-timestd"],
                    "units_stable": True, "authority_fresh": None,
                    "heartbeat_sent": True, "notes": []}
            align_apply.record_live(ap, live)
            data = json.loads(ap.read_text())
            self.assertEqual(data["live"], live)
            self.assertEqual(data["components"], release.components)
            self.assertEqual(data["left"], {"hf-timestd": align.AHEAD_NOTE})
            self.assertEqual(data["at"], "2026-09-24T12:00:00Z")
            self.assertEqual(data["release"], "v3.53")
            self.assertFalse((Path(tmp) / "aligned.json.tmp").exists())

    def test_absent_aligned_json_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ap = Path(tmp) / "aligned.json"
            align_apply.record_live(ap, {"at": "t"})
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_replaces_an_earlier_live_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, ap = self._aligned(tmp)
            align_apply.record_live(ap, {"at": "one"})
            align_apply.record_live(ap, {"at": "two"})
            self.assertEqual(json.loads(ap.read_text())["live"], {"at": "two"})
