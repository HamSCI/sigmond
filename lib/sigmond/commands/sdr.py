"""`smd admin sdr {list|identify}`: which RX888 is which radiod.

Reading /sys and /etc/radio is unprivileged, so `list` needs no root.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import time
from pathlib import Path

from sigmond import sdr_map
from sigmond.paths import COORDINATION_PATH
from sigmond.ui import err, heading, warn

# Module attributes so tests can point them elsewhere.
SYSFS_ROOT = Path('/sys/bus/usb/devices')
CONFDIR = Path('/etc/radio')
COORD_PATH = COORDINATION_PATH
_clock = time.monotonic
_sleep = time.sleep
WAIT_SECS = 180


def load_coord():
    try:
        from sigmond.coordination import load_coordination
        return load_coordination()
    except Exception:
        return None


def unit_state(inst: str) -> str:
    """`systemctl is-active radiod@<inst>`, `unknown` when it cannot run."""
    try:
        r = subprocess.run(['systemctl', 'is-active', f'radiod@{inst}'],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return 'unknown'
    return r.stdout.strip() or 'unknown'


def _antenna(inst: str) -> str:
    try:
        return sdr_map.conf_description(
            (CONFDIR / f'radiod@{inst}.conf').read_text())
    except OSError:
        return ''


def _table(headers, rows) -> list[str]:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    fmt = '  '.join(f'{{:<{w}}}' for w in widths)
    return [fmt.format(*headers).rstrip()] + \
           [fmt.format(*map(str, r)).rstrip() for r in rows]


def cmd_sdr_list(args) -> int:
    cards = sdr_map.present_cards(SYSFS_ROOT)
    instances = sdr_map.instance_serials(CONFDIR, load_coord())
    pinned = sdr_map.conf_pinned_serials(CONFDIR)
    rep = sdr_map.report(cards, instances, pinned)
    opened = sdr_map.serial_to_instance(instances)
    rc = 1 if rep.problems else 0

    if getattr(args, 'json', False):
        print(json.dumps({
            'cards': [dict(dataclasses.asdict(c),
                           opened_by=opened.get(c.serial, ''))
                      for c in cards],
            'instances': instances,
            'conf_pinned': pinned,
            'problems': rep.problems,
        }, indent=2))
        return rc

    on_bus = {c.serial for c in cards if c.programmed}
    heading('RX888 cards on the USB bus')
    rows = [(c.serial if c.programmed else '(bootloader)', c.devpath,
             c.speed or '-', opened.get(c.serial, '') or '-') for c in cards]
    for line in (_table(['SERIAL', 'USB', 'SPEED', 'OPENED BY'], rows)
                 if rows else ['(none)']):
        print(line)
    print()
    heading('radiod instances')

    def _label(inst, s):
        if s and not pinned.get(inst):
            return f'{s} (coordination only, conf unpinned)'
        return s or '-'
    rows = [(inst, unit_state(inst), _label(inst, s),
             ('yes' if s in on_bus else 'no') if s else '-', _antenna(inst))
            for inst, s in sorted(instances.items())]
    for line in (_table(['INSTANCE', 'STATE', 'SERIAL', 'ON BUS', 'ANTENNA'], rows)
                 if rows else ['(none)']):
        print(line)
    print()
    if rep.problems:
        print('Needs attention:')
        for p in rep.problems:
            print(f'- {p}')
    else:
        print('Every local radiod names its own RX888 and every RX888 is on the bus.')
    return rc


def _systemctl(*args) -> int:
    try:
        return subprocess.run(['systemctl', *args], timeout=60).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def _is_active(unit: str) -> bool:
    """True when `systemctl is-active <unit>` says active."""
    try:
        r = subprocess.run(['systemctl', 'is-active', unit],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.stdout.strip() == 'active'


def present_serials() -> set[str]:
    return {c.serial for c in sdr_map.present_cards(SYSFS_ROOT)
            if c.programmed and c.serial}


def _wait_secs(args=None) -> float:
    """--wait-secs, else SIGMOND_SDR_IDENTIFY_WAIT_SECS, else 180."""
    flag = getattr(args, 'wait_secs', None)
    if flag is not None:
        return float(flag)
    try:
        return float(os.environ.get('SIGMOND_SDR_IDENTIFY_WAIT_SECS', WAIT_SECS))
    except ValueError:
        return WAIT_SECS


def _walk(instances: list[str], wait: float | None = None) -> dict[str, str] | None:
    """Unplug each card in turn; {instance: serial}, or None if abandoned."""
    if wait is None:
        wait = _wait_secs()
    found: dict[str, str] = {}
    for inst in instances:
        desc = _antenna(inst) or inst
        while True:
            before = present_serials()
            print(f'Unplug the RX888 on: {desc}  (radiod@{inst})')
            try:
                serial = sdr_map.wait_for_drop(
                    before, present_serials, _clock() + wait, _clock, _sleep)
            except sdr_map.AmbiguousDrop as e:
                print(f'More than one card dropped at once: {", ".join(e.serials)}')
                print('Plug them all back in; I will ask again.')
                if not sdr_map.wait_for_present(before, present_serials,
                                                _clock() + wait, _clock, _sleep):
                    return None
                continue
            if serial is None:
                return None
            print(f'  RX888 {serial} went away.  Plug it back in.')
            if not sdr_map.wait_for_return(serial, present_serials,
                                           _clock() + wait, _clock, _sleep):
                return None
            if serial in found.values():
                print(f'  {serial} was already matched to another radiod; '
                      f'unplug a different card.')
                continue
            found[inst] = serial
            break
    return found


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + '.sdr-identify.tmp')
    try:
        tmp.write_text(text)
        try:
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _write_pins(found: dict[str, str]) -> bool:
    """Pin every conf and coordination.toml, or none: all new texts are
    computed first.  False if anything could not be computed or written."""
    plan: list[tuple[Path, str]] = []
    try:
        for inst, serial in sorted(found.items()):
            conf = CONFDIR / f'radiod@{inst}.conf'
            plan.append((conf, sdr_map.set_conf_serial(conf.read_text(), serial)))
        try:
            coord_text = COORD_PATH.read_text()
        except OSError as e:
            warn(f'{COORD_PATH}: {e}; coordination left alone')
            coord_text = None
        if coord_text is not None:
            for inst, serial in sorted(found.items()):
                try:
                    coord_text = sdr_map.set_coord_serial(coord_text, inst, serial)
                except ValueError:
                    warn(f'no [radiod.{inst}] table in {COORD_PATH}; '
                         f'coordination left alone for it')
            plan.append((COORD_PATH, coord_text))
    except (OSError, ValueError) as e:
        err(f'cannot pin every conf ({e}); nothing written')
        return False
    for path, text in plan:
        try:
            _write_atomic(path, text)
        except OSError as e:
            err(f'{path}: {e}')
            return False
    return True


def cmd_sdr_identify(args) -> int:
    dry = getattr(args, 'dry_run', False)
    if not dry and os.geteuid() != 0:
        err('identify writes /etc/radio and restarts radiod; run it as root')
        return 1
    instances = sorted(sdr_map.instance_serials(CONFDIR, load_coord()))
    if not instances:
        err(f'no radiod conf with an [rx888] section in {CONFDIR}; '
            f'run `smd config init radiod` first')
        return 1
    if len(instances) < 2:
        err('one radiod needs no identify; its conf is unbound by design')
        return 1
    if len(present_serials()) < len(instances):
        err(f'{len(instances)} radiod instances but fewer RX888 cards on the '
            f'bus; plug them all in first')
        return 1

    units = [f'radiod@{i}.service' for i in instances]
    timer = 'sigmond-radiod-watchdog.timer'
    # Restart afterwards only what was running before; a watchdog run in
    # progress is stopped too so it cannot restart a radiod mid-walk.
    was_active = [u for u in (*units, timer) if _is_active(u)]
    for u in ('sigmond-radiod-watchdog.service', timer, *units):
        _systemctl('stop', u)
    try:
        found = _walk(instances, _wait_secs(args))
        if found is None:
            print('Walk abandoned: no card change seen in time; nothing written.')
            return 3
        heading('Mapping')
        rows = [(i, _antenna(i) or '-', found[i]) for i in instances]
        for line in _table(['INSTANCE', 'ANTENNA', 'SERIAL'], rows):
            print(line)
        # A replug can bring a card back at a slower USB speed.
        for p in sdr_map.report(sdr_map.present_cards(SYSFS_ROOT),
                                found).problems:
            warn(p)
        if dry:
            print('(dry run: nothing written)')
        elif not _write_pins(found):
            return 4
        return 0
    finally:
        for u in was_active:
            _systemctl('start', u)
