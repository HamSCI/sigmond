"""`smd admin radiod {rename|describe}`: apply a rename plan on a station.

`sigmond.radiod_rename` plans; this module applies, in spec §5's order:
refuse, show, confirm, then under the lifecycle lock record what was
running, stop it, move and write every file, regenerate the derived files
and start again exactly what had been running before.  An instance the
operator had parked stays parked.

Every host access is a module-level hook so tests can replace it:
ROOT, systemctl, is_active, is_enabled, ka9q_web_rewrite,
ka9q_web_port_map, write_atomic, confirm, lock, sdr_list,
enabled_components, catalog_components, resolve_client_units and
passthrough_lookup.

Exit codes: 0 done; 1 refused (nothing written); 2 declined at the prompt;
4 stopped part way through the writes or the regenerate step (the written
list is printed and nothing is started); 5 everything was written but a
unit that had been running (or enabled) did not come back.

Design: docs/superpowers/specs/2026-10-31-radiod-rename-design.md.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
from pathlib import Path

from sigmond import radiod_rename as planner
from sigmond.coordination import load_coordination, render_env
from sigmond.lifecycle import lifecycle_lock, order_units, resolve_units
from sigmond.ui import err, heading, warn

ROOT = Path('/')

#: Passed to render_env; None means read each client's deploy.toml.
passthrough_lookup = None


# ---------------------------------------------------------------- host hooks

def systemctl(*args) -> int:
    try:
        return subprocess.run(['systemctl', *args], timeout=120).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def _quiet(*args) -> bool:
    try:
        return subprocess.run(['systemctl', *args, '--quiet'],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def is_active(unit: str) -> bool:
    return _quiet('is-active', unit)


def is_enabled(unit: str) -> bool:
    return _quiet('is-enabled', unit)


def ka9q_web_rewrite(old: str, new: str) -> list[str]:
    """Regenerate the ka9q-web units after the rename; bin/smd replaces
    this with the real writer.  Returns lines to print."""
    return []


def ka9q_web_port_map(old: str, new: str):
    """(before lines, after lines) of the ka9q-web port map, or None when
    unknown; bin/smd replaces this."""
    return None


def write_atomic(path: Path, text: str) -> None:
    """Replace `path` with `text` via a temp file and os.replace, keeping
    the old file's mode, owner and group."""
    path = Path(path)
    tmp = path.with_name(path.name + '.radiod-rename.tmp')
    try:
        st = path.stat()
    except OSError:
        st = None
    try:
        tmp.write_text(text)
        if st is not None:
            os.chmod(tmp, st.st_mode & 0o7777)
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except PermissionError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def confirm(prompt: str) -> bool:
    try:
        return input(f'{prompt} [y/N] ').strip().lower() in ('y', 'yes')
    except EOFError:
        return False


def lock(reason: str):
    return lifecycle_lock(reason=reason)


def sdr_list() -> int:
    from sigmond.commands import sdr
    return sdr.cmd_sdr_list(argparse.Namespace(json=False))


def enabled_components() -> list[str]:
    from sigmond.topology import load_topology
    return load_topology().enabled_components()


def catalog_components() -> list[str]:
    try:
        from sigmond.catalog import load_catalog
        return sorted(load_catalog())
    except Exception:
        return []


def _coord_path() -> Path:
    return ROOT / 'etc/sigmond/coordination.toml'


def _default_resolve_client_units(components: list[str]) -> list[str]:
    """Service units of `components`, in lifecycle start order."""
    enabled = enabled_components()
    comps = [c for c in components if c in enabled]
    if not comps:
        return []
    try:
        coord = load_coordination(_coord_path())
    except Exception:
        coord = None
    units = [u for u in resolve_units(comps, enabled)
             if u.kind == 'service' and not u.orphaned]
    return [u.unit for u in order_units(units, coordination=coord)]


resolve_client_units = _default_resolve_client_units


# ---------------------------------------------------------------- helpers

def _shown(path: Path) -> str:
    try:
        return '/' + Path(path).relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _changed_lines(before: str, after: str) -> int:
    sm = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(),
                                 autojunk=False)
    return sum(max(i2 - i1, j2 - j1)
               for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != 'equal')


