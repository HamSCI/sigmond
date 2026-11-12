"""Full client purge — the operator escape hatch for deprecated clients.

``smd remove <name> --purge`` (or ``smd remove <name>`` when ``<name>``
is in the deprecation list) routes here.  In order:

1. Stop and disable every systemd unit declared by the client's
   ``deploy.toml`` (read it BEFORE deleting anything).
2. Remove the symlinks created by the deploy.toml ``kind="link"`` install
   steps (typically ``/etc/systemd/system/<unit>``).
3. ``systemctl daemon-reload``.
4. ``rm -rf`` the venv at ``/opt/<name>/`` if present.
5. ``rm -rf`` the source repo at ``/opt/git/sigmond/<name>/``.
6. ``rm -rf`` the config dir at ``/etc/<name>/``.
7. Leftovers an install puts outside those trees (KJ6MKI, 2026-11-12):
   symlinks in ``/etc/systemd/system`` (and its ``*.wants``) or
   ``/usr/local/bin`` that point into the checkout or venv -- e.g.
   hf-tec's install.sh links ``hf-tec-qa@.service``/``.timer``, which
   deploy.toml never declares, and they were left dangling; sigmond's
   ``/etc/sigmond/clients/<name>.deploy.toml`` and ``<name>@*.sources.toml``;
   and the deploy.toml ``mkdir`` destinations (``/var/lib/<name>``,
   ``/var/log/<name>``), removed only when they hold no files.

Refuses to act on a name that's neither in the deprecation list nor on
disk — better to surface "nothing to purge" than to power through a
typo.  The caller (bin/smd) supplies confirmation prompts; this module
just executes the plan and prints what it's doing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Optional

GIT_BASE = Path('/opt/git/sigmond')
ETC_BASE = Path('/etc')
VENV_BASE = Path('/opt')
SYSTEMD_SYSTEM = Path('/etc/systemd/system')
SIGMOND_CLIENTS = Path('/etc/sigmond/clients')
LOCAL_BIN = Path('/usr/local/bin')


def _read_deploy_toml(repo_dir: Path) -> Optional[dict]:
    deploy = repo_dir / 'deploy.toml'
    if not deploy.exists():
        return None
    try:
        with open(deploy, 'rb') as f:
            return tomllib.load(f)
    except (OSError, ValueError) as exc:
        print(f"  warning: could not read {deploy}: {exc}", file=sys.stderr)
        return None


def _running_template_instances(unit: str) -> list[str]:
    """For a template unit ``foo@.service`` return the currently-running
    ``foo@instance.service`` names.  Used so stop/disable hits each
    real instance, not the template skeleton (which systemctl rejects)."""
    template = unit.replace('@.', '@')
    r = subprocess.run(
        ['systemctl', 'list-units', '--type=service', '--all',
         '--no-legend', '--plain', f'{template}*'],
        capture_output=True, text=True, check=False,
    )
    return [line.split()[0] for line in r.stdout.splitlines() if line.strip()]


def _expand_units(declared: list[str]) -> list[str]:
    """Turn the deploy.toml ``units`` list (which may contain template
    skeletons like ``radiod@.service``) into concrete unit names."""
    out: list[str] = []
    for unit in declared:
        if '@.' in unit:
            out.extend(_running_template_instances(unit))
        else:
            out.append(unit)
    return out


def plan_purge(name: str, *, extra_paths: tuple[str, ...] = ()) -> dict:
    """Compute what a purge of *name* would touch.  Pure inspection —
    no side effects.  Returned dict is suitable for both human display
    and as the input to ``execute_purge``.

    *extra_paths* (typically pulled from a ``DeprecatedEntry``) are
    absolute paths added to the rm-rf list.  Missing ones are silently
    dropped so a deprecation block can list paths that may or may not
    exist on a given host."""
    repo_dir = GIT_BASE / name
    deploy = _read_deploy_toml(repo_dir) if repo_dir.exists() else None

    declared_units: list[str] = []
    link_dsts: list[Path] = []
    mkdir_dsts: list[Path] = []
    if deploy is not None:
        sysd = deploy.get('systemd', {}) or {}
        # templated_units too: hf-tec@ / codar-sounder@ were never stopped
        # or disabled because only `units` was read.
        for unit in list(sysd.get('units', [])) + list(sysd.get('templated_units', [])):
            if unit not in declared_units:
                declared_units.append(unit)
        for step in deploy.get('install', {}).get('steps', []):
            if step.get('kind') == 'link' and step.get('dst'):
                link_dsts.append(Path(step['dst']))
            elif step.get('kind') == 'mkdir' and step.get('dst'):
                mkdir_dsts.append(Path(step['dst']))

    extras = [Path(p) for p in extra_paths if Path(p).exists()]
    venv_dir = VENV_BASE / name if (VENV_BASE / name).exists() else None
    owned_roots = [d for d in (repo_dir if repo_dir.exists() else None, venv_dir) if d]

    return {
        'name': name,
        'repo_dir': repo_dir if repo_dir.exists() else None,
        'venv_dir': venv_dir,
        'config_dir': ETC_BASE / name if (ETC_BASE / name).exists() else None,
        'extra_dirs': extras,
        'declared_units': declared_units,
        'expanded_units': _expand_units(declared_units),
        'link_dsts': link_dsts,
        'owned_links': _links_into(owned_roots, exclude=link_dsts),
        'client_files': _client_files(name),
        'state_dirs': [d for d in mkdir_dsts if d.is_dir()],
    }


def _links_into(roots, *, exclude=()) -> list[Path]:
    """Symlinks in the unit and bin directories whose target lies inside one
    of *roots* -- what an install.sh linked without declaring it."""
    if not roots:
        return []
    # A link counts when it names a root exactly or something under it --
    # never a sibling that only shares the prefix (hf-tec vs hf-tec-foo).
    exact = {str(r).rstrip('/') for r in roots}
    prefixes = [x + '/' for x in exact]
    dirs = [SYSTEMD_SYSTEM, LOCAL_BIN]
    try:
        dirs += [d for d in SYSTEMD_SYSTEM.iterdir()
                 if d.is_dir() and not d.is_symlink() and d.name.endswith('.wants')]
    except OSError:
        pass
    skip = {str(p) for p in exclude}
    out = []
    for d in dirs:
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if not e.is_symlink() or str(e) in skip:
                continue
            try:
                target = os.readlink(e)
            except OSError:
                continue
            if not os.path.isabs(target):
                target = os.path.normpath(os.path.join(str(d), target))
            if target.rstrip('/') in exact or any(target.startswith(p) for p in prefixes):
                out.append(e)
    return out


def _client_files(name: str) -> list[Path]:
    """sigmond's own per-client files under /etc/sigmond/clients."""
    out = []
    f = SIGMOND_CLIENTS / f'{name}.deploy.toml'
    if f.exists() or f.is_symlink():
        out.append(f)
    try:
        out += sorted(SIGMOND_CLIENTS.glob(f'{name}@*.sources.toml'))
    except OSError:
        pass
    return out


