"""The ka9q-web builder records only what a real `make` built.

2026-11-19: a fleet align moved ka9q-web's checkout to 4084060, but the
installed binary stayed old.  The builder rewrote its build record on the
path that built nothing, so the record claimed the new commit and later
staleness checks read it as current.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_ka9q_web_record", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_ka9q_web_record", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()
_REAL_RUN = subprocess.run


def _git(*args, cwd):
    return _REAL_RUN(["git", "-c", "user.email=t@example", "-c", "user.name=t",
                      *args], cwd=cwd, check=True, capture_output=True, text=True)


def _cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class Ka9qWebBuildRecordTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ka9q-web-record-"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.src = self._tmp / "src"
        self.src.mkdir()
        _git("init", "-q", "-b", "main", cwd=self.src)
        (self.src / "a").write_text("a\n")
        _git("add", "-A", cwd=self.src)
        _git("commit", "-qm", "one", cwd=self.src)
        self.a = _git("rev-parse", "HEAD", cwd=self.src).stdout.strip()
        (self.src / "b").write_text("b\n")
        _git("add", "-A", cwd=self.src)
        _git("commit", "-qm", "two", cwd=self.src)
        self.b = _git("rev-parse", "HEAD", cwd=self.src).stdout.strip()
        payload = b"\x7fELF ka9q-web pretend binary"
        (self.src / "ka9q-web").write_bytes(payload)
        self.installed = self._tmp / "installed-ka9q-web"
        self.installed.write_bytes(payload)
        self.manifests = self._tmp / "manifests"
        self.manifests.mkdir()
        self.record = self.manifests / "ka9q-web.toml"
        self.onion = self._tmp / "onion-src"
        self.onion.mkdir()
        self.ran: list[list[str]] = []
        self.make_rc = 0

    def _write_record(self, sha):
        self.record.write_text(
            'component = "ka9q-web"\n\n[[upstream]]\nname = "ka9q-web"\n'
            f'url  = "u"\nref  = "master"\npin  = ""\nsha  = "{sha}"\n')

    def _fake_run(self, cmd, **kw):
        cmd = [str(c) for c in cmd]
        if cmd[:1] == ["sudo"]:
            cmd = cmd[1:]
        self.ran.append(cmd)
        if cmd[:1] == ["make"]:
            return _cp(self.make_rc if cmd[1:2] != ["install"] else 0,
                       err="boom" if self.make_rc else "")
        if cmd[:1] == ["install"]:       # the manifest writer's root install
            shutil.copy(cmd[3], cmd[4])
            return _cp()
        if cmd[:1] == ["mkdir"]:
            return _cp()
        return _REAL_RUN(cmd, capture_output=True, text=True, **{
            k: v for k, v in kw.items() if k == "cwd"})

    def _call(self):
        with mock.patch.object(smd, "_run", side_effect=self._fake_run), \
             mock.patch.object(smd, "_KA9Q_WEB_BIN", self.installed), \
             mock.patch.object(smd, "_BUILD_MANIFEST_DIR", self.manifests), \
             mock.patch.object(smd, "_ONION_SRC", self.onion), \
             mock.patch.object(smd, "_build_onion", return_value=True), \
             mock.patch.object(smd, "_ensure_native_build_deps", return_value=True), \
             mock.patch.object(smd, "_install_radiod_deps", return_value=True), \
             mock.patch.object(smd, "_native_pin", return_value=""):
            return smd._build_ka9q_web_with_onion(self.src)

    def _make_argvs(self):
        return [a for a in self.ran
                if a[:1] == ["make"] and a[1:2] != ["--version"]]

    def test_no_rebuild_writes_no_record(self):
        self._write_record(self.b)
        before = self.record.read_bytes()
        self.assertTrue(self._call())
        self.assertEqual(self._make_argvs(), [])
        self.assertEqual(self.record.read_bytes(), before)

    def test_record_behind_head_rebuilds(self):
        self._write_record(self.a)
        self.assertTrue(self._call())
        makes = [a[1] for a in self._make_argvs()]
        self.assertEqual(makes[0], "clean")
        self.assertTrue(makes[1].startswith("-j"), makes)
        self.assertEqual(self._recorded_sha(), self.b)

    def test_failed_make_keeps_record(self):
        self._write_record(self.a)
        self.make_rc = 2
        self.assertFalse(self._call())
        self.assertEqual(self._recorded_sha(), self.a)

    def _recorded_sha(self):
        with mock.patch.object(smd, "_BUILD_MANIFEST_DIR", self.manifests):
            return smd._manifest_source_sha("ka9q-web")


if __name__ == "__main__":
    unittest.main()