def _load_hold():
    p = ROOT / 'var/lib/sigmond/sdr-hold.json'
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _web_units(p) -> list[str]:
    """The ka9q-web unit(s) serving the old status."""
    units = []
    plain = ROOT / 'etc/systemd/system/ka9q-web.service'
    if any(e.path == plain for e in p.edits):
        units.append('ka9q-web.service')
    units.append(f'ka9q-web@{p.old}.service')
    return units


def _web_after(unit: str, old: str, new: str) -> str:
    return f'ka9q-web@{new}.service' if unit == f'ka9q-web@{old}.service' else unit


def show_plan(p, port_map=None, *, clients=None) -> None:
    """Print everything a rename (or describe) will do."""
    if p.refusals:
        for r in p.refusals:
            err(r)
        return
    heading(f'radiod@{p.old} -> radiod@{p.new}' if p.old != p.new
            else f'radiod@{p.old}: description')
    if p.moves:
        print('moves:')
        for m in p.moves:
            print(f'  {_shown(m.src)} -> {_shown(m.dst)}')
    if p.edits:
        print('edits:')
        for e in p.edits:
            print(f'  {_shown(e.path)}  (+{_changed_lines(e.before, e.after)} '
                  f'changed lines)')
    if p.not_edited:
        print('found but not edited:')
        for x in p.not_edited:
            print(f'  {_shown(x)}')
    if p.history:
        print('history stays under the old name:')
        for x in p.history:
            print(f'  {x}')
    if p.old == p.new:
        print(f'restarts radiod@{p.old}.service if it is running; no clients.')
    else:
        clients = list(clients or [])
        web = _web_units(p)
        print('stops (each only if running):')
        for u in [*reversed(clients), *web, f'radiod@{p.old}.service']:
            print(f'  {u}')
        print('starts (only what was running before):')
        for u in [f'radiod@{p.new}.service',
                  *(_web_after(w, p.old, p.new) for w in web), *clients]:
            print(f'  {u}')
    if port_map:
        before, after = port_map
        print('ka9q-web port map before:')
        for line in before:
            print(f'  {line}')
        print('ka9q-web port map after:')
        for line in after:
            print(f'  {line}')


def _print_written(written: list[str]) -> None:
    print('written:')
    for w in written:
        print(w)


def _move(src: Path, dst: Path) -> None:
    if os.path.lexists(dst):
        # os.replace would silently swap out an empty directory.
        raise FileExistsError(f'{_shown(dst)} already exists')
    try:
        os.replace(src, dst)
    except OSError:
        if dst.exists():
            raise
        shutil.move(str(src), str(dst))


# ---------------------------------------------------------------- rename

