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
import os
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Optional

# Where firstboot-v3.sh writes it, and where sigmond-wizard.sh copies it
# into the VM.  Read-only as far as this module is concerned: rewriting
# it would forge the lineage, which is the one thing it records well.
IMAGE_VERSION_FILE = '/etc/sigmond-appliance/version'
HISTORY_FILE = '/var/lib/sigmond/update-history.jsonl'
# sigmond-appliance's provision-components.sh writes this into the golden VM
# at build time.  The version file reaches the VM only when sigmond-wizard.sh
# copies it, late in the first boot; sigmond-wisdom.service runs before that,
# and on a fresh image (final review, 2026-11-05) it judged the baked wisdom
# as a repo install's.
GOLDEN_IMAGE_MARKER = '/etc/sigmond-appliance/golden-image'

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


def is_appliance_install(root: str = '/') -> bool:
    """True on a host the USB image laid down, False on a repo install.

    The image's firstboot writes /etc/sigmond-appliance/version, and the
    golden VM's build writes GOLDEN_IMAGE_MARKER; nothing else writes either.
    Whatever differs for a ham's own machine keys on this one test, so the
    kernel command line stays the operator's unless sigmond owns the box.
    """
    return any((Path(root) / p.lstrip('/')).exists()
               for p in (IMAGE_VERSION_FILE, GOLDEN_IMAGE_MARKER))


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


def _git_commit(path) -> Optional[dict]:
    """Date, summary and local-change state of a checkout's HEAD.

    The summary is the commit's subject, except for a GitHub merge commit
    ("Merge pull request #109 from HamSCI/fix/...") whose subject names a
    branch, not a change: there the first body line is the PR's title,
    which is what changed.  Same --no-optional-locks rule as `_git_head`.
    """
    base = ['git', '--no-optional-locks', '-c', f'safe.directory={path}',
            '-C', str(path)]
    log = subprocess.run(base + ['log', '-1', '--format=%cs%x00%s%x00%b', 'HEAD'],
                         capture_output=True, text=True)
    if log.returncode != 0 or '\0' not in log.stdout:
        return None
    date, subject, body = (log.stdout.split('\0', 2) + ['', ''])[:3]
    summary = subject.strip()
    if summary.startswith('Merge pull request'):
        title = next((l.strip() for l in body.splitlines() if l.strip()), '')
        pr = summary.split(' from ')[0].replace('Merge pull request ', 'PR ')
        if title:
            summary = f'{title} ({pr})'
    # Tracked files only: an editor's or a Mac copy's stray files are not a
    # change to what runs, a hand-edited tracked file is.
    st = subprocess.run(base + ['status', '--porcelain', '--untracked-files=no'],
                        capture_output=True, text=True)
    return {'date': date.strip(), 'summary': summary,
            'dirty': st.returncode == 0 and bool(st.stdout.strip())}


def component_commits(checkouts: Iterable, git: Callable = _git_commit) -> dict:
    """Date, summary and local-change flag of each checkout's HEAD, keyed by
    component name; read live, like `component_versions`."""
    out = {}
    for path in checkouts:
        try:
            info = git(path)
        except OSError:
            info = None
        if info:
            out[Path(path).name] = info
    return out


# ─── where each component stands: against the image, and against upstream ────

MANIFEST_FILE = '/etc/sigmond-appliance/manifest.txt'
LS_REMOTE_TIMEOUT = 15


def image_manifest(path=MANIFEST_FILE) -> dict:
    """The image's own record: when it was built and the commit of each
    component it shipped.  {} when there is no manifest (a repo install, or
    an image older than the manifest)."""
    try:
        text = Path(path).read_text()
    except OSError:
        return {}
    from .doctor import _parse_manifest_components
    built = next((l.split(':', 1)[1].strip() for l in text.splitlines()
                  if l.startswith('built_utc:')), None)
    return {'built': built, 'components': _parse_manifest_components(text) or {}}


