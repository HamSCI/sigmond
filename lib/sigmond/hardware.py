"""Hardware-readiness probes (CONTRACT §3 / install-orchestration Phase D).

A hardware-gated client reports its own readiness via the top-level
``hardware_present`` field of ``<client> inventory --json`` — the client
detects its own hardware (CONTRACT §3) rather than sigmond hard-coding USB
IDs.  :func:`hardware_ready` consults that self-describe first and falls back
to a per-client lsusb probe only while a client has not yet emitted the field,
so detection keeps working across the transition.

Tri-state throughout:
  * ``True``  — the client's required hardware is present (or the client is in
    a no-hardware mode, e.g. mag-recorder's simulator, and can still produce).
  * ``False`` — the client requires hardware that is absent.
  * ``None``  — not hardware-gated, the client doesn't implement the field and
    has no fallback, or readiness could not be determined.  Callers must treat
    ``None`` as "don't gate / unknown", never as absent.
"""
from __future__ import annotations

import enum
import glob
import json
import os
import re
import select
import shutil
import subprocess
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


class Presence(enum.Enum):
    """Tri-state (plus NA) hardware-presence vocabulary for UIs.

    Mirrors :func:`hardware_ready`'s ``True / False / None`` tri-state and
    adds ``NA`` for "no hardware prerequisite, nothing was probed".
    """

    YES = "yes"
    NO = "no"
    NA = "na"
    UNKNOWN = "unknown"

    @classmethod
    def from_tristate(cls, value: Optional[bool]) -> "Presence":
        if value is True:
            return cls.YES
        if value is False:
            return cls.NO
        return cls.UNKNOWN


def _lsusb() -> str:
    try:
        return subprocess.run(["lsusb"], capture_output=True, text=True,
                              timeout=5).stdout
    except Exception:                                  # noqa: BLE001
        return ""


def _magnetometer_present() -> bool:
    """RM3100 via its Pololu USB-I2C adapter (udev symlink or 1ffb:2502/2503)."""
    if os.path.exists("/dev/ttyMAG0"):
        return True
    return bool(re.search(r"1ffb:250[23]|pololu", _lsusb(), re.I))


def _sdr_present() -> bool:
    """RX888 / RX888mk2 — a Cypress FX3 (04b4:00bc/00f0/00f1/00f3) on the USB bus.

    Keep the PID set in sync with discovery/usb_sdr.py's KNOWN_SDR_DEVICES
    (they had drifted: this regex lacked 00bc, that table lacked 00f0).
    """
    return bool(re.search(r"rx888|04b4:(00bc|00f[013])", _lsusb(), re.I))


def _gpsdo_present() -> bool:
    """Leo Bodnar GPSDO (USB/HID) — VID 1dd2 (LBE-1420/1421/mini) on the bus."""
    return bool(re.search(r"1dd2:|leo bodnar", _lsusb(), re.I))


# Legacy lsusb fallbacks, keyed by client/component name.  Consulted ONLY when
# the client's `inventory --json` does not report `hardware_present` (or has no
# inventory CLI — e.g. upstream ka9q-radio, whose SDR sigmond must detect
# itself).  As each client emits the field, its entry here becomes vestigial.
_LEGACY_PROBES: dict[str, Callable[[], bool]] = {
    "mag-recorder":  _magnetometer_present,
    "gpsdo-monitor": _gpsdo_present,
    "ka9q-radio":    _sdr_present,
}


def inventory_hardware_present(client: str) -> Optional[bool]:
    """Read ``hardware_present`` from ``<client> inventory --json``.

    Returns the reported bool, or ``None`` when the client has no CLI on PATH,
    the call fails, the JSON is unparseable, or the field is absent/non-bool."""
    exe = shutil.which(client)
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "inventory", "--json"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
    except Exception:                                  # noqa: BLE001
        return None
    val = data.get("hardware_present") if isinstance(data, dict) else None
    return val if isinstance(val, bool) else None


def hardware_ready(client: str) -> Optional[bool]:
    """Tri-state hardware readiness for ``client`` (see module docstring).

    The client's own ``inventory --json hardware_present`` is authoritative;
    the lsusb fallback applies only when the client doesn't report it."""
    val = inventory_hardware_present(client)
    if val is not None:
        return val
    probe = _LEGACY_PROBES.get(client)
    return probe() if probe else None


