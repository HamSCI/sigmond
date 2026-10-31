"""Which RX888 is which radiod: USB-present cards against radiod confs.

Pure logic, no printing and no subprocess.  A host with more than one RX888
must pin each `radiod@<id>.conf` to a card with `serial = "..."` in its
[rx888] section, or a reboot can swap antennas.  Serials compare upper-cased
and stripped.  Port of wsprdaemon's `wd -X` report.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_VID = '04b4'
_PID_PROGRAMMED = '00f1'
_PID_BOOTLOADER = '00f3'
_MIN_SPEED_MBPS = 5000


@dataclass(frozen=True)
class PresentCard:
    serial: str
    devpath: str
    speed: str
    programmed: bool


def _norm(serial) -> str:
    return str(serial or '').strip().upper()


def present_cards(root: Path = Path('/sys/bus/usb/devices')) -> list[PresentCard]:
    """Every RX888 on the USB bus (00f1 programmed, 00f3 bootloader), in
    sorted sysfs directory order."""
    try:
        entries = sorted(Path(root).iterdir())
    except OSError:
        return []
    cards = []
    for dev in entries:
        def _read(name):
            try:
                return (dev / name).read_text().strip()
            except OSError:
                return ''
        if _read('idVendor').lower() != _VID:
            continue
        pid = _read('idProduct').lower()
        if pid not in (_PID_PROGRAMMED, _PID_BOOTLOADER):
            continue
        if not _read('busnum'):
            continue
        programmed = pid == _PID_PROGRAMMED
        cards.append(PresentCard(
            serial=_norm(_read('serial')) if programmed else '',
            devpath=dev.name,
            speed=_read('speed'),
            programmed=programmed))
    return cards


def _unquote(value: str) -> str:
    if value[:1] in ('"', "'"):
        end = value.find(value[0], 1)
        return value[1:end] if end > 0 else value[1:]
    return value.split('#', 1)[0].strip()


def _section_value(text: str, key: str) -> str:
    """Raw uncommented `key = value` in the [rx888] section, "" if none."""
    pat = re.compile(rf'^{key}\s*=\s*(.*)$')
    in_rx888 = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('['):
            in_rx888 = line.split('#', 1)[0].strip() == '[rx888]'
            continue
        if in_rx888:
            m = pat.match(line)
            if m:
                return _unquote(m.group(1).strip()).strip()
    return ''


def has_rx888_section(text: str) -> bool:
    return any(l.strip().split('#', 1)[0].strip() == '[rx888]'
               for l in text.splitlines())


def conf_serial(text: str) -> str:
    """The uncommented `serial =` in the [rx888] section, upper-cased."""
    return _norm(_section_value(text, 'serial'))


def conf_description(text: str) -> str:
    """The [rx888] section's `description =` value, "" if none."""
    return _section_value(text, 'description')


def instance_serials(confdir: Path = Path('/etc/radio'), coord=None) -> dict[str, str]:
    """{instance_id: serial} for every radiod@*.conf with an [rx888] section.
    The conf's serial wins, else coord.radiods[id].sdr_serial, else ""."""
    out = {}
    try:
        confs = sorted(Path(confdir).glob('radiod@*.conf'))
    except OSError:
        return {}
    for conf in confs:
        inst = conf.name[len('radiod@'):-len('.conf')]
        try:
            text = conf.read_text()
        except OSError:
            continue
        if not has_rx888_section(text):
            continue
        serial = conf_serial(text)
        if not serial and coord is not None:
            rd = coord_radiod_for(coord, inst)
            serial = _norm(getattr(rd, 'sdr_serial', '')) if rd else ''
        out[inst] = serial
    return out


def coord_radiod_for(coord, instance: str):
    """The coordination radiod for radiod@<instance>.conf: the one whose
    `radio_conf` names that file, else the one keyed or id'd `instance`,
    else None.  Coordination keys a radiod by its id or by its status DNS
    name, so the conf path is the reliable link."""
    radiods = getattr(coord, 'radiods', None) or {}
    name = f'radiod@{instance}.conf'
    for rd in radiods.values():
        rc = str(getattr(rd, 'radio_conf', '') or '').strip()
        if rc == name or rc.endswith('/' + name):
            return rd
    for key, rd in radiods.items():
        if key == instance or getattr(rd, 'id', None) == instance:
            return rd
    return None


def conf_pinned_serials(confdir: Path = Path('/etc/radio')) -> dict[str, str]:
    """{instance_id: serial} from the conf alone (what radiod reads): ""
    when the [rx888] section has no live `serial =` line."""
    return instance_serials(confdir, None)


