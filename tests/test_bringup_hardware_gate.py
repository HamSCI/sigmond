"""bin/smd _bringup_hardware_gate: advisory rows never block.

Standing rule: GPSDO location, else typed grid.  A GPSDO that disciplines
the RX888 but publishes no position (an MVR GPSDO B without NMEA
passthrough) prints its advice and never fails --require-hardware; the
station falls back to its typed grid.  A GPSDO that is absent still fails."""
from __future__ import annotations

import importlib.util
import os
import types
from pathlib import Path
from unittest import mock

from sigmond import hardware

ADVICE = ('position unavailable: the MVR reports position only in NMEA '
          'passthrough, which is off; enable it with `gpsdo-monitor mvr '
          'passthrough on --persist` (stop the gpsdo-monitor service first)')
PROF = types.SimpleNamespace(local_radiod_infra=('gpsdo-monitor',), clients=())


def _load_smd():
    spec = importlib.util.spec_from_loader("smd_mod_hw_gate", loader=None)
    smd = importlib.util.module_from_spec(spec)
    smd.__dict__["__file__"] = "bin/smd"
    src = Path(__file__).resolve().parent.parent / "bin" / "smd"
    with mock.patch.dict(os.environ, {"SIGMOND_NO_VENV_REEXEC": "1"}):
        exec(compile(src.read_text(), str(src), "exec"), smd.__dict__)
    return smd


def _gpsdo(monkeypatch, *, present, fix, advisories=()):
    monkeypatch.setattr(hardware, 'detect_gpsdo', lambda: present)
    monkeypatch.setattr(hardware, 'gpsdo_fix', lambda: fix)
    monkeypatch.setattr(hardware, 'gpsdo_advisories', lambda: list(advisories))


def test_position_row_is_advisory(monkeypatch):
    _gpsdo(monkeypatch, present=True, fix=(True, None, 'DGPS fix'), advisories=[ADVICE])
    checks = hardware.gate_checks(PROF, False)
    pos = [c for c in checks if c.label == 'GPSDO position']
    assert len(pos) == 1 and pos[0].advisory is True
    assert all(c.advisory is False for c in checks if c is not pos[0])


def test_require_hardware_passes_a_locked_gpsdo_without_position(monkeypatch, capsys):
    smd = _load_smd()
    _gpsdo(monkeypatch, present=True, fix=(True, None, 'DGPS fix'), advisories=[ADVICE])
    assert smd._bringup_hardware_gate(PROF, False, require=True) is True
    cap = capsys.readouterr()
    out = cap.out + cap.err
    assert 'gpsdo-monitor mvr passthrough on --persist' in out   # advice printed
    assert 'not attached' not in out


def test_require_hardware_still_fails_without_a_gpsdo(monkeypatch, capsys):
    smd = _load_smd()
    _gpsdo(monkeypatch, present=False, fix=(False, None, None))
    assert smd._bringup_hardware_gate(PROF, False, require=True) is False
    cap = capsys.readouterr()
    assert 'GPSDO (Leo Bodnar or MVR)' in cap.out + cap.err


def test_require_hardware_still_fails_on_no_fix(monkeypatch):
    smd = _load_smd()
    _gpsdo(monkeypatch, present=True, fix=(False, None, 'no_fix'))
    assert smd._bringup_hardware_gate(PROF, False, require=True) is False


def test_dormant_warning_names_both_gpsdo_kinds():
    src = (Path(__file__).resolve().parent.parent / "bin" / "smd").read_text()
    assert 'no Leo Bodnar GPSDO on the bus' not in src
    assert 'no GPSDO (Leo Bodnar or MVR) on the bus' in src