# ---------------------------------------------------------------------------
# Bring-up hardware gate (shared by `smd bringup` and the TUI Greenfield
# screen).  These moved here VERBATIM from bin/smd so the wizard's equipment
# panel reports exactly what bring-up will decide — there is one
# implementation, not two.  Stdlib-only: bin/smd imports this module.
# ---------------------------------------------------------------------------


def detect_local_sdr() -> bool:
    """True when a radiod-capable SDR is present.  The upfront check for a
    local-radiod profile — hardware, not whether ka9q-radio is built yet.
    Delegates to the shared readiness probe (CONTRACT §3 / Phase D); ka9q-radio
    has no inventory CLI so this resolves via the lsusb fallback (RX888 =
    Cypress FX3 04b4:00f0/00f1/00f3).  Unknown (None) is treated as absent —
    don't claim a local radiod stack without a confirmed SDR."""
    return hardware_ready('ka9q-radio') is True


def detect_magnetometer() -> bool:
    """True when mag-recorder's data source is available, per its own
    `inventory --json hardware_present` self-describe (CONTRACT §3 / Phase D),
    falling back to the Pololu USB-I2C / `/dev/ttyMAG0` lsusb probe.  Unknown
    (None) is treated as absent — don't scaffold a client whose hardware we
    can't confirm."""
    return hardware_ready('mag-recorder') is True


def detect_gpsdo() -> bool:
    """True when a Leo Bodnar GPSDO is attached, per gpsdo-monitor's own
    `inventory --json hardware_present` self-describe (CONTRACT §3 / Phase D),
    falling back to the `1dd2:` USB/HID lsusb probe.  Unknown (None) is treated
    as absent — don't scaffold gpsdo-monitor for hardware we can't confirm."""
    return hardware_ready('gpsdo-monitor') is True


def gpsdo_fix():
    """(has_fix, grid, fix_str) for the GPSDO.

    Prefer gpsdo-monitor's published /run/gpsdo (fast, authoritative once the
    service runs).  Fall back to reading the GPSDO's NMEA DIRECTLY off its CDC tty
    so the bring-up gate + grid-derivation work BEFORE gpsdo-monitor is installed —
    otherwise a physically-locked GPSDO reads as 'no fix' on a fresh host (the bug
    the walk-through caught).  Both paths need root, so bringup elevates before the
    gate."""
    for f in glob.glob('/run/gpsdo/*.json'):
        if f.endswith('index.json'):
            continue
        try:
            h = json.load(open(f)).get('health', {})
        except Exception:   # noqa: BLE001
            continue
        fix = h.get('gps_fix')
        if fix:                       # monitor is up and publishing
            return (fix in ('2D', '3D'), h.get('grid'), fix)
    return gpsdo_fix_direct()


