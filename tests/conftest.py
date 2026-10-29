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
    # A second load of this file (a test that execs it again) reuses the
    # stand-in the first load made, so PATH carries exactly one.
    _NOSUDO = os.environ.get("SIGMOND_TEST_NOSUDO_DIR", "")
    if not (_NOSUDO and os.path.isfile(os.path.join(_NOSUDO, "sudo"))):
        _NOSUDO = _tempfile.mkdtemp(prefix="sigmond-test-nosudo-")
        atexit.register(_shutil.rmtree, _NOSUDO, True)
        with open(os.path.join(_NOSUDO, "sudo"), "w") as _f:
            _f.write("#!/bin/sh\n"
                     "echo \"sudo refused under pytest (tests/conftest.py): $*\" >&2\n"
                     "exit 1\n")
        os.chmod(os.path.join(_NOSUDO, "sudo"), 0o755)
        os.environ["SIGMOND_TEST_NOSUDO_DIR"] = _NOSUDO
    if os.environ.get("PATH", "").split(os.pathsep)[0] != _NOSUDO:
        os.environ["PATH"] = _NOSUDO + os.pathsep + os.environ.get("PATH", "")
    # bin/smd's `_need_root` refuses outright, exit 1, while this is set
    # (Task 27): the second wall, for a child whose PATH lost the stand-in.
    os.environ["SIGMOND_TEST_NO_ELEVATE"] = "1"

# The programs an elevation runs through, by basename.
ELEVATORS = frozenset({"sudo", "doas", "pkexec", "run0"})

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


def _elevator(name) -> bool:
    try:
        return os.path.basename(os.fsdecode(name)) in ELEVATORS
    except TypeError:
        return False


def _refuse(how, what):
    raise AssertionError(
        f"a test reached {how}({what!r}, ...): an elevation through "
        f"{os.path.basename(os.fsdecode(what))}.  Patch `_need_root` (or the "
        "exec) in the test; the suite never elevates.")


def _guarded_env(env):
    """An explicit env for a child: the stand-in first on its PATH, and
    SIGMOND_TEST_NO_ELEVATE set, so a child that strips the environment
    still cannot reach the host's sudo."""
    env = dict(env)
    path = env.get("PATH")
    parts = (path if path is not None else os.defpath).split(os.pathsep)
    if not parts or parts[0] != _NOSUDO:
        env["PATH"] = os.pathsep.join([_NOSUDO] + [p for p in parts if p])
    env["SIGMOND_TEST_NO_ELEVATE"] = "1"
    return env


def _argv0(args, shell):
    """The program a Popen ``args`` names (the first word of a shell
    string)."""
    import shlex
    if isinstance(args, (str, bytes, os.PathLike)):
        if shell or isinstance(args, (str, bytes)):
            text = os.fsdecode(args)
            try:
                words = shlex.split(text)
            except ValueError:
                words = text.split()
            return words[0] if words else ""
        return args
    try:
        return list(args)[0]
    except (IndexError, TypeError):
        return ""


_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "mksh", "ash",
                     "busybox"})


def _shell_words(text):
    """The unquoted words of a shell string (G-1).  `;`, `&`, `|`, `(`,
    `)`, `<`, `>` and backticks split words, so `a; b`, `a && b`,
    `$(b)` and `` `b` `` each yield b.  A quoted string stays one token
    and is skipped: it is data (`echo "run sudo"`), and the wizard tests
    print sudoers lines that way."""
    import shlex
    lex = shlex.shlex(os.fsdecode(text).replace("\n", " ; "), posix=False,
                      punctuation_chars="();<>|&`")
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        tokens = list(lex)
    except ValueError:                  # an unclosed quote: plain split
        tokens = os.fsdecode(text).split()
    for tok in tokens:
        if tok[:1] in ("'", '"'):
            continue
        yield tok.lstrip("$!{")


def _shell_elevator(text):
    """The first unquoted word of a shell string that names an elevator
    by a path (``/usr/bin/sudo``, ``./doas``), or None.

    Every word is scanned, but a bare ``sudo`` passes here: the shell
    resolves it through PATH at run time, where the refusing stand-in
    stands first.  A path skips PATH, so only this scan can refuse it.
    Refusing bare names too would refuse the wizard's own shell
    functions, which name sudo in bodies the tests stub or never run."""
    for word in _shell_words(text):
        if word and "/" in word and _elevator(word):
            return word
    return None


def _popen_shell_text(args, shell):
    """The shell text a Popen call runs, or None: a string under
    ``shell=True``, ``args[0]`` of a list under it, or every argument after
    ``-c`` (or a flag cluster holding ``c``, like ``-lc``) of a shell."""
    import re
    if isinstance(args, (str, bytes)):
        return os.fsdecode(args) if shell else None
    try:
        argv = [os.fsdecode(a) for a in args]
    except TypeError:
        return None
    if not argv:
        return None
    if shell:
        return " ".join(argv)
    if os.path.basename(argv[0]) not in _SHELLS:
        return None
    for i, a in enumerate(argv[1:], 1):
        if a == "--":
            break
        if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a):
            return " ".join(argv[i + 1:])
    return None