def _holds_files(d: Path) -> bool:
    for _root, _dirs, files in os.walk(d):
        if files:
            return True
    return False


def _remove_if_empty(d: Path) -> bool:
    """rmdir *d* and every directory under it when none holds a file.
    True when *d* is gone."""
    if not d.is_dir() or d.is_symlink() or _holds_files(d):
        return False
    for root, dirs, _files in os.walk(d, topdown=False):
        for sub in dirs:
            os.rmdir(os.path.join(root, sub))
    os.rmdir(d)
    return True


def render_plan(plan: dict) -> list[str]:
    """One-line-per-thing human description of the purge plan."""
    out: list[str] = []
    if plan['expanded_units']:
        out.append(f"  systemd:   stop+disable {', '.join(plan['expanded_units'])}")
    for dst in plan['link_dsts']:
        if dst.is_symlink() or dst.exists():
            out.append(f"  symlink:   rm {dst}")
    if plan['venv_dir']:
        out.append(f"  venv:      rm -rf {plan['venv_dir']}")
    if plan['repo_dir']:
        out.append(f"  source:    rm -rf {plan['repo_dir']}")
    if plan['config_dir']:
        out.append(f"  config:    rm -rf {plan['config_dir']}")
    for extra in plan.get('extra_dirs', ()):
        out.append(f"  extra:     rm -rf {extra}")
    for link in plan.get('owned_links', ()):
        out.append(f"  symlink:   rm {link}")
    for f in plan.get('client_files', ()):
        out.append(f"  sigmond:   rm {f}")
    for d in plan.get('state_dirs', ()):
        out.append(f"  state dir: rmdir {d} (only if it holds no files)")
    if not out:
        out.append(f"  (nothing on disk for {plan['name']})")
    return out


