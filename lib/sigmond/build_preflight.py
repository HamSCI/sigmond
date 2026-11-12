"""Can this host build a component's Python C extensions?  Checked before its
install.sh runs, so a missing header fails that one component with the apt
package to install, instead of a compiler error deep in uv's output.

KJ6MKI, 2026-11-11 (Ubuntu 22.04): hf-timestd 7.0.0 depends on systemd-python,
which ships no wheels and compiles against Python.h.  The host's python3.11 is
Ubuntu's 3.11.0~rc1 build without python3.11-dev, so `smd align --apply`
failed hf-timestd's install with a build error and stopped there.

Which interpreter: the component's existing venv when there is one; otherwise
the one its install.sh asks uv for (`.python-version`, else the floor of
`requires-python`), looked up on PATH.  When no such interpreter is on PATH, uv
fetches a managed Python, which carries its own headers -- nothing to check.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

# Dependencies with no binary wheels: installing them compiles against Python.h.
SOURCE_ONLY_DEPS = ("systemd-python",)

_PROBE = ("import os, sys, sysconfig; "
          "p = os.path.join(sysconfig.get_paths()['include'], 'Python.h'); "
          "print('%d.%d' % sys.version_info[:2]); print('' if os.path.exists(p) else p)")


def source_only_deps(repo: Path) -> list:
    """The SOURCE_ONLY_DEPS that ``repo``'s pyproject.toml or uv.lock names."""
    text = ""
    for f in ("pyproject.toml", "uv.lock"):
        try:
            text += (Path(repo) / f).read_text(errors="replace")
        except OSError:
            pass
    return [d for d in SOURCE_ONLY_DEPS
            if re.search(r'["\s]' + re.escape(d) + r'["\s>=<~!,\]]', text)]


def interpreter_for(repo: Path, which: Callable = shutil.which) -> Optional[str]:
    """The Python the component's venv uses or will use; None when uv would
    bring its own (nothing on this host to check)."""
    repo = Path(repo)
    for v in ("venv", ".venv"):
        py = repo / v / "bin" / "python"
        if py.exists():
            return str(py)
    want = None
    try:
        want = (repo / ".python-version").read_text().strip().split()[0]
    except (OSError, IndexError):
        pass
    if not want:
        try:
            m = re.search(r'requires-python\s*=\s*"[^"]*?(\d+\.\d+)',
                          (repo / "pyproject.toml").read_text(errors="replace"))
            want = m.group(1) if m else None
        except OSError:
            pass
    m = re.match(r"(\d+\.\d+)", want or "")
    if not m:
        return which("python3")
    return which(f"python{m.group(1)}")


def check(repo: Path, *, run: Callable = subprocess.run,
          which: Callable = shutil.which) -> Optional[str]:
    """None when ``repo`` can build here; else a one-line reason that names
    the package to install."""
    deps = source_only_deps(repo)
    if not deps:
        return None
    py = interpreter_for(repo, which=which)
    if not py:
        return None
    try:
        r = run([py, "-c", _PROBE], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (r.stdout or "").splitlines()
    if r.returncode != 0 or len(lines) < 2 or not lines[1].strip():
        return None
    ver, missing = lines[0].strip(), lines[1].strip()
    return (f"{Path(repo).name}: {', '.join(deps)} must be compiled and needs "
            f"Python.h for {py} (Python {ver}); {missing} is missing — "
            f"install it: sudo apt install python{ver}-dev")
