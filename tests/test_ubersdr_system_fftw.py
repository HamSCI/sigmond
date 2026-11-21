"""2026-11-21: the UberSDR decoders build against the system FFTW, never a
hand-built one in /usr/local.

KPH1 and KPH2 carry FFTW 3.3.11 in /usr/local (for their radiod).  UberSDR's
CMakeLists asks pkg-config for fftw3f, pkg-config searches /usr/local first,
and the static libfftw3f.a it then links came from 3.3.11.  WSJT-X's stored
outputs were made with the system's 3.3.10, so the rebuild of 8719788 failed
parity there (wsprd 19 of 175 cases, jt9 3 of 589) and was not installed,
while the same commit passes on N8UR, which has only the system FFTW.
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_system_fftw", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_system_fftw", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


class SystemFftwLookupTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_finds_the_multiarch_library_and_ignores_usr_local(self):
        lib = _touch(self.root / "usr/lib/x86_64-linux-gnu/libfftw3f.a")
        _touch(self.root / "usr/local/lib/libfftw3f.a")
        found = smd._system_fftw3f(root=self.root)
        self.assertIsNotNone(found)
        pkgconfig_libdir, static_lib = found
        self.assertEqual(static_lib, str(lib))
        self.assertEqual(
            pkgconfig_libdir.split(":"),
            [str(self.root / "usr/lib/x86_64-linux-gnu/pkgconfig"),
             str(self.root / "usr/share/pkgconfig")])
        self.assertNotIn("/usr/local", pkgconfig_libdir)

    def test_none_without_a_system_static_library(self):
        _touch(self.root / "usr/local/lib/libfftw3f.a")
        self.assertIsNone(smd._system_fftw3f(root=self.root))


class ConfigureUsesSystemFftwTests(unittest.TestCase):

    def _configure_argv(self, system):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "ubersdr_wsprd"
        src.mkdir()
        dest = Path(tmp.name) / "decoders"
        calls = []

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            # Stop at the compile: only the configure step matters here.
            rc = 1 if "--build" in cmd else 0
            return subprocess.CompletedProcess(cmd, rc, "", "stop here")

        with contextlib.ExitStack() as st:
            for p in (
                    mock.patch.object(smd, "_UBERSDR_SRC", src),
                    mock.patch.object(smd, "_DECODER_DIR", dest),
                    mock.patch.object(smd, "_native_pin",
                                      return_value="f" * 40),
                    mock.patch.object(smd, "_git_head", return_value="f" * 40),
                    mock.patch.object(smd, "_ensure_native_build_deps",
                                      return_value=True),
                    mock.patch.object(smd, "_apt",
                                      return_value=subprocess.CompletedProcess(
                                          [], 0, "", "")),
                    mock.patch.object(smd, "_decoder_x86_arch",
                                      return_value="x86-64-v3"),
                    mock.patch.object(smd, "_system_fftw3f",
                                      return_value=system),
                    mock.patch.object(smd, "_run", side_effect=fake_run)):
                st.enter_context(p)
            st.enter_context(contextlib.redirect_stdout(io.StringIO()))
            st.enter_context(contextlib.redirect_stderr(io.StringIO()))
            self.assertFalse(smd._build_ubersdr_decoders())
        configure = [c for c in calls if "cmake" in c and "-S" in c]
        self.assertEqual(len(configure), 1, calls)
        return configure[0]

    def test_configure_names_the_system_fftw(self):
        argv = self._configure_argv(
            ("/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/share/pkgconfig",
             "/usr/lib/x86_64-linux-gnu/libfftw3f.a"))
        # sudo resets the environment, so the setting rides on the argv.
        self.assertEqual(argv[:2], [
            "env",
            "PKG_CONFIG_LIBDIR=/usr/lib/x86_64-linux-gnu/pkgconfig:"
            "/usr/share/pkgconfig"])
        self.assertIn(
            "-DFFTW3F_STATIC_LIBRARY=/usr/lib/x86_64-linux-gnu/libfftw3f.a",
            argv)

    def test_configure_unchanged_without_a_system_fftw(self):
        argv = self._configure_argv(None)
        self.assertEqual(argv[0], "cmake")
        self.assertFalse(any(a.startswith("-DFFTW3F_STATIC_LIBRARY")
                             for a in argv))


if __name__ == "__main__":
    unittest.main()
