"""What is actually installed on this host — computed, never remembered.

`/etc/sigmond-appliance/version` is written once by firstboot and copied
into the VM by the wizard.  Nothing updates it afterwards, so on
2026-08-15 DASI002 asserted `v3.20` while running radiod cd44bbdd,
ka9q-python 3.24.0 and hf-timestd 55e8797 — all installed in place that
morning.  The string was true when written and false by lunchtime, with
no way to tell from the box.

In-place update is the normal path for this fleet, so ANY single stored
version string drifts the same way.  The answer is therefore not a
better stamp but a different shape:

* component state is **computed from the checkouts on every call**, so
  it cannot go stale — the failure mode this module exists to prevent;
* the image string is kept for what it honestly is, the lineage that
  laid the box down, and is labelled so it cannot be read as a claim
  about the present;
* the update history is the one genuinely unrecomputable fact, so it is
  stored — append-only, because "what happened last" is precisely the
  question that already had a stale answer.

Consistent with the rest of the diagnostics here: a missing file is a
finding, never a traceback.  These run on the oldest, least healthy
units in the field, which is where they matter most.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Optional

# Where firstboot-v3.sh writes it, and where sigmond-wizard.sh copies it
# into the VM.  Read-only as far as this module is concerned: rewriting
# it would forge the lineage, which is the one thing it records well.
IMAGE_VERSION_FILE = '/etc/sigmond-appliance/version'
HISTORY_FILE = '/var/lib/sigmond/update-history.jsonl'

_LINEAGE_MEANS = ('image installed on this host — lineage only; '
                  'components may have moved since (see below)')
_LINEAGE_UNKNOWN = ('no image stamp on this host — predates the stamp, '
                    'or was not installed from an appliance image')


def image_lineage(path=IMAGE_VERSION_FILE) -> dict:
    """The image that laid this host down, plus what that does not mean.

    Deliberately returns `means` alongside the string: every consumer of
    this value on DASI002 read it as "what this box is", and it is not.
    """
    try:
        text = Path(path).read_text().strip()
    except OSError:
        return {'image': None, 'means': _LINEAGE_UNKNOWN}
    return {'image': text or None,
            'means': _LINEAGE_MEANS if text else _LINEAGE_UNKNOWN}


def _git_head(path, *args) -> Optional[str]:
    # --no-optional-locks for the same reason doctor.py uses it: run as
    # root, a plain git command rewrites .git/index and leaves it
    # root-owned, recreating the damage we clean up elsewhere.
    proc = subprocess.run(
        ['git', '--no-optional-locks', '-c', f'safe.directory={path}',
         '-C', str(path), 'rev-parse', '--short', 'HEAD', *args],
        capture_output=True, text=True)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def component_versions(checkouts: Iterable, git: Callable = _git_head) -> dict:
    """Live HEAD of each checkout, keyed by component name.

    Computed every call.  A cached copy would be a second thing to keep
    in sync, and keeping things in sync by hand is the defect.
    """
    out = {}
    for path in checkouts:
        name = Path(path).name
        try:
            head = git(path)
        except OSError:
            head = None
        if head:
            out[name] = head
    return out


def record_update(path, entry: dict) -> None:
    """Append one line describing an update. Never rewrites the file."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as fh:
            fh.write(json.dumps(entry, sort_keys=True) + '\n')
    except OSError:
        pass          # history is useful, not load-bearing


def update_history(path=HISTORY_FILE) -> list:
    """Every recorded update, oldest first, skipping unreadable lines.

    A power cut mid-write leaves a truncated final line; dropping it is
    right, and letting it blind the whole tool is not.
    """
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def format_report(lineage: dict, components: dict, history: list,
                  radiod: Optional[dict] = None) -> str:
    """Human-readable provenance, with the lineage caveat kept adjacent.

    The caveat sits on the same line as the number deliberately: split
    apart, the number travels alone and gets misread — which is how
    "DASI002 is a v3.20 box" became something we believed.
    """
    lines = [f'image:      {lineage.get("image") or "(unstamped)"}'
             f'   [{lineage.get("means")}]', '', 'components (live):']
    for name in sorted(components):
        lines.append(f'    {name:<16} {components[name]}')
    if not components:
        lines.append('    (no component checkouts found)')

    if radiod:
        running, pin = radiod.get('running'), radiod.get('pin')
        note = '' if not pin or running == pin else '   <-- differs from pin'
        lines += ['', f'radiod running: {running or "(unreadable)"}'
                      f'   pin: {pin or "(none)"}{note}']

    if history:
        lines += ['', f'updates since install ({len(history)}):']
        for h in history[-5:]:
            lines.append(f'    {h.get("at", "?")}  {h.get("what", "?")}')
        if lineage.get('image'):
            lines.append(f'    -> this host has moved since {lineage["image"]}'
                         f' was installed')
    else:
        lines += ['', 'no recorded updates since install']
    return '\n'.join(lines)