def _g(path, *args, timeout=None):
    base = ['git', '--no-optional-locks', '-c', f'safe.directory={path}',
            '-C', str(path)]
    try:
        r = subprocess.run(base + list(args), capture_output=True, text=True,
                           timeout=timeout,
                           env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    except (OSError, subprocess.TimeoutExpired):
        return 1, ''
    return r.returncode, r.stdout.strip()


def _upstream_branch(path) -> Optional[str]:
    """The branch upstream ships: the one this checkout tracks, else the
    remote's default (a pinned checkout is detached and tracks nothing)."""
    rc, out = _g(path, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
    if rc == 0 and out.startswith('origin/'):
        return out[len('origin/'):]
    rc, out = _g(path, 'symbolic-ref', '-q', 'refs/remotes/origin/HEAD')
    if rc == 0 and out.startswith('refs/remotes/origin/'):
        return out[len('refs/remotes/origin/'):]
    for b in ('main', 'master'):
        if _g(path, 'rev-parse', '-q', '--verify', f'refs/remotes/origin/{b}')[0] == 0:
            return b
    return None


def standing(path, image_sha: Optional[str], remote: bool = True) -> dict:
    """One component against the image it came in and against upstream.

    vs_image: 'image' (still what the image shipped), 'newer' (moved past
    it), 'older', 'differs' (neither contains the other), 'not in image', or
    None when there is no image manifest.

    upstream: commits on upstream's branch that this checkout does not
    have.  Asked with `git ls-remote`, which writes nothing into the
    checkout -- so this works as any user, and a component it cannot reach
    falls back to the last fetch, marked so.  `behind` is None when the tip
    has moved but its commits were never fetched here.
    """
    from .doctor import _sha_equal
    head = _g(path, 'rev-parse', 'HEAD')[1]
    out = {'pinned': _g(path, 'symbolic-ref', '-q', 'HEAD')[0] != 0,
           'vs_image': None, 'upstream': None}
    if image_sha is not None:
        if not image_sha:
            out['vs_image'] = 'not in image'
        elif _sha_equal(head, image_sha):
            out['vs_image'] = 'image'
        elif _g(path, 'merge-base', '--is-ancestor', image_sha, 'HEAD')[0] == 0:
            out['vs_image'] = 'newer'
        elif _g(path, 'merge-base', '--is-ancestor', 'HEAD', image_sha)[0] == 0:
            out['vs_image'] = 'older'
        else:
            out['vs_image'] = 'differs'
    branch = _upstream_branch(path)
    if not branch:
        return out
    tip, checked = None, False
    if remote:
        rc, ls = _g(path, 'ls-remote', 'origin', f'refs/heads/{branch}',
                    timeout=LS_REMOTE_TIMEOUT)
        if rc == 0 and ls:
            tip, checked = ls.split()[0], True
    if not tip:
        tip = _g(path, 'rev-parse', '-q', '--verify', f'refs/remotes/origin/{branch}')[1] or None
    if not tip:
        return out
    up = {'branch': branch, 'checked': checked, 'behind': None, 'date': None}
    if _g(path, 'cat-file', '-e', f'{tip}^{{commit}}')[0] == 0:
        up['behind'] = int(_g(path, 'rev-list', '--count', f'HEAD..{tip}')[1] or 0)
        up['date'] = _g(path, 'log', '-1', '--format=%cs', tip)[1] or None
    elif tip != head:
        up['behind'] = None                  # moved, never fetched here
    out['upstream'] = up
    return out


def component_standing(checkouts: Iterable, image: dict, remote: bool = True) -> dict:
    """`standing` for every checkout, in parallel: one ls-remote each, so
    26 components take about as long as the slowest one, not their sum."""
    from concurrent.futures import ThreadPoolExecutor
    comps = image.get('components') if image else None
    paths = list(checkouts)

    def one(p):
        sha = None if comps is None else comps.get(Path(p).name, '')
        try:
            return Path(p).name, standing(p, sha, remote)
        except OSError:
            return Path(p).name, None
    with ThreadPoolExecutor(max_workers=16) as ex:
        return {n: s for n, s in ex.map(one, paths) if s}


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


SUMMARY_WIDTH = 64
DETAIL_SUMMARY_WIDTH = 52          # the terminal table has two more columns


def _image_col(st: Optional[dict]) -> str:
    v = (st or {}).get('vs_image')
    return {'not in image': 'added', None: ''}.get(v, v)


def _upstream_col(st: Optional[dict]) -> str:
    up = (st or {}).get('upstream')
    if not up:
        return '?'
    if up['behind'] == 0:
        col = 'current'
    elif up['behind'] is None:
        col = 'newer'
    else:
        col = f'{up["behind"]} newer'
    if st.get('pinned') and up['behind'] != 0:
        col += '*'
    return col


def _detail_table(components: dict, commits: dict, standing: dict,
                  image: Optional[dict]) -> str:
    """The terminal view: one row per component, newest first, with where it
    stands against the image and against upstream, then what that adds up
    to."""
    have_image = bool(image and image.get('components'))
    lines = ['components (live), newest first:',
             f'    {"date":<10}  {"component":<17} {"commit":<9} '
             + (f'{"vs image":<8}  ' if have_image else '')
             + f'{"upstream":<10}  what it changed']
    rows = sorted(((commits[n].get('date') or '?', n) for n in components if n in commits),
                  reverse=True)
    for date, name in rows:
        c, st = commits[name], standing.get(name)
        summary = c.get('summary') or ''
        if len(summary) > DETAIL_SUMMARY_WIDTH:
            summary = summary[:DETAIL_SUMMARY_WIDTH - 1] + '…'
        local = '  [+ local changes]' if c.get('dirty') else ''
        lines.append(f'    {date:<10}  {name:<17} {components[name]:<9} '
                     + (f'{_image_col(st):<8}  ' if have_image else '')
                     + f'{_upstream_col(st):<10}  {summary}{local}')

    lines.append('')
    if have_image:
        moved = sorted(n for n in components
                       if (standing.get(n) or {}).get('vs_image') in ('newer', 'older', 'differs', 'not in image'))
        gone = sorted(set(image['components']) - set(components))
        lines.append(f'since the image: {", ".join(moved) if moved else "nothing has moved"}'
                     + (f'; removed: {", ".join(gone)}' if gone else ''))
    newer = sorted(n for n in components
                   if ((standing.get(n) or {}).get('upstream') or {}).get('behind', 0) != 0
                   and (standing.get(n) or {}).get('upstream'))
    held = [n for n in newer if standing[n].get('pinned')]
    if newer:
        lines.append(f'newer upstream: {", ".join(n + ("*" if n in held else "") for n in newer)}')
        if held:
            lines.append('    * held at a pin: `smd update` leaves it; `smd update --unpin` would take it')
        if any((standing[n].get('upstream') or {}).get('behind') is None for n in newer):
            lines.append('    "newer" with no count: upstream has moved, but its commits were '
                         'never fetched here')
        lines.append('    `smd update` (a dry run) lists exactly what it would install')
    else:
        lines.append('newer upstream: nothing -- every component is at its branch tip')
    stale = sorted(n for n, st in standing.items()
                   if st and st.get('upstream') and not st['upstream']['checked'])
    if stale:
        lines.append(f'    not reachable just now, so from the last fetch: {", ".join(stale)}')
    return '\n'.join(lines)


def format_report(lineage: dict, components: dict, history: list,
                  radiod: Optional[dict] = None,
                  commits: Optional[dict] = None,
                  detail_only: bool = False,
                  standing: Optional[dict] = None,
                  image: Optional[dict] = None) -> str:
    """Human-readable provenance, with the lineage caveat kept adjacent.

    The caveat sits on the same line as the number deliberately: split
    apart, the number travels alone and gets misread — which is how
    "DASI002 is a v3.20 box" became something we believed.
    """
    built = (image or {}).get('built')
    built = f', built {built[:16].replace("T", " ")} UTC' if built else ''
    lines = [f'image:      {lineage.get("image") or "(unstamped)"}{built}'
             f'   [{lineage.get("means")}]']
    # On a terminal the dated block alone says everything the hash list does
    # (rob, 2026-11-11: "suppress all that ... just have the second section").
    detail_only = detail_only and bool(commits)
    if not detail_only:
        lines += ['', 'components (live):']
        for name in sorted(components):
            lines.append(f'    {name:<16} {components[name]}')
        if not components:
            lines.append('    (no component checkouts found)')

    # ⛔ A SEPARATE block, after a blank line.  The image build captures this
    # output as the image's manifest, and every reader of `components (live):`
    # (doctor._parse_manifest_components: drift, align, adopt, fleet) takes
    # exactly two fields per row and stops at the first blank line.  A date
    # or a summary on those rows would drop them all and fail the build's
    # 10-row floor.
    if commits and detail_only and standing is not None:
        lines += ['', _detail_table(components, commits, standing, image)]
    elif commits:
        lines += ['', 'components (live), newest first: date, commit, what it changed'
                      if detail_only else 'what each is at (newest first):']
        rows = [(commits[n].get('date') or '?', n) for n in components if n in commits]
        for date, name in sorted(rows, key=lambda r: (r[0], r[1]), reverse=True):
            c = commits[name]
            summary = c.get('summary') or ''
            if len(summary) > SUMMARY_WIDTH:
                summary = summary[:SUMMARY_WIDTH - 1] + '…'
            local = '  [+ local changes]' if c.get('dirty') else ''
            lines.append(f'    {date}  {name:<17} {components[name]:<9} {summary}{local}')

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
