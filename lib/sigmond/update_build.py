"""Run a component's deploy.toml ``[build]`` for `smd update`.

A component with no install.sh but a ``[build].steps`` (station-web,
hamsci-physics) builds its own venv through those steps.  `smd install` runs
them; `smd update` pulled the checkout and stopped, so a new dependency
crash-looped the service at its next restart (v3.83 row 11).

`smd` refuses to run under sudo, so the update executor reaches root by
running this file as a script: ``sudo -n python3 update_build.py <repo>``.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Run as a script, this file's own lib/ is not on sys.path yet.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sigmond import installer  # noqa: E402


_UNIT_EXTS = ('.service', '.timer', '.target', '.socket', '.path',
              '.mount', '.slice')


def _wrote_unit_link(msg: str) -> bool:
    """A link message that put a unit under /etc/systemd.  The messages name
    the link's basename, not its directory, so a unit suffix counts too."""
    if msg.lstrip().startswith('warning'):
        return False
    if '/etc/systemd' in msg:
        return True
    parts = msg.split()
    return len(parts) > 1 and parts[0] == 'linked' \
        and parts[1].endswith(_UNIT_EXTS)


def run(repo_dir: Path, *, runner=subprocess.run) -> tuple[bool, list[str]]:
    """Build, link, and reload systemd when a unit link landed.

    ``force=True``: an update builds even when every ``produces`` artefact
    exists, since the venv exists and holds the old dependencies.
    """
    ok, msgs = installer.run_deploy_build_steps(
        repo_dir, runner=runner, force=True)
    if not ok:
        return False, msgs
    links = installer.apply_deploy_toml_links(repo_dir)
    msgs.extend(links)
    if any(_wrote_unit_link(m) for m in links):
        r = runner(['systemctl', 'daemon-reload'],
                   capture_output=True, text=True)
        if r.returncode != 0:
            msgs.append(f'  warning: systemctl daemon-reload exit '
                        f'{r.returncode}')
    return True, msgs


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print('usage: update_build.py <repo-dir>', file=sys.stderr)
        return 2
    ok, msgs = run(Path(argv[0]))
    for m in msgs:
        print(m)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
