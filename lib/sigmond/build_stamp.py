"""Which commit a deploy.toml ``[build]`` last built, per component.

A component with no install.sh but a deploy.toml ``[build]`` (station-web,
hamsci-physics) builds its own venv.  Whether a checkout sits behind
upstream says nothing about whether its venv was built from the code it now
holds.  A v3.82 station shows why (v3.83 final review, finding 1): its old
`smd update` pulled station-web and built nothing; a second update saw
nothing behind; `smd align` found no install.sh to run.  The next restart
started station-web on a venv that lacked the new dependencies.

So every successful build records the checkout's HEAD here, and `smd update`
and `smd align` build again whenever that record is absent or names another
commit.

The stamps live in ``/var/lib/sigmond/build-stamps/<component>``: root
writes them (the build runs as root), and /var/lib/sigmond survives reboots
and updates, where /run would not.  ``SIGMOND_BUILD_STAMP_DIR`` moves the
directory; the test suite points it at a temporary one.
"""
from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path
from typing import Optional

DEFAULT_STAMP_DIR = Path('/var/lib/sigmond/build-stamps')
ENV_STAMP_DIR = 'SIGMOND_BUILD_STAMP_DIR'


def stamp_dir() -> Path:
    env = os.environ.get(ENV_STAMP_DIR)
    return Path(env) if env else DEFAULT_STAMP_DIR


def stamp_path(component: str, directory: Optional[Path] = None) -> Path:
    return (directory or stamp_dir()) / component


def head_of(repo_dir: Path) -> Optional[str]:
    """The checkout's HEAD sha, or None when git cannot say."""
    from sigmond import gitowner
    if not (Path(repo_dir) / '.git').exists():
        # Not a checkout of its own: git would answer for an enclosing one.
        return None
    try:
        r = gitowner.run_git(Path(repo_dir), 'rev-parse', 'HEAD')
    except OSError:
        return None
    sha = (r.stdout or '').strip()
    return sha if r.returncode == 0 and sha else None


def read_stamp(component: str, directory: Optional[Path] = None) -> Optional[str]:
    try:
        text = stamp_path(component, directory).read_text().strip()
    except OSError:
        return None
    return text or None


def write_stamp(repo_dir: Path, head: str,
                directory: Optional[Path] = None) -> Optional[str]:
    """Record ``head`` as built for this checkout, atomically (temp file,
    then rename).  Returns a warning line when the write failed, else None:
    a stamp that cannot be written costs one extra build later, never the
    build that just succeeded."""
    d = directory or stamp_dir()
    name = Path(repo_dir).name
    tmp = None
    try:
        d.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=f'.{name}.')
        with os.fdopen(fd, 'w') as fh:
            fh.write(head + '\n')
        os.chmod(tmp, 0o644)
        os.replace(tmp, d / name)
        return None
    except OSError as exc:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return f'  warning: could not record the build of {name} in {d}: {exc}'


def has_installer(repo_dir: Path) -> bool:
    """The two install-script candidates `smd update` and `smd align` try."""
    repo_dir = Path(repo_dir)
    return ((repo_dir / 'scripts' / 'install.sh').exists()
            or (repo_dir / 'install.sh').exists())


def declares_build(repo_dir: Path) -> bool:
    """Whether deploy.toml holds a ``[build]`` table with a non-empty
    ``steps`` list.  A file that will not parse declares no build."""
    try:
        with open(Path(repo_dir) / 'deploy.toml', 'rb') as fh:
            section = tomllib.load(fh).get('build')
    except (OSError, ValueError):
        # TOMLDecodeError and UnicodeDecodeError are both ValueError.
        return False
    steps = section.get('steps') if isinstance(section, dict) else None
    return isinstance(steps, list) and bool(steps)


def is_stale(repo_dir: Path, head: Optional[str] = None,
             directory: Optional[Path] = None) -> bool:
    """No stamp, or a stamp naming another commit than HEAD.  A HEAD that
    cannot be read counts as stale: building once more costs less than a
    service on a venv without its dependencies."""
    head = head or head_of(repo_dir)
    stamp = read_stamp(Path(repo_dir).name, directory)
    return not head or stamp != head


def needs_build(repo_dir: Path, directory: Optional[Path] = None) -> bool:
    """The deploy.toml build stands in for an absent installer, and its
    stamp is stale.  An install.sh owns its own build."""
    return (not has_installer(repo_dir) and declares_build(repo_dir)
            and is_stale(repo_dir, directory=directory))
