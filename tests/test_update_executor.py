"""sigmond#48 — `smd update --apply` must not run install after a failed
pull, must show WHY install.sh failed (and keep the full output), and must
bound its network phase.  Real git repos under a scratch `--base`, a fake
install.sh that records its invocation; `runuser` (root-only) is shimmed
away so the privilege drop does not make the executor untestable."""
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
        "smd_under_test_update_exec", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_update_exec", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()
_REAL_RUN = subprocess.run


def _run_without_runuser(cmd, *a, **kw):
    """Tests are not root: `runuser -u X -- git ...` would be refused.
    Strip the privilege-drop prefix and run the git command as ourselves."""
    if isinstance(cmd, list) and cmd[:1] == ["runuser"] and "--" in cmd:
        cmd = cmd[cmd.index("--") + 1:]
    return _REAL_RUN(cmd, *a, **kw)


def _git(*args, cwd):
    return _REAL_RUN(["git", "-c", "user.email=t@example", "-c", "user.name=t", *args],
                     cwd=cwd, check=True, capture_output=True, text=True)


class _Rig(unittest.TestCase):
    """origin (bare) + base/<comp> checkout that is ONE commit behind, with a
    fake install.sh whose behaviour the test chooses."""

    COMP = "fakeclient"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.origin = root / "origin.git"
        _git("init", "--bare", "-b", "main", str(self.origin), cwd=root)
        seed = root / "seed"
        _git("clone", str(self.origin), str(seed), cwd=root)
        (seed / "scripts").mkdir()
        (seed / "scripts" / "install.sh").write_text("#!/bin/bash\necho install-ran > \"$(dirname \"$0\")/../.installed\"\nexit 0\n")
        (seed / "a.txt").write_text("a\n")
        _git("add", "-A", cwd=seed); _git("commit", "-m", "first", cwd=seed)
        _git("push", "-u", "origin", "main", cwd=seed)
        self.base = root / "base"; self.base.mkdir()
        self.host = self.base / self.COMP
        _git("clone", str(self.origin), str(self.host), cwd=root)
        (seed / "b.txt").write_text("b\n")
        _git("add", "-A", cwd=seed); _git("commit", "-m", "second", cwd=seed)
        _git("push", "origin", "main", cwd=seed)
        self.logdir = root / "logs"
        self._patches = [
            mock.patch.object(smd.subprocess, "run", side_effect=_run_without_runuser),
            mock.patch.object(smd, "UPDATE_LOG_DIR", self.logdir),
            mock.patch.object(smd, "_venv_sibling_skew", return_value=([], [])),
        ]
        for p in self._patches: p.start()

    def tearDown(self):
        for p in self._patches: p.stop()
        self._tmp.cleanup()

    def _set_install_sh(self, body: str):
        """Change the HOST checkout's install.sh (committed so the tree is
        clean for the pull) — the version that will run after the pull is
        the one upstream ships, so write it in seed and push."""
        # host is behind; the install.sh that runs is the pulled one, so we
        # must put it upstream.
        root = Path(self._tmp.name); seed = root / "seed"
        (seed / "scripts" / "install.sh").write_text(body)
        _git("add", "-A", cwd=seed); _git("commit", "-m", "install.sh", cwd=seed)
        _git("push", "origin", "main", cwd=seed)

    def _update(self):
        args = types.SimpleNamespace(base=self.base, apply=True, no_fetch=False)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = smd.cmd_update(args)
        return rc, out.getvalue() + err.getvalue()


class ExecutorTests(_Rig):
    def test_success_path_pulls_then_installs(self):
        rc, text = self._update()
        self.assertEqual(rc, 0, text)
        self.assertTrue((self.host / ".installed").exists(), text)

    def test_install_is_skipped_when_pull_failed(self):
        # The host KNOWS it is behind (fetched), then the remote goes away:
        # pull must fail, and install.sh must NOT run.
        _git("fetch", cwd=self.host)
        _git("remote", "set-url", "origin", "/nonexistent/origin.git", cwd=self.host)
        rc, text = self._update()
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.host / ".installed").exists(),
                         "install.sh ran after a failed pull")
        self.assertIn("install skipped", text.lower())

    def test_install_failure_shows_why_and_keeps_the_full_output(self):
        self._set_install_sh("#!/bin/bash\necho 'uv sync: ENOSPC no space left on device' >&2\nexit 1\n")
        rc, text = self._update()
        self.assertNotEqual(rc, 0)
        self.assertIn("no space left on device", text)          # the reason, on screen
        logs = list(self.logdir.glob(f"update-*{self.COMP}*.log"))
        self.assertEqual(len(logs), 1, f"expected one update log, got {logs}")
        self.assertIn("no space left on device", logs[0].read_text())
        self.assertIn(str(logs[0]), text)                          # and named


class NetworkBoundTests(unittest.TestCase):
    def test_fetch_and_pull_are_bounded(self):
        seen = []
        def fake_run(cmd, *a, **kw):
            seen.append(kw)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(smd.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(smd.os, "geteuid", return_value=1000):
            smd._git_fetch_as_owner(Path("/tmp/x"))
            smd._git_pull_as_owner(Path("/tmp/x"))
        for kw in seen:
            self.assertGreater(kw.get("timeout", 0), 0)
            env = kw.get("env") or {}
            self.assertIn("GIT_HTTP_LOW_SPEED_TIME", env)
            self.assertIn("GIT_HTTP_LOW_SPEED_LIMIT", env)
            self.assertEqual(env.get("GIT_TERMINAL_PROMPT"), "0")

    def test_timeout_becomes_a_failed_result_not_an_exception(self):
        def fake_run(cmd, *a, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))
        with mock.patch.object(smd.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(smd.os, "geteuid", return_value=1000):
            r = smd._git_fetch_as_owner(Path("/tmp/x"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("timed out", (r.stderr or "").lower())
