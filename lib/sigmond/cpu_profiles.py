"""Per-CPU station defaults, keyed by the wisdom bundle's CPU slug.

Stdlib-only.  One table, ``CPU_PROFILES``; another CPU is one more entry.
A CPU that is not in the table gets today's behaviour everywhere: every
RX888 radiod at 129.6 Msps and no decoder memory-bandwidth cap.

Two consumers:

  * ``smd config init radiod`` (and the TUI SDR inventory) ask
    ``rx888_samprate_hz`` for the ADC rate of each NEW RX888 radiod conf.
    An existing conf's rate is never rewritten.
  * ``scripts/proxmox/bootstrap.sh`` runs
    ``python3 -m sigmond.cpu_profiles decoder-mba-gbps "<host model>"`` and
    hands the answer to host-apply.sh, which passes it to
    sigmond-host-resctrl.sh as the decoder pCPUs' AMD MBA cap.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .wisdom import cpu_model, cpu_slug

FULL_SAMPRATE_HZ = 129_600_000      # RX888 full rate: HF + 6 m, 45.375 MHz


@dataclass(frozen=True)
class CpuProfile:
    name: str               # for the comment in a generated conf
    # How many RX888 radiods on one host run at FULL_SAMPRATE_HZ; every
    # further one gets reduced_samprate_hz.  The first is the one the
    # timing radiod / hf-timestd uses (it needs 45.375 MHz and 6 m).
    full_rate_radiods: int
    reduced_samprate_hz: int
    # AMD MBA cap for the decoder (worker) pCPUs on the Proxmox host, in
    # GB/s; None = no cap.
    decoder_mba_gbps: Optional[float]
    source: str


CPU_PROFILES: dict[str, CpuProfile] = {
    # AMD Ryzen 5 5560U (Cezanne): 6 cores / 12 threads, sequential SMT
    # pairs, one 8 MB L3 (lscpu on KFS-OMNI-SW-PM, 2026-11-06), L3 CAT + MBA.
    #
    # Sample rates, KFS-OMNI 2026-11-06 (Proxmox VM, fresh FFTW wisdom,
    # radiods on adjacent physical cores vCPU 6,7 and 8,9, both inside a
    # 5 MB L3 CAT slice): OMNI @129.6 fft thread 62-67 %, SW @64.8 25-28 %.
    # With SW raised to 129.6: OMNI 101-104 %, SW 90-103 %, both saturated
    # and decoders idle -- two 129.6 Msps FFTs slow each other ~1.6x.
    # Reverted.
    #
    # Decoder MBA, KFS-OMNI-SW-PM 2026-11-06 (resctrl mbm_total_bytes per
    # 2 s): radiod pair steady 7.8-8.7 GB/s; decoders 1.3-2 GB/s quiet, up
    # to ~7 GB/s in the even-minute WSPR burst (30-40 decoder processes).
    # MB:0=64 (8 GB/s) on the worker pCPUs cut OMNI's burst plateau from
    # 76-88 % (peaks 92-100 %) to 69-72 % with decode time unchanged
    # (~12 s); MB:0=32 (4 GB/s) doubled the burst length.
    'amd-ryzen-5-5560u': CpuProfile(
        name='Ryzen 5 5560U',
        full_rate_radiods=1,
        reduced_samprate_hz=64_800_000,
        decoder_mba_gbps=8,
        source='KFS-OMNI / KFS-OMNI-SW-PM, 2026-11-06'),
}


def profile_for(model: str) -> Optional[CpuProfile]:
    """The profile for a /proc/cpuinfo 'model name', or None."""
    if not model:
        return None
    return CPU_PROFILES.get(cpu_slug(model))


def host_profile(cpuinfo: Path | None = None) -> Optional[CpuProfile]:
    """The profile for this machine's CPU, or None."""
    return profile_for(cpu_model(cpuinfo))


def rx888_samprate_hz(ordinal: int, profile: Optional[CpuProfile]) -> int:
    """ADC rate for the RX888 radiod that is number ``ordinal`` (0-based)
    on this host."""
    if profile is None or ordinal < profile.full_rate_radiods:
        return FULL_SAMPRATE_HZ
    return profile.reduced_samprate_hz


def decoder_mba_gbps(model: str) -> Optional[float]:
    p = profile_for(model)
    return p.decoder_mba_gbps if p else None


def main(argv: list[str] | None = None) -> int:
    """``python3 -m sigmond.cpu_profiles decoder-mba-gbps "<model>"`` prints
    the cap (e.g. ``8``), or an empty line for no cap."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != 'decoder-mba-gbps':
        print('usage: python3 -m sigmond.cpu_profiles decoder-mba-gbps "<model name>"',
              file=sys.stderr)
        return 2
    cap = decoder_mba_gbps(argv[1])
    print('' if cap is None else f'{cap:g}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