def cmd_radiod_rename(args) -> int:
    old, new = args.old, args.new
    comps = sorted(set(catalog_components()) | set(enabled_components()))
    p = planner.plan(old, new, description=args.description, root=ROOT,
                     components=comps, hold=_load_hold())
    new_unit = f'radiod@{new}.service'
    if not p.refused and planner.valid_id(new):
        for what, check in (('enabled', is_enabled), ('active', is_active)):
            if check(new_unit):
                p.refusals.append(
                    f'{new_unit} is already {what}; pick another name or '
                    f'retire it first: systemctl disable --now {new_unit}')
                break
    if p.refused:
        show_plan(p)
        return 1

    clients = resolve_client_units(p.affected_components)
    show_plan(p, ka9q_web_port_map(old, new), clients=clients)
    if args.dry_run:
        return 0
    if not args.yes and not confirm(
            f'Rename radiod@{old} to radiod@{new} (restarts what was running)?'):
        print('declined; nothing written.')
        return 2

    old_unit = f'radiod@{old}.service'
    web = _web_units(p)
    with lock(f'admin radiod rename {old} {new}'):
        watched = [old_unit, *web, *clients]
        was_active = {u: is_active(u) for u in watched}
        was_enabled = {u: is_enabled(u) for u in watched}

        for u in [*reversed(clients), *web]:
            if was_active[u]:
                systemctl('stop', u)
        systemctl('stop', old_unit)
        if was_enabled[old_unit]:
            systemctl('disable', old_unit)

        written: list[str] = []
        try:
            for m in p.moves:
                _move(m.src, m.dst)
                written.append(f'{_shown(m.src)} -> {_shown(m.dst)}')
            for e in p.edits:
                write_atomic(e.path, e.after)
                written.append(_shown(e.path))
            env_path = ROOT / 'etc/sigmond/coordination.env'
            env = render_env(load_coordination(_coord_path()),
                             passthrough_lookup=passthrough_lookup)
            write_atomic(env_path, env)
            if _shown(env_path) not in written:
                written.append(_shown(env_path))
            web_lines = ka9q_web_rewrite(old, new)
        except (Exception, KeyboardInterrupt) as e:
            err(f'rename stopped part way: {e!r}')
            err('nothing was started; finish by hand from this list:')
            _print_written(written)
            return 4
        for line in web_lines:
            print(line)
        if systemctl('daemon-reload') != 0:
            err('systemctl daemon-reload failed; nothing was started, so no '
                'unit runs from a stale definition.  Finish by hand from '
                'this list:')
            _print_written(written)
            return 4

        failed: list[str] = []
        started: list[str] = []

        def start(unit: str) -> None:
            started.append(unit)
            if systemctl('start', unit) != 0:
                failed.append(unit)

        if was_enabled[old_unit] and systemctl('enable', new_unit) != 0:
            failed.append(new_unit)
        if was_active[old_unit]:
            start(new_unit)
        for w in web:
            if was_active[w]:
                start(_web_after(w, old, new))
        for u in clients:
            if was_active[u]:
                start(u)

    _report(p, written, started, failed, was_active[old_unit])
    if failed:
        _did_not_start(failed)
        return 5
    return 0


def _did_not_start(units: list[str]) -> None:
    seen = list(dict.fromkeys(units))
    print(f'renamed, but these units did not start: {" ".join(seen)}')


def _report(p, written, started, failed, radiod_was_active) -> None:
    heading(f'radiod@{p.old} is now radiod@{p.new}')
    print(f'conf: /etc/radio/radiod@{p.old}.conf -> /etc/radio/radiod@{p.new}.conf')
    per_dir: dict[str, int] = {}
    for e in p.edits:
        d = _shown(e.path.parent)
        per_dir[d] = per_dir.get(d, 0) + 1
    print('files edited:')
    for d, n in sorted(per_dir.items()):
        print(f'  {d}: {n}')
    if started:
        print('started again:')
        for u in started:
            print(f'  {u}')
    if not radiod_was_active:
        print(f'radiod@{p.new}.service was not running before the rename and '
              f'was left stopped; `smd apply` starts it.')
    for f in failed:
        warn(f'{f} did not come back; check `systemctl status {f}` and '
             f'start it by hand.')
    if p.history:
        print('history stays under the old name:')
        for x in p.history:
            print(f'  {x}')
    print(f'Spots decoded after the rename are filed under receiver '
          f'`{p.new}` on wsprdaemon; set `receiver_override` in pipelines.toml '
          f'to keep `{p.old}`.')
    print()
    try:
        sdr_list()
    except Exception as e:
        warn(f'smd admin sdr list failed: {e}')


# ---------------------------------------------------------------- describe

def cmd_radiod_describe(args) -> int:
    inst = args.instance
    p = planner.describe_plan(inst, args.text, root=ROOT)
    if p.refused:
        show_plan(p)
        return 1
    show_plan(p)
    if args.dry_run:
        return 0
    if not args.yes and not confirm(
            f'Set the description of radiod@{inst} (restarts it if running)?'):
        print('declined; nothing written.')
        return 2
    unit = f'radiod@{inst}.service'
    with lock(f'admin radiod describe {inst}'):
        active = is_active(unit)
        try:
            for e in p.edits:
                write_atomic(e.path, e.after)
        except Exception as e:
            err(f'describe failed: {e}')
            _print_written([])
            return 4
        restart_failed = active and systemctl('restart', unit) != 0
    if restart_failed:
        print(f'description set, but these units did not start: {unit}')
        return 5
    print(f'radiod@{inst}: description set'
          + ('; restarted.' if active else '; not running, left stopped.'))
    return 0
