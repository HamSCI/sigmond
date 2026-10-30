"""Which RX888 is which radiod: USB-present cards against radiod confs.

Pure logic, no printing and no subprocess.  A host with more than one RX888
must pin each `radiod@<id>.conf` to a card with `serial = "..."` in its
[rx888] section, or a reboot can swap antennas.  Serials compare upper-cased
and stripped.  Port of wsprdaemon's `wd -X` report.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

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
            rd = getattr(coord, 'radiods', {}).get(inst)
            serial = _norm(getattr(rd, 'sdr_serial', '')) if rd else ''
        out[inst] = serial
    return out


def serial_to_instance(instances: dict[str, str]) -> dict[str, str]:
    return {s: i for i, s in instances.items() if s}


@dataclass
class SdrReport:
    cards: list[PresentCard]
    instances: dict[str, str]
    problems: list[str] = field(default_factory=list)


def report(cards: list[PresentCard], instances: dict[str, str]) -> SdrReport:
    problems = []
    multi = len(instances) > 1
    on_bus = {c.serial for c in cards if c.programmed and c.serial}

    if multi:
        for inst in sorted(i for i, s in instances.items() if not s):
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
            if c.serial not in by_serial:
                problems.append(
                    f"RX888 {c.serial} on {c.devpath} is not named by any "
                    f"radiod conf")
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
