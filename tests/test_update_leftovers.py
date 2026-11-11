"""Two things `smd update` tripped over on AI6VN-1 (2026-11-11): a radiod build
left ka9q-radio's generated docs modified (so update HELD ka9q-radio), and
install.sh asked the fresh-install Proxmox question during an update."""
import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_leftovers", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_leftovers", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class GeneratedDocsRestored(unittest.TestCase):
    def test_only_the_generated_docs_are_put_back(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            g = ["git", "-C", t, "-c", "user.email=t@t", "-c", "user.name=t"]
            subprocess.run(["git", "-C", t, "init", "-q"], check=True)
            (d / "docs").mkdir()
            for f in ("docs/PREVIEW.html", "docs/PREVIEW.txt", "src.c"):
                (d / f).write_text("orig\n")
            subprocess.run(g + ["add", "."], check=True)
            subprocess.run(g + ["commit", "-qm", "x"], check=True)
            for f in ("docs/PREVIEW.html", "docs/PREVIEW.txt", "src.c"):
                (d / f).write_text("rebuilt\n")
            smd._restore_ka9q_radio_generated_docs(d)
            self.assertEqual((d / "docs/PREVIEW.html").read_text(), "orig\n")
            self.assertEqual((d / "docs/PREVIEW.txt").read_text(), "orig\n")
            self.assertEqual((d / "src.c").read_text(), "rebuilt\n")   # a real edit stays

    def test_called_after_make_install(self):
        src = (REPO / "bin" / "smd").read_text()
        i = src.index("def _build_ka9q_radio(")
        body = src[i:src.index("\ndef ", i + 10)]
        self.assertLess(body.index("'make', 'install'"),
                        body.index("_restore_ka9q_radio_generated_docs(src_dir)"))


class NoProxmoxQuestionOnUpdate(unittest.TestCase):
    def test_existing_install_is_not_asked(self):
        s = (REPO / "install.sh").read_text()
        i = s.index('Run Proxmox passthrough setup first?')
        cond = s[s.rindex('if [[ "$_virt" == "kvm"', 0, i):i]
        self.assertIn("! -f /etc/sigmond/coordination.toml", cond)


if __name__ == "__main__":
    unittest.main()