def gpsdo_fix_direct():
    """Sample the GPSDO's NMEA straight off its CDC tty — no gpsdo-monitor needed.
    Locates the Leo Bodnar (USB vid 1dd2) tty, reads a few seconds of NMEA, returns
    (has_fix, grid, fix_str).  Best-effort: (False, None, None) if the device isn't
    found or can't be read (e.g. an unprivileged dry-run)."""
    tty = None
    for node in sorted(glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*')):
        try:
            props = subprocess.run(
                ['udevadm', 'info', '-q', 'property', '-n', node],
                capture_output=True, text=True, check=False).stdout
        except Exception:   # noqa: BLE001
            continue
        if 'ID_VENDOR_ID=1dd2' in props:   # Leo Bodnar LBE-1421
            tty = node
            break
    if tty is None:
        return (False, None, None)
    subprocess.run(['stty', '-F', tty, 'raw', '-echo', '9600'], check=False)
    sys.modules.setdefault('hid', types.ModuleType('hid'))  # nmea.py imports hid_xport
    try:
        sys.path.insert(0, '/opt/git/sigmond/gpsdo-monitor/src')
        from gpsdo_monitor.nmea import NmeaState, feed  # feed() needs no pyserial
    except Exception:   # noqa: BLE001
        return (False, None, None)
    st = NmeaState()
    try:
        fd = os.open(tty, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
    except OSError:
        return (False, None, None)
    buf = b''
    deadline = time.monotonic() + 5.0
    try:
        while time.monotonic() < deadline:
            r, _, _ = select.select([fd], [], [], 0.5)
            if not r:
                continue
            try:
                chunk = os.read(fd, 512)
            except OSError:
                break
            if not chunk:
                continue
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                try:
                    feed(st, line.decode('ascii', 'ignore').strip())
                except Exception:   # noqa: BLE001
                    pass
            if st.gps_fix in ('2D', '3D') and st.latitude is not None:
                break                       # have fix + position, done
    finally:
        os.close(fd)
    fix = st.gps_fix
    return (fix in ('2D', '3D'), st.maidenhead(), fix)


def rm3100_responds() -> Optional[bool]:
    """Functional magnetometer check: does the RM3100 actually ANSWER on the I2C
    bus (0x23), not merely the Pololu adapter being present?  A USB-presence
    probe passes when the adapter is plugged in but the sensor behind it is
    dead/unpowered/unwired (exactly the B4-100 case), so bring-up must probe the
    sensor itself.  Returns True (responding), False (adapter present but sensor
    silent), or None (can't tell — no /dev/ttyMAG0, or mag-usb not installed
    yet, so the caller falls back to presence)."""
    magusb, dev = Path('/usr/local/bin/mag-usb'), Path('/dev/ttyMAG0')
    if not magusb.exists() or not dev.exists():
        return None
    r = subprocess.run(['timeout', '8', str(magusb), '-A', '0x23'],
                       capture_output=True, text=True)
    out = ((getattr(r, 'stdout', '') or '') + (getattr(r, 'stderr', '') or '')).lower()
    if '"x":' in out or 'revid' in out:
        return True
    if 'nack' in out or 'not respond' in out or 'did not respond' in out:
        return False
    return None


@dataclass(frozen=True)
class GateCheck:
    """One row of the bring-up "required external devices" inventory.

    ``detected`` is the same tri-state bring-up prints: True (present /
    ✓), False (MISSING / ✗ — hard-stops under ``--require-hardware``),
    None (unconfirmed / ?).  ``hint`` is the operator fix-it line.
    """

    label: str
    detected: Optional[bool]
    hint: str

    @property
    def presence(self) -> Presence:
        return Presence.from_tristate(self.detected)


def gate_checks(prof, local: bool) -> list:
    """Assemble the bring-up hardware inventory for ``prof`` — the exact rows
    ``smd bringup`` prints under "required external devices", and the exact
    tri-state its ``--require-hardware`` gate acts on.

    Extracted from ``bin/smd:_bringup_hardware_gate`` so the TUI Greenfield
    wizard can show the operator the SAME verdict without re-implementing it.
    ``bin/smd`` keeps the printing and the abort decision; this is only the
    probe-and-decide half.  Returns a list of :class:`GateCheck`.
    """
    checks = []
    if local:
        checks.append(GateCheck('RX888 SDR', detect_local_sdr(),
                                'attach the RX888 (Cypress FX3) on a USB-3 port'))
    if 'gpsdo-monitor' in getattr(prof, 'local_radiod_infra', ()) or local:
        present = detect_gpsdo()
        has_fix, grid, fixstr = gpsdo_fix()
        if present and not has_fix:
            # On the bus but not located — the exact 'connect the antenna' case.
            checks.append(GateCheck(
                'GPSDO fix', False,
                f'GPSDO present but NO GPS fix (gps_fix={fixstr or "none"}) — '
                'connect the GPS antenna: no fix means an undisciplined '
                'RX888 clock and no auto-derived location'))
        elif present and has_fix:
            checks.append(GateCheck(f'GPSDO fix [{grid or "?"}]', True, 'located'))
        else:
            checks.append(GateCheck(
                'GPSDO (Leo Bodnar)', present,
                'attach the GPSDO on USB — without a fix the RX888 runs '
                'undisciplined and location cannot be auto-derived'))
    if 'mag-recorder' in getattr(prof, 'clients', ()):
        present, responds = detect_magnetometer(), rm3100_responds()
        if present and responds is False:
            checks.append(GateCheck(
                'Magnetometer (RM3100)', False,
                'Pololu adapter present but the RM3100 is NOT answering '
                'at 0x23 — check the sensor wiring/power'))
        else:
            checks.append(GateCheck(
                'Magnetometer (RM3100)', present,
                'attach the RM3100 via the Pololu USB-I2C adapter'))
    return checks
