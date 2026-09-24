"""Tests for sigmond.align_apply — git is faked; nothing touches a real checkout."""
import tempfile
import types
import unittest
from pathlib import Path

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
        while i < len(argv) and argv[i] in ("-c", "-C"):
            i += 2
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


class NormalizeRepoTests(unittest.TestCase):
    def test_ssh_and_https_forms_are_equal(self):
        self.assertEqual(
            align_apply.normalize_repo("git@github.com:HamSCI/sigmond.git"),
            align_apply.normalize_repo("https://github.com/hamsci/sigmond/"))


if __name__ == "__main__":
    unittest.main()