@pytest.fixture(autouse=True)
def _no_elevation(monkeypatch):
    """An in-process exec of an elevator (bin/smd's `_need_root` re-exec)
    would replace the pytest process, or elevate it; a child started
    through subprocess, posix_spawn, spawn* or system would elevate.  Each
    raises instead when the program's basename is sudo, doas, pkexec or
    run0, by any path.  A child given its own env still gets the stand-in
    first on PATH.  A test that patches one of these itself replaces this
    wrapper."""
    if ALLOW_SUDO:
        return
    import subprocess
    for name in ("execv", "execve", "execvp", "execvpe", "execl", "execle",
                 "execlp", "execlpe"):
        real = getattr(os, name, None)
        if real is None:
            continue

        def guarded(file, *a, _real=real, _name=name, **kw):
            if _elevator(file):
                _refuse(f"os.{_name}", file)
            return _real(file, *a, **kw)
        monkeypatch.setattr(os, name, guarded)

    real_init = subprocess.Popen.__init__

    def popen_init(self, args, *a, **kw):
        prog = _argv0(args, kw.get("shell", False))
        for p in (prog, kw.get("executable")):
            if p is not None and _elevator(p):
                _refuse("subprocess.Popen", p)
        text = _popen_shell_text(args, kw.get("shell", False))
        hit = _shell_elevator(text) if text is not None else None
        if hit is not None:
            _refuse("subprocess.Popen (shell)", hit)
        if kw.get("env") is not None:
            kw["env"] = _guarded_env(kw["env"])
        return real_init(self, args, *a, **kw)
    monkeypatch.setattr(subprocess.Popen, "__init__", popen_init)

    for name in ("posix_spawn", "posix_spawnp"):
        real = getattr(os, name, None)
        if real is None:
            continue

        def spawn(path, argv, *a, _real=real, _name=name, **kw):
            for p in (path, *(list(argv)[:1])):
                if _elevator(p):
                    _refuse(f"os.{_name}", p)
            return _real(path, argv, *a, **kw)
        monkeypatch.setattr(os, name, spawn)

    for name in ("spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl",
                 "spawnle", "spawnlp", "spawnlpe"):
        real = getattr(os, name, None)
        if real is None:
            continue

        def spawn_mode(mode, file, *a, _real=real, _name=name, **kw):
            # spawnv*: a[0] is the argv list; spawnl*: a[0] is arg0
            first = a[0] if a else None
            if isinstance(first, (list, tuple)):
                first = first[0] if first else None
            for p in (file, first):
                if p is not None and _elevator(p):
                    _refuse(f"os.{_name}", p)
            return _real(mode, file, *a, **kw)
        monkeypatch.setattr(os, name, spawn_mode)

    real_system = os.system

    def system(command):
        hit = _shell_elevator(command)
        if hit is not None:
            _refuse("os.system", hit)
        return real_system(command)
    monkeypatch.setattr(os, "system", system)


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


@pytest.fixture(autouse=True)
def _build_stamps_in_tmp(monkeypatch, tmp_path):
    """A successful deploy.toml build records its HEAD under
    /var/lib/sigmond/build-stamps (sigmond.build_stamp).  Keep every test's
    stamps in its own directory, inherited by the subprocesses it starts
    (update_build.py runs as one)."""
    monkeypatch.setenv("SIGMOND_BUILD_STAMP_DIR", str(tmp_path / "build-stamps"))


@pytest.fixture(autouse=True)
def _no_host_sink_files(tmp_path, monkeypatch):
    """Keep every in-process sink_path() off the host's real sink files.

    Both writer copies bind /etc/hs-uploader/sink.toml and
    /var/lib/sigmond/sink.db as keyword defaults, so `Writer.from_env(env={})`
    would resolve to a live station's sink.  Re-point those defaults at paths
    that do not exist.  A test that passes its own `config_file=`,
    `legacy_path=` or `state_dir=`, or sets the env, still wins.  (A test
    that starts a subprocess names its sink through the env.)
    """
    import importlib
    nowhere = tmp_path / "no-such-host-sink"
    for modname in ("hs_uploader.sink.writer", "sigmond.hamsci_sink._bundled"):
        try:
            mod = importlib.import_module(modname)
        except ImportError:
            continue
        # `sink_path` delegates to `_sink_choice`, and `Writer.from_env`
        # calls both; re-point both.
        for fn in (mod.sink_path, mod._sink_choice):
            kw = dict(fn.__kwdefaults__)
            kw["config_file"] = str(nowhere / "sink.toml")
            kw["legacy_path"] = str(nowhere / "legacy" / "sink.db")
            monkeypatch.setattr(fn, "__kwdefaults__", kw)