def sysfs_serial_for(bus, dev, root: Path = Path('/sys/bus/usb/devices')) -> str:
    """The USB iSerial of the device at bus/dev, from the world-readable
    sysfs tree, upper-cased; "" when there is none or no such device."""
    try:
        want = (int(bus), int(dev))
    except (TypeError, ValueError):
        return ''
    try:
        entries = sorted(Path(root).iterdir())
    except OSError:
        return ''
    for d in entries:
        try:
            got = (int((d / 'busnum').read_text().strip()),
                   int((d / 'devnum').read_text().strip()))
        except (OSError, ValueError):
            continue
        if got == want:
            try:
                return _norm((d / 'serial').read_text())
            except OSError:
                return ''
    return ''


_USB_FD = re.compile(r'^/dev/bus/usb/(\d{3})/(\d{3})$')


def held_serial(fd_dir: Path, sysfs_root: Path = Path('/sys/bus/usb/devices')) -> str:
    """The serial of the USB device a process holds open, from its
    /proc/<pid>/fd links; "" when unreadable (another user's process
    without root) or none is a /dev/bus/usb node."""
    try:
        fds = sorted(Path(fd_dir).iterdir())
    except OSError:
        return ''
    for fd in fds:
        try:
            m = _USB_FD.match(os.readlink(fd))
        except OSError:
            continue
        if m:
            serial = sysfs_serial_for(int(m.group(1)), int(m.group(2)),
                                      sysfs_root)
            if serial:
                return serial
    return ''


def serial_to_instance(instances: dict[str, str]) -> dict[str, str]:
    return {s: i for i, s in instances.items() if s}


@dataclass
class SdrReport:
    cards: list[PresentCard]
    instances: dict[str, str]
    problems: list[str] = field(default_factory=list)


def report(cards: list[PresentCard], instances: dict[str, str],
           pinned: dict[str, str] | None = None, *,
           held: dict[str, str] | None = None) -> SdrReport:
    """`instances` may carry coordination labels; `pinned` (from
    conf_pinned_serials) decides the swap risk from the conf alone, since
    that is what radiod reads.  Without `pinned`, `instances` decides it.
    `held` ({instance: serial} a running radiod has open) counts a card as
    claimed even when its conf does not name it.  More than one conf, or
    more than one programmed card, makes it a multi-card host."""
    problems = []
    n_programmed = sum(1 for c in cards if c.programmed)
    multi = len(instances) > 1 or n_programmed > 1
    held_serials = {_norm(s) for s in (held or {}).values() if s}
    on_bus = {c.serial for c in cards if c.programmed and c.serial}
    pins = instances if pinned is None else \
        {i: pinned.get(i, '') for i in instances}

    if multi:
        for inst in sorted(i for i, s in pins.items() if not s):
            problems.append(
                f"radiod@{inst} has no 'serial =' line, so it opens whichever "
                f"RX888 is free: a reboot can swap antennas")
    for inst in sorted(instances):
        s = instances[inst]
        if s and s not in on_bus:
            problems.append(
                f"radiod@{inst} wants RX888 {s}, which is not on the USB bus")
    by_serial: dict[str, list[str]] = {}
    for inst, s in instances.items():
        if s:
            by_serial.setdefault(s, []).append(inst)
    for s in sorted(by_serial):
        if len(by_serial[s]) > 1:
            problems.append(
                f"RX888 {s} is named by more than one radiod: "
                f"{','.join(sorted(by_serial[s]))}")
    programmed = sorted((c for c in cards if c.programmed and c.serial),
                        key=lambda c: c.serial)
    if multi:
        for c in programmed:
            if c.serial not in by_serial and c.serial not in held_serials:
                problems.append(
                    f"RX888 {c.serial} on {c.devpath} is not named by any "
                    f"radiod conf")
    if n_programmed > len(instances):
        problems.append(
            f"{n_programmed} RX888(s) on the bus but only {len(instances)} "
            f"radiod conf(s): run `smd config init radiod` to register the rest")
    for c in programmed:
        try:
            slow = int(c.speed) < _MIN_SPEED_MBPS
        except ValueError:
            slow = False
        if slow:
            problems.append(
                f"RX888 {c.serial} on {c.devpath} is at only {c.speed} Mb/s, "
                f"so radiod will not open it")
    n = sum(1 for c in cards if not c.programmed)
    if n:
        problems.append(
            f"{n} RX888(s) still in the FX3 bootloader (no firmware yet, so "
            f"no serial)")
    return SdrReport(cards=cards, instances=instances, problems=problems)


# ---- identify: unplug-walk helpers (injected clock/sleep, no I/O) ------------

POLL_SECS = 0.25


class AmbiguousDrop(Exception):
    """More than one serial vanished in a single poll."""

    def __init__(self, serials):
        self.serials = sorted(serials)
        super().__init__(', '.join(self.serials))


