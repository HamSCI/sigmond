"""The wizard reads the LBE-Mini's position itself, over USB HID.

The Mini (1dd2:2211) is HID-only -- UBX on interrupt-IN, no serial node -- so
the wizard's NMEA path cannot read it.  It used to ask gpsdo-monitor in the
decoder VM, which on a greenfield first boot is not running yet: it is
installed by the bring-up the operator is still answering questions for.  So
the "a live GPSDO fix IS the answer" skip never fired on the commonest DASI
GPSDO and the operator typed a grid the hardware already knew (AI6VN v3.83
bring-up, 2026-10-29).

The helper is EMBEDDED in the wizard because pm-align refreshes a fixed set of
host scripts; a separate file would never reach a host updated that way.
These tests extract it from the wizard, so the wizard is the single source.
"""
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"


def load_helper() -> types.ModuleType:
    text = WIZARD.read_text()
    m = re.search(r"cat >\"\$helper\" <<'MINIGRIDEOF'\n(.*?)\nMINIGRIDEOF\n",
                  text, re.S)
    assert m, "the embedded LBE-Mini helper is not in the wizard"
    src = m.group(1)
    mod = types.ModuleType("mini_grid")
    mod.__dict__["__name__"] = "mini_grid_under_test"   # keep __main__ inert
    exec(compile(src, "<wizard:mini_grid>", "exec"), mod.__dict__)
    return mod


HELPER = load_helper()


def ubx_message(cls: int, mid: int, payload: bytes) -> bytes:
    body = bytes([cls, mid]) + len(payload).to_bytes(2, "little") + payload
    a = b = 0
    for byte in body:
        a = (a + byte) & 0xFF
        b = (b + a) & 0xFF
    return b"\xb5\x62" + body + bytes([a, b])


def nav_pvt(lat: float, lon: float, fix_type: int = 3, sats: int = 11) -> bytes:
    p = bytearray(92)
    p[20] = fix_type
    p[23] = sats
    p[24:28] = int(round(lon * 1e7)).to_bytes(4, "little", signed=True)
    p[28:32] = int(round(lat * 1e7)).to_bytes(4, "little", signed=True)
    return bytes(p)


def hid_frames(stream: bytes, carries: bool = True) -> list[bytes]:
    """Chunk a UBX byte stream into interrupt-IN frames: signal-loss byte,
    status byte (0x80 = carries UBX), then 62 bytes of payload."""
    out, status = [], 0x80 if carries else 0x00
    for i in range(0, len(stream), 62):
        chunk = stream[i:i + 62]
        out.append(bytes([0, status]) + chunk + b"\x00" * (62 - len(chunk)))
    return out


def feed(frames):
    buf, got = b"", []
    for raw in frames:
        carries, payload = HELPER.decode_frame(raw)
        if not carries:
            continue
        buf += payload
        msgs, buf = HELPER.iter_messages(buf)
        got.extend(msgs)
    return got


def test_recovers_a_nav_pvt_from_hid_frames():
    msgs = feed(hid_frames(ubx_message(0x01, 0x07, nav_pvt(38.9567, -92.3331))))
    assert [(c, m) for c, m, _ in msgs] == [(0x01, 0x07)]
    pvt = HELPER.parse_nav_pvt(msgs[0][2])
    assert pvt["fix_type"] == 3 and pvt["sats"] == 11
    assert pvt["lat"] == pytest.approx(38.9567, abs=1e-6)
    assert pvt["lon"] == pytest.approx(-92.3331, abs=1e-6)


def test_message_spanning_two_frames_is_reassembled():
    """A NAV-PVT is 100 bytes on the wire and a frame carries 62, so every
    real one spans frames.  This is the path that matters."""
    stream = ubx_message(0x01, 0x07, nav_pvt(-77.8463, 166.6683))   # McMurdo
    frames = hid_frames(stream)
    assert len(frames) > 1, "test vector no longer spans frames"
    msgs = feed(frames)
    assert len(msgs) == 1
    pvt = HELPER.parse_nav_pvt(msgs[0][2])
    assert pvt["lat"] == pytest.approx(-77.8463, abs=1e-6)