def execute_purge(plan: dict, *, dry_run: bool = False) -> int:
    """Execute the plan returned by ``plan_purge``.  Returns 0 on
    success, non-zero on the first hard failure.  Best-effort: a
    failed systemctl-stop on a unit that's already gone is logged
    but doesn't abort the rest."""
    name = plan['name']

    if dry_run:
        for line in render_plan(plan):
            print(line)
        return 0

    # 1. Stop + disable units.  Stop first so disable doesn't race
    # against an in-flight start.
    for unit in plan['expanded_units']:
        r = subprocess.run(['systemctl', 'stop', unit],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0 and 'not loaded' not in r.stderr:
            print(f"  warning: systemctl stop {unit}: {r.stderr.strip()}",
                  file=sys.stderr)
    for unit in plan['declared_units']:
        # Templates can't be disabled directly; disabling the skeleton
        # is what removes the *.wants/ symlinks pointing at instances.
        r = subprocess.run(['systemctl', 'disable', unit],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0 and 'No such file' not in r.stderr \
                and 'does not exist' not in r.stderr:
            print(f"  warning: systemctl disable {unit}: {r.stderr.strip()}",
                  file=sys.stderr)

    # 2. Remove symlinks the deploy.toml installed.
    for dst in plan['link_dsts']:
        try:
            if dst.is_symlink() or dst.exists():
                dst.unlink()
                print(f"  removed {dst}")
        except OSError as exc:
            print(f"  warning: rm {dst}: {exc}", file=sys.stderr)

    # 2b. Undeclared links into the checkout/venv, and sigmond's per-client
    # files.  Before the daemon-reload, so systemd forgets those units too.
    for path in list(plan.get('owned_links', ())) + list(plan.get('client_files', ())):
        try:
            if path.is_symlink() or path.exists():
                path.unlink()
                print(f"  removed {path}")
        except OSError as exc:
            print(f"  warning: rm {path}: {exc}", file=sys.stderr)

    # 3. daemon-reload so systemd forgets the just-removed units.
    subprocess.run(['systemctl', 'daemon-reload'],
                   capture_output=True, check=False)

    # 4-6. rm -rf each tree.  shutil.rmtree handles the depth; we just
    # log each top-level we successfully removed.
    for key, label in (('venv_dir', 'venv'),
                       ('repo_dir', 'source'),
                       ('config_dir', 'config')):
        target = plan[key]
        if target is None:
            continue
        try:
            shutil.rmtree(target)
            print(f"  removed {label}: {target}")
        except OSError as exc:
            print(f"  error: rm -rf {target}: {exc}", file=sys.stderr)
            return 1
    for extra in plan.get('extra_dirs', ()):
        try:
            shutil.rmtree(extra)
            print(f"  removed extra: {extra}")
        except OSError as exc:
            print(f"  error: rm -rf {extra}: {exc}", file=sys.stderr)
            return 1

    # 7. State/log dirs the install made: gone only when they hold no file.
    # Data is never deleted here; a dir that still holds files is named.
    for d in plan.get('state_dirs', ()):
        try:
            if _remove_if_empty(d):
                print(f"  removed empty dir: {d}")
            elif d.is_dir():
                print(f"  kept {d}: it holds files (data is not purged)")
        except OSError as exc:
            print(f"  warning: rmdir {d}: {exc}", file=sys.stderr)

    return 0


def deprecated_on_disk(deprecated: dict) -> list[str]:
    """Return the names from *deprecated* that still have a checkout
    under /opt/git/sigmond/.  Used by ``smd list`` to surface the
    "you have a deprecated client lingering" hint."""
    return sorted(name for name in deprecated
                  if (GIT_BASE / name).exists())
