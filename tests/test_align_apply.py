"""Tests for sigmond.align_apply — git is faked; nothing touches a real checkout."""
import types
import unittest

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


if __name__ == "__main__":
    unittest.main()