def test_keepalive_frames_are_not_folded_in():
    """⛔ Keepalive frames carry 0xFF/0x00 filler.  Folding them into the
    reassembly buffer corrupts any message spanning two UBX frames -- the
    exact trap gpsdo-monitor's decode_mini_hid_frame docstring warns about."""
    stream = ubx_message(0x01, 0x07, nav_pvt(38.9567, -92.3331))
    frames = hid_frames(stream)
    poisoned = [frames[0], bytes([0, 0x00]) + b"\xff" * 62] + frames[1:]
    msgs = feed(poisoned)
    assert len(msgs) == 1, "a keepalive frame broke reassembly"
    assert HELPER.parse_nav_pvt(msgs[0][2])["lat"] == pytest.approx(38.9567, abs=1e-6)


def test_a_corrupt_checksum_is_refused():
    stream = bytearray(ubx_message(0x01, 0x07, nav_pvt(38.9567, -92.3331)))
    stream[-1] ^= 0xFF
    assert feed(hid_frames(bytes(stream))) == []


@pytest.mark.parametrize("lat,lon", [
    (38.9567, -92.3331), (-77.8463, 166.6683), (47.9253, -97.0329),
    (37.4419, -122.1430), (0.0, 0.0), (51.5074, -0.1278),
])
def test_maidenhead_agrees_with_gpsdo_monitor(lat, lon):
    """Two independent implementations, or the test is just restating one."""
    src = REPO.parent / "gpsdo-monitor" / "src"
    if not (src / "gpsdo_monitor" / "nmea.py").exists():
        pytest.skip("gpsdo-monitor checkout not beside sigmond")
    # nmea.py imports from its own package, so the package root must be on
    # the path -- without this the test SKIPS and proves nothing.
    # Load nmea.py WITHOUT executing gpsdo_monitor/__init__.py, which pulls
    # hid_xport and so needs hidapi -- a dependency this repo does not have
    # and should not grow just to compare two grid calculations.
    pkg = sys.modules.get("gpsdo_monitor")
    if pkg is None:
        pkg = types.ModuleType("gpsdo_monitor")
        pkg.__path__ = [str(src / "gpsdo_monitor")]
        sys.modules["gpsdo_monitor"] = pkg
    # nmea.py takes one constant from hid_xport, which needs hidapi.  Stub
    # just that constant rather than install a USB library to compare arithmetic.
    if "gpsdo_monitor.hid_xport" not in sys.modules:
        stub = types.ModuleType("gpsdo_monitor.hid_xport")
        stub.VID_LBE = 0x1DD2
        sys.modules["gpsdo_monitor.hid_xport"] = stub
    spec = importlib.util.spec_from_file_location(
        "gpsdo_monitor.nmea", src / "gpsdo_monitor" / "nmea.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gpsdo_monitor.nmea"] = mod
    spec.loader.exec_module(mod)
    assert HELPER.maidenhead(lat, lon) == mod.to_maidenhead(lat, lon, 6)


def test_helper_needs_no_third_party_import():
    """It runs on a bare Proxmox host at first boot: stdlib only."""
    text = WIZARD.read_text()
    src = re.search(r"cat >\"\$helper\" <<'MINIGRIDEOF'\n(.*?)\nMINIGRIDEOF\n",
                    text, re.S).group(1)
    imported = set(re.findall(r"^\s*(?:import|from)\s+([\w.]+)", src, re.M))
    stdlib = {"array", "fcntl", "json", "os", "struct", "sys", "time"}
    assert imported <= stdlib, \
        f"non-stdlib import in the embedded helper: {sorted(imported - stdlib)}"
