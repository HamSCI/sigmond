"""smd's cpu-freq keeps the Zen 3 2000/1600 default only where it fits (Rob 2026-11-16)."""
import importlib.machinery
import importlib.util
from pathlib import Path

_p = Path(__file__).resolve().parent.parent / "bin" / "smd"
_l = importlib.machinery.SourceFileLoader("smd_zen3_fit", str(_p))
_s = importlib.util.spec_from_loader("smd_zen3_fit", _l)
smd = importlib.util.module_from_spec(_s)
_l.exec_module(smd)


def n(rate):
    return {"instance": "X", "serial": "", "samprate": rate}


def test_one_radiod_at_129_fits():
    assert smd._zen3_default_fits([n(129_600_000)])


def test_several_at_64_8_fit():
    assert smd._zen3_default_fits([n(64_800_000), n(None), n(64_800_000)])


def test_several_with_one_at_129_do_not_fit():
    assert not smd._zen3_default_fits([n(64_800_000), n(129_600_000)])


def test_clock_limits_fall_back_when_it_does_not_fit():
    cfg = {"radiod_max_mhz": 2000, "other_max_mhz": 1600, "zen3_default": True}
    r, o, why = smd._cpu_freq_limits(cfg, [n(129_600_000), n(129_600_000)])
    assert (r, o) == (3200, 1400) and "129.6" in why
    r, o, why = smd._cpu_freq_limits(cfg, [n(129_600_000)])
    assert (r, o, why) == (2000, 1600, "")
    own = {"radiod_max_mhz": 2000, "other_max_mhz": 1600}
    assert smd._cpu_freq_limits(own, [n(129_600_000), n(129_600_000)])[:2] == (2000, 1600)
