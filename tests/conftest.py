"""Shared test path bootstrap.

Tests import `sigmond` without requiring the package to be installed.
This mirrors what bin/smd does at runtime.
"""

import os
import sys
from pathlib import Path

# bin/smd re-execs into <repo>/venv/bin/python at module scope (install.sh's
# layout, distinct from this checkout's dev .venv/).  Eighteen test files load
# bin/smd with exec(); without this guard the FIRST of them replaces the
# running pytest process via os.execv(), which does not look like a failure --
# it looks like the suite ending early.  Each of those files sets this itself;
# setting it here once makes it true for any file added later, and setdefault
# leaves a caller's own override alone.
os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
# A bring-up that ends with rc 0 rewrites the first-run marker it finds
# (bin/smd _settle_firstrun_marker).  Point it at a path that never exists,
# so a test of `smd bringup` cannot touch the marker of the host running it.
os.environ.setdefault("SIGMOND_FIRSTRUN_MARKER", "/nonexistent/sigmond-test/.firstrun-bringup-done")

# No elevation from the suite (Task 20 incident, 2026-10-23: a test ran the
# real `bin/smd sink fill`; `_need_root` re-executed it through passwordless
# sudo and it wrote this host's /etc).  Unless SIGMOND_TEST_ALLOW_SUDO=1,
# a `sudo` that refuses and exits 1 stands first on PATH for this process
# and every child it starts (bin/smd run as a subprocess finds it through
# shutil.which), and the autouse fixture below makes an in-process exec of
# sudo raise.  Tests that need the host's real sudo carry
# `needs_real_sudo` and skip while the guard stands.
ALLOW_SUDO = os.environ.get("SIGMOND_TEST_ALLOW_SUDO") == "1"
if not ALLOW_SUDO:
    import atexit
    import shutil as _shutil
    import tempfile as _tempfile
    _NOSUDO = _tempfile.mkdtemp(prefix="sigmond-test-nosudo-")
    atexit.register(_shutil.rmtree, _NOSUDO, True)
    with open(os.path.join(_NOSUDO, "sudo"), "w") as _f:
        _f.write("#!/bin/sh\n"
                 "echo \"sudo refused under pytest (tests/conftest.py): $*\" >&2\n"
                 "exit 1\n")
    os.chmod(os.path.join(_NOSUDO, "sudo"), 0o755)
    os.environ["PATH"] = _NOSUDO + os.pathsep + os.environ.get("PATH", "")

_LIB = Path(__file__).resolve().parent.parent / 'lib'
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


def _tui_skip_banner():
    """Print a loud stderr warning when textual is absent.

    Each tests/test_tui_*.py file skips its own classes via
    @unittest.skipUnless(_HAS_TEXTUAL, ...) — that mechanism stays as
    the enforcement point. This banner exists because that per-class
    skip is otherwise silent: `pip install -e '.[dev]'` (no `tui`
    extra) produces a suite that runs and reports green while quietly
    skipping every TUI test. That gap is exactly what let the TUI
    suite go unexercised in CI from June to August 2026 with nobody
    noticing. Run scripts/dev-setup.sh (installs `.[tui,dev]`) to get
    textual and stop the skips.
    """
    try:
        import textual  # noqa: F401
    except ImportError:
        pass
    else:
        return
    tui_test_count = len(list((Path(__file__).resolve().parent).glob('test_tui_*.py')))
    print(
        '\n' + '=' * 70 + '\n'
        'WARNING: textual is not installed in this interpreter.\n'
        f'  {tui_test_count} tests/test_tui_*.py file(s) will SKIP ENTIRELY,\n'
        '  not fail -- the run will still look green.\n'
        "  Run scripts/dev-setup.sh to install the '.[tui,dev]' extras.\n"
        + '=' * 70 + '\n',
        file=sys.stderr,
    )


_tui_skip_banner()


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_host_systemctl(monkeypatch):
    """Keep a test from asking the host's real `systemctl cat` by accident.

    `smd doctor`'s check families (radiod's drop-ins, the guest agent's
    drop-in) ask the host's systemd whether a unit exists.  A test that calls
    `collect_findings` or `cmd_doctor` without stubbing them would then
    report whatever the machine running the suite happens to have installed.
    This wrapper fails a `systemctl cat` loudly (the existence gate of both
    drop-in families).  Other systemctl reads stay allowed: older families
    already make them in unstubbed tests, harmlessly.  A test that patches
    `subprocess.run` (or `smd._run`) itself replaces the wrapper and keeps
    working.
    """
    import subprocess
    real_run = subprocess.run

    def guarded(cmd, *a, **kw):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else []
        if argv[:1] == ['sudo']:
            argv = argv[1:]
        if argv[:2] == ['systemctl', 'cat']:
            raise AssertionError(
                f"a test ran the host's real {' '.join(map(str, argv))!r}. "
                "Stub the doctor family that asked "
                "(smd._qga_dropin_findings, smd._radiod_dropins_findings, "
                "or the _ensure_* twin), or pass run= / patch smd._run.")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, 'run', guarded)


def needs_real_sudo(obj):
    """Skip unless SIGMOND_TEST_ALLOW_SUDO=1: the test runs the host's real
    sudo (on temporary paths), which the suite's guard refuses."""
    import unittest
    return unittest.skipUnless(
        ALLOW_SUDO, "runs real sudo; set SIGMOND_TEST_ALLOW_SUDO=1 to run")(obj)


@pytest.fixture(autouse=True)
def _no_elevation(monkeypatch):
    """An in-process exec of sudo (bin/smd's `_need_root` re-exec) would
    replace the pytest process, or elevate it.  Raise instead.  A test
    that patches os.execvp itself replaces this wrapper."""
    if ALLOW_SUDO:
        return
    for name in ("execv", "execve", "execvp", "execvpe", "execl", "execle",
                 "execlp", "execlpe"):
        real = getattr(os, name, None)
        if real is None:
            continue

        def guarded(file, *a, _real=real, _name=name, **kw):
            if os.path.basename(os.fsdecode(file)) == "sudo":
                raise AssertionError(
                    f"a test reached os.{_name}({file!r}, ...): an elevation "
                    "through sudo.  Patch `_need_root` (or os.execvp) in the "
                    "test; the suite never elevates.")
            return _real(file, *a, **kw)
        monkeypatch.setattr(os, name, guarded)


@pytest.fixture(autouse=True)
def _no_host_uploader_environment(monkeypatch):
    """The gate path asks hs-uploader.service's environment and reads
    /etc/hs-uploader/uploader.env for a moved HS_UPLOADER_STATE_DIR (Task
    27 M-3).  Keep every test off the host's: the unit reads empty and the
    file stands absent.  A test of that check patches both itself."""
    from sigmond.commands import uploader as up_cmd
    monkeypatch.setattr(up_cmd, "_unit_environment", lambda: {})
    monkeypatch.setattr(up_cmd, "_UPLOADER_ENV",
                        Path("/nonexistent/sigmond-test/uploader.env"))
