"""apt must WAIT for the dpkg lock, and a build that isn't needed must not
run apt at all.

⛔ Why this file exists.  W3USR-019 (Scranton DASI-019) came up on 2026-09-18
with every component running except ka9q-web, whose unit did not exist:

    E: Could not get lock /var/lib/dpkg/lock-frontend.
       It is held by process 2572 (unattended-upgr)
    E: Unable to acquire the dpkg frontend lock ...
    ✗  failed to install ka9q-radio build deps via apt
    ...
    ✗  step exited 1: /usr/local/bin/smd install --components ka9q-web --yes
    ...
    ka9q-web.service: Failed to start: Unit ka9q-web.service not found.

Two independent defects put that station on the air without a web UI, and
this file pins one regression test to each.

1.  smd's apt calls died the instant something else held the dpkg lock.  On a
    freshly-installed Debian box that something is `unattended-upgrades`,
    running on exactly the boots when first-run bring-up does its apt work.
    It is a RACE — the same log shows ka9q-web building fine minutes earlier,
    which is why this survived every lab install.

2.  `_build_ka9q_web_with_onion` installed build deps BEFORE checking whether
    a compile was needed.  The binary was already built and current (it ships
    in the golden image), so that apt run bought nothing — yet its failure
    propagated all the way out and `_write_ka9q_web_unit` never ran.

The second is what makes the first load-bearing: the unit-writing step runs
only after the build function returns True.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import os
import re
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
        "smd_under_test_apt_lock", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_apt_lock", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

_OK = subprocess.CompletedProcess([], 0, "", "")


class AptWaitsForTheLockTests(unittest.TestCase):
    """Every apt-get smd runs must carry a lock timeout."""

    def _record(self):
        calls: list[list[str]] = []

        def fake_run(cmd, **kw):
            calls.append([str(c) for c in cmd])
            return _OK

        return calls, fake_run

    def _apt_calls(self, calls):
        return [c for c in calls if any(part.endswith("apt-get") for part in c)]

    def _assert_waits(self, argv):
        joined = " ".join(argv)
        self.assertIn(
            "DPkg::Lock::Timeout", joined,
            f"apt invoked without a lock timeout, so unattended-upgrades "
            f"can kill it outright: {joined}")
        m = re.search(r"DPkg::Lock::Timeout=(\d+)", joined)
        self.assertIsNotNone(m, joined)
        self.assertGreaterEqual(
            int(m.group(1)), 60,
            "a lock timeout under a minute does not outlast an "
            "unattended-upgrades run")

    def test_radiod_build_deps_wait_for_the_lock(self):
        calls, fake_run = self._record()
        with mock.patch.object(smd, "_run", side_effect=fake_run), \
             mock.patch.object(smd, "_is_raspberry_pi", return_value=False):
            self.assertTrue(smd._install_radiod_deps())
        apt = self._apt_calls(calls)
        self.assertTrue(apt, "expected _install_radiod_deps to shell out to apt-get")
        for argv in apt:
            self._assert_waits(argv)

    def test_native_build_deps_wait_for_the_lock(self):
        calls, fake_run = self._record()
        # Force the install path: pretend the toolchain probe fails but
        # apt-get itself is present.
        def which(name):
            return "/usr/bin/apt-get" if name == "apt-get" else None

        with mock.patch.object(smd, "_run", side_effect=fake_run), \
             mock.patch.object(smd.shutil, "which", side_effect=which):
            self.assertTrue(smd._ensure_native_build_deps(label="test"))
        apt = self._apt_calls(calls)
        self.assertTrue(apt, "expected _ensure_native_build_deps to shell out to apt-get")
        for argv in apt:
            self._assert_waits(argv)

    def test_no_apt_get_call_bypasses_the_helper(self):
        """A new direct apt-get would reintroduce the bug silently.

        The helper is only a fix while everything goes through it, and
        nothing in the source tree enforces that but this check.
        """
        src = (REPO / "bin" / "smd").read_text().splitlines()
        offenders = []
        for n, line in enumerate(src, 1):
            if "'apt-get'" not in line and '"apt-get"' not in line:
                continue
            # The helper itself, and shutil.which probes / advice strings.
            if "shutil.which" in line or "_apt(" in line:
                continue
            offenders.append(f"{n}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "these call apt-get directly instead of via _apt(), so they do "
            "not wait for the dpkg lock:\n  " + "\n  ".join(offenders))


class Ka9qWebSkipsUnneededBuildTests(unittest.TestCase):
    """An up-to-date ka9q-web must reach the unit writer without apt."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ka9q-web-build-"))
        self.src = self._tmp / "src"
        self.src.mkdir()
        # The image ships both: the binary installed under /usr/local/sbin and
        # the leftover build artefact in the source tree.  Identical content
        # is what "already current" means.
        payload = b"\x7fELF ka9q-web pretend binary"
        self.built = self.src / "ka9q-web"
        self.built.write_bytes(payload)
        self.installed = self._tmp / "usr-local-sbin-ka9q-web"
        self.installed.write_bytes(payload)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _patches(self, deps_ok: bool):
        """Patch the world so only the decision under test can vary."""
        self.dep_calls: list[str] = []

        def native(label="build"):
            self.dep_calls.append(f"native:{label}")
            return deps_ok

        def radiod():
            self.dep_calls.append("radiod-deps")
            return deps_ok

        return [
            mock.patch.object(smd, "_KA9Q_WEB_BIN", self.installed),
            mock.patch.object(smd, "_source_advanced_past_build", return_value=False),
            mock.patch.object(smd, "_ensure_native_build_deps", side_effect=native),
            mock.patch.object(smd, "_install_radiod_deps", side_effect=radiod),
            mock.patch.object(smd, "_write_build_manifest", return_value=None),
            mock.patch.object(smd, "_git_remote_url", return_value="https://example/ka9q-web"),
            mock.patch.object(smd, "_git_head", return_value="0" * 40),
        ]

    def test_current_binary_needs_no_apt_and_still_succeeds(self):
        """The W3USR-019 case: deps unavailable, but nothing needs building.

        `deps_ok=False` stands in for the held dpkg lock.  Before the fix the
        dep install ran first and its failure returned False from here, which
        is what stopped `_write_ka9q_web_unit` from ever running.
        """
        patches = self._patches(deps_ok=False)
        with mock.patch.object(smd, "_run", return_value=_OK) as run:
            for p in patches:
                p.start()
            try:
                ok = smd._build_ka9q_web_with_onion(self.src)
            finally:
                for p in reversed(patches):
                    p.stop()

        self.assertTrue(
            ok,
            "an already-current ka9q-web reported failure, so the caller "
            "never installs ka9q-web.service and the station has no web UI")
        self.assertEqual(
            self.dep_calls, [],
            f"build deps were installed for a build that never happens: "
            f"{self.dep_calls}")
        for call in run.call_args_list:
            argv = [str(c) for c in call.args[0]]
            self.assertNotIn(
                "make", argv[:1],
                f"compiled despite an up-to-date binary: {argv}")

    def test_stale_binary_still_installs_deps_and_builds(self):
        """The fix must not turn the skip into a blanket refusal to build."""
        self.installed.write_bytes(b"an older build")   # shas now differ
        patches = self._patches(deps_ok=True)
        ran: list[list[str]] = []

        def fake_run(cmd, **kw):
            ran.append([str(c) for c in cmd])
            return _OK

        with mock.patch.object(smd, "_run", side_effect=fake_run), \
             mock.patch.object(smd, "_build_onion", return_value=True), \
             mock.patch.object(smd, "_ONION_SRC", self._tmp / "onion-src"):
            for p in patches:
                p.start()
            try:
                (self._tmp / "onion-src").mkdir()
                ok = smd._build_ka9q_web_with_onion(self.src)
            finally:
                for p in reversed(patches):
                    p.stop()

        self.assertTrue(ok)
        self.assertIn("radiod-deps", self.dep_calls)
        self.assertTrue(
            any(argv[:1] == ["make"] for argv in ran),
            f"a stale binary was not rebuilt: {ran}")

    def test_missing_binary_is_built(self):
        """Greenfield: nothing installed yet, so deps and a compile are due."""
        self.installed.unlink()
        patches = self._patches(deps_ok=True)
        ran: list[list[str]] = []

        def fake_run(cmd, **kw):
            ran.append([str(c) for c in cmd])
            return _OK

        with mock.patch.object(smd, "_run", side_effect=fake_run), \
             mock.patch.object(smd, "_build_onion", return_value=True), \
             mock.patch.object(smd, "_ONION_SRC", self._tmp / "onion-src"):
            for p in patches:
                p.start()
            try:
                (self._tmp / "onion-src").mkdir()
                ok = smd._build_ka9q_web_with_onion(self.src)
            finally:
                for p in reversed(patches):
                    p.stop()

        self.assertTrue(ok)
        self.assertIn("radiod-deps", self.dep_calls)
        self.assertTrue(any(argv[:1] == ["make"] for argv in ran))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
