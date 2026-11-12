"""`smd align` fixes from the first bare-metal --apply (KJ6MKI, 2026-11-11):
missing components install only on opt-in, a build preflight for Python.h,
forced tag fetches, and a hint for foreign-owned .git files."""
import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from sigmond import align, align_apply, build_preflight

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_kj6mki", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_kj6mki", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class MissingDecision(unittest.TestCase):
    def test_default_skips_with_the_flag_named(self):
        install, why = align.missing_decision("codar-sounder", enabled=[], install_missing=False)
        self.assertFalse(install)
        self.assertEqual(why, "would skip: not on this station (use --install-missing)")

    def test_flag_installs(self):
        install, why = align.missing_decision("codar-sounder", enabled=[], install_missing=True)
        self.assertTrue(install)
        self.assertIn("would install", why)

    def test_enabled_in_topology_installs(self):
        install, why = align.missing_decision("hf-timestd", enabled=["hf-timestd"],
                                              install_missing=False)
        self.assertTrue(install)
        self.assertIn("enabled in topology", why)

    def test_sigmond_rac_never_implicitly(self):
        install, why = align.missing_decision("sigmond-rac", enabled=[], install_missing=True)
        self.assertFalse(install)
        self.assertIn("never installed implicitly", why)
        self.assertTrue(align.missing_decision("sigmond-rac", enabled=["sigmond-rac"],
                                               install_missing=False)[0])


class MissingNotes(unittest.TestCase):
    def _plan(self):
        return [align.Item("psk-recorder", "current", "a" * 8, "a" * 8),
                align.Item("codar-sounder", "missing", None, "b" * 40, "no checkout"),
                align.Item("hf-timestd", "missing", None, "c" * 40, "no checkout"),
                align.Item("sigmond-rac", "missing", None, "d" * 40, "no checkout")]

    def test_apply_and_dry_run_share_the_decision(self):
        with mock.patch.object(smd, "_load_topology", return_value={}), \
             mock.patch.object(smd, "_enabled_components", return_value=["hf-timestd"]):
            plan, skip = smd._align_missing_notes(self._plan(), NS(install_missing=False))
        self.assertEqual(skip, {"codar-sounder", "sigmond-rac"})
        notes = {it.component: it.note for it in plan}
        self.assertIn("would install (enabled in topology", notes["hf-timestd"])
        self.assertIn("use --install-missing", notes["codar-sounder"])
        self.assertEqual(notes["psk-recorder"], "")

    def test_flag_installs_all_but_sigmond_rac(self):
        with mock.patch.object(smd, "_load_topology", return_value={}), \
             mock.patch.object(smd, "_enabled_components", return_value=[]):
            _, skip = smd._align_missing_notes(self._plan(), NS(install_missing=True))
        self.assertEqual(skip, {"sigmond-rac"})

    def test_parser_knows_the_flag(self):
        self.assertIn("'--install-missing'", (REPO / "bin" / "smd").read_text())


class BuildPreflight(unittest.TestCase):
    def _repo(self, deps='"systemd-python>=235",', pyver="3.11"):
        d = Path(tempfile.mkdtemp()) / "hf-timestd"
        d.mkdir()
        (d / "pyproject.toml").write_text(
            f'[project]\nrequires-python = ">=3.10"\ndependencies = [\n    {deps}\n]\n')
        if pyver:
            (d / ".python-version").write_text(pyver + "\n")
        return d

    def _run(self, missing):
        def run(argv, **kw):
            out = "3.11\n" + ("/usr/include/python3.11/Python.h\n" if missing else "\n")
            return subprocess.CompletedProcess(argv, 0, out, "")
        return run

    def test_missing_headers_name_the_package(self):
        why = build_preflight.check(self._repo(), run=self._run(True),
                                    which=lambda n: f"/usr/bin/{n}")
        self.assertIn("systemd-python", why)
        self.assertIn("sudo apt install python3.11-dev", why)
        self.assertIn("/usr/bin/python3.11", why)

    def test_headers_present_passes(self):
        self.assertIsNone(build_preflight.check(self._repo(), run=self._run(False),
                                                which=lambda n: f"/usr/bin/{n}"))

    def test_no_source_only_dep_is_not_checked(self):
        run = mock.Mock()
        self.assertIsNone(build_preflight.check(self._repo(deps='"numpy",'), run=run,
                                                which=lambda n: f"/usr/bin/{n}"))
        run.assert_not_called()

    def test_no_such_interpreter_means_uv_brings_one(self):
        run = mock.Mock()
        self.assertIsNone(build_preflight.check(self._repo(), run=run, which=lambda n: None))
        run.assert_not_called()

    def test_existing_venv_python_is_the_one_checked(self):
        repo = self._repo()
        (repo / "venv" / "bin").mkdir(parents=True)
        (repo / "venv" / "bin" / "python").write_text("")
        self.assertEqual(build_preflight.interpreter_for(repo, which=lambda n: None),
                         str(repo / "venv" / "bin" / "python"))

    def test_run_install_refuses_before_install_sh(self):
        repo = self._repo()
        (repo / "install.sh").write_text("#!/bin/bash\nexit 0\n")
        with mock.patch.object(build_preflight, "check", return_value="needs python3.11-dev"), \
             mock.patch.object(smd.subprocess, "run") as run:
            rc = smd._align_run_install(repo)
        self.assertEqual(rc, smd.ALIGN_PREFLIGHT_FAILED)
        run.assert_not_called()