def wait_for_drop(before: set[str], poll: Callable[[], set[str]],
                  deadline: float, clock=time.monotonic,
                  sleep=time.sleep) -> str | None:
    """The one serial in `before` that `poll()` no longer shows; None at the
    deadline; AmbiguousDrop when several vanish in the same poll."""
    while True:
        gone = set(before) - set(poll())
        if len(gone) == 1:
            return next(iter(gone))
        if gone:
            raise AmbiguousDrop(gone)
        if clock() >= deadline:
            return None
        sleep(POLL_SECS)


def wait_for_present(serials: set[str], poll: Callable[[], set[str]],
                     deadline: float, clock=time.monotonic,
                     sleep=time.sleep) -> bool:
    """True once every serial in `serials` is in `poll()`."""
    while True:
        if set(serials) <= set(poll()):
            return True
        if clock() >= deadline:
            return False
        sleep(POLL_SECS)


def wait_for_return(serial: str, poll, deadline: float,
                    clock=time.monotonic, sleep=time.sleep) -> bool:
    return wait_for_present({serial}, poll, deadline, clock, sleep)


# A table header at column 0: `[a.b]` or `[[a.b]]`, maybe a trailing comment.
_HEADER = re.compile(r'^\[\[?[^\]]+\]\]?\s*(#.*)?$')
_RADIOD_HEADER = re.compile(
    r"""^\[\s*radiod\s*\.\s*(?:"([^"]*)"|'([^']*)'|([A-Za-z0-9_-]+))\s*\]$""")


def _is_radiod_table(header: str, instance: str) -> bool:
    """True for `[radiod.<id>]`, `[radiod."<id>"]` or `[radiod.'<id>']`."""
    m = _RADIOD_HEADER.match(header)
    return bool(m) and next(g for g in m.groups() if g is not None) == instance


def _pin_in_table(text: str, in_table, key_re: str, new_line: str,
                  accept_commented: bool) -> str | None:
    """Replace the key line inside the table `in_table(header)` selects, or
    insert `new_line` right after its header.  None if no such table."""
    lines = text.splitlines(keepends=True)
    start = None
    end = len(lines)
    for i, raw in enumerate(lines):
        if not _HEADER.match(raw.rstrip('\r\n')):
            continue
        if start is not None:
            end = i
            break
        if in_table(raw.split('#', 1)[0].strip()):
            start = i
    if start is None:
        return None
    live = re.compile(rf'^\s*{key_re}\s*=')
    cmt = re.compile(rf'^\s*#\s*{key_re}\s*=')
    hit = next((i for i in range(start + 1, end) if live.match(lines[i])), None)
    if hit is None and accept_commented:
        hit = next((i for i in range(start + 1, end) if cmt.match(lines[i])), None)
    nl = '\n'
    if hit is not None:
        lines[hit] = new_line + nl
    else:
        if not lines[start].endswith(('\n', '\r')):
            lines[start] += nl
        lines.insert(start + 1, new_line + nl)
    return ''.join(lines)


def set_conf_serial(text: str, serial: str) -> str:
    """Pin `serial = "<serial>"` in the [rx888] section (replacing a
    commented or live line, else inserting it first)."""
    out = _pin_in_table(text, lambda h: h == '[rx888]', 'serial',
                        f'serial      = "{_norm(serial)}"', True)
    if out is None:
        raise ValueError('no [rx888] section')
    return out


_RADIO_CONF_LINE = re.compile(
    r"""^\s*radio_conf\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def _radiod_header_by_conf(text: str, instance: str) -> str | None:
    """The header of the first [radiod.*] table whose `radio_conf` names
    radiod@<instance>.conf, else None."""
    name = f'radiod@{instance}.conf'
    header = None
    for raw in text.splitlines():
        line = raw.rstrip('\r\n')
        if _HEADER.match(line):
            h = line.split('#', 1)[0].strip()
            header = h if _RADIOD_HEADER.match(h) else None
            continue
        m = _RADIO_CONF_LINE.match(line)
        if header and m:
            value = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
            if value == name or value.endswith('/' + name):
                return header
    return None


def set_coord_serial(text: str, instance: str, serial: str) -> str:
    """Pin `sdr_serial` in the [radiod.<instance>] table of coordination.toml
    (bare or quoted key, as `smd config init radiod` writes it), else in the
    [radiod.*] table whose `radio_conf` names radiod@<instance>.conf (the
    bring-up path keys the table by the status DNS name)."""
    line = f'sdr_serial  = "{_norm(serial)}"'
    out = _pin_in_table(text, lambda h: _is_radiod_table(h, instance),
                        'sdr_serial', line, False)
    if out is None:
        header = _radiod_header_by_conf(text, instance)
        if header is not None:
            out = _pin_in_table(text, lambda h: h == header,
                                'sdr_serial', line, False)
    if out is None:
        raise ValueError(f'no [radiod.{instance}] table')
    return out