class ForcedTagFetch(unittest.TestCase):
    def test_align_fetch_forces_tags(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        align_apply.fetch("/x", "o", run=run, as_owner=lambda o, a: a)
        self.assertIn("--force", calls[0])
        self.assertIn("--tags", calls[0])

    def test_native_source_fetches_force_tags(self):
        src = (REPO / "bin" / "smd").read_text()
        self.assertEqual(src.count("'fetch', '--quiet', '--tags', '--force', 'origin'"), 3)
        self.assertNotIn("_native_git(src, 'fetch', '--quiet', 'origin')", src)


class ForeignGitFiles(unittest.TestCase):
    def test_no_foreign_files_no_hint(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / ".git" / "objects").mkdir(parents=True)
            (Path(t) / ".git" / "HEAD").write_text("x")
            self.assertEqual(align_apply.foreign_git_files_hint(t), "")

    def test_foreign_file_named_with_repair(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / ".git").mkdir()
            f = Path(t) / ".git" / "HEAD"
            f.write_text("x")
            real = Path.lstat

            def fake(self):
                st = real(self)
                if self == f:
                    return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                                           st.st_uid + 1, st.st_gid, st.st_size,
                                           st.st_atime, st.st_mtime, st.st_ctime))
                return st
            with mock.patch.object(Path, "lstat", fake):
                hint = align_apply.foreign_git_files_hint(t)
        self.assertIn(".git/HEAD", hint)
        self.assertIn(f"chown -R --reference={t} {t}/.git", hint)


class RestartsUnknown(unittest.TestCase):
    def test_no_services_while_moving_says_unknown(self):
        out = []
        with mock.patch.object(smd, "_align_services", return_value={}), \
             mock.patch.object(smd, "_align_staleness", return_value={
                 "stale": {}, "running": set(), "consumes": {}, "radiod_consumers": set()}):
            n = smd._align_print_restarts("/b", ["psk-recorder"], say=out.append)
        self.assertEqual(n, 1)
        self.assertTrue(any("unknown" in line for line in out), out)

    def test_moving_hs_uploader_with_its_daemon_up_is_predicted(self):
        out = []
        with mock.patch.object(smd, "_align_services", return_value={"psk-recorder": ["u"]}), \
             mock.patch.object(smd, "_align_staleness", return_value={
                 "stale": {}, "running": {"psk-recorder"}, "consumes": {},
                 "radiod_consumers": set()}), \
             mock.patch.object(smd, "_align_units_started_at", return_value=1.0):
            smd._align_print_restarts("/b", ["hs-uploader"], say=out.append)
        self.assertTrue(any(l.strip().startswith("hs-uploader") for l in out), out)


if __name__ == "__main__":
    unittest.main()


# ── a blocked run names what still runs old code (KJ6MKI rerun, 2026-11-12) ──

def test_blocked_run_names_stale_services_and_the_restart_command(monkeypatch):
    from sigmond import align_apply
    mod = smd
    stale = {"psk-recorder": "moved", "wspr-recorder": "moved", "ka9q-web": "moved"}
    monkeypatch.setattr(mod, "_align_services", lambda *a, **k: {})
    monkeypatch.setattr(mod, "_align_staleness", lambda *a, **k: {
        "stale": stale, "running": set(stale), "radiod_consumers": []})
    out = []
    steps = [align_apply.Step("hamsci-physics", "failed", "build exit 127"),
             align_apply.Step("psk-recorder", "moved", "")]
    names = mod._align_report_stale_after_failure(steps, base="/x", catalog=None,
                                                  say=out.append)
    assert names == ["ka9q-web", "psk-recorder", "wspr-recorder"]
    text = "\n".join(out)
    assert "hamsci-physics failed" in text
    assert "smd restart ka9q-web psk-recorder wspr-recorder" in text


def test_blocked_run_with_nothing_stale_says_so(monkeypatch):
    from sigmond import align_apply
    mod = smd
    monkeypatch.setattr(mod, "_align_services", lambda *a, **k: {})
    monkeypatch.setattr(mod, "_align_staleness", lambda *a, **k: {
        "stale": {}, "running": set(), "radiod_consumers": []})
    out = []
    assert mod._align_report_stale_after_failure(
        [align_apply.Step("hf-timestd", "failed", "")], base="/x", catalog=None,
        say=out.append) == []
    assert "no service is running old code" in out[0]


def test_make_live_reports_stale_before_returning_on_a_block():
    src = (Path(__file__).resolve().parent.parent / "bin" / "smd").read_text()
    i = src.index("def _align_make_live(")
    body = src[i:src.index("\ndef ", i + 10)]
    block = body[body.index("if any(s.outcome in _ALIGN_BLOCKING for s in move_steps):"):]
    block = block[:block.index("return 1")]
    assert "_align_report_stale_after_failure(" in block
