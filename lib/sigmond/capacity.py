"""Capacity gate for multi-receiver bring-up (spec §4).

Pure and stdlib-only: the caller passes the instances and the CPU counts.
Costs are measured cores per running instance (AC0G-B2, Ryzen 7 5700U,
2026-11-05, cgroup cpu.stat over 30 minutes).
"""
from __future__ import annotations

from dataclasses import dataclass

INSTANCE_COST_CORES = {
    'wspr-recorder': 0.21,   # measured 0.207, 0.208 (two instances)
    'psk-recorder': 0.18,    # measured 0.177, 0.173
    'hf-timestd': 0.35,      # timestd-core-recorder 0.345; the rest ~0.003
}
DEFAULT_COST_CORES = 0.25     # a client nobody has measured


@dataclass
class CapacityVerdict:
    needed: float
    available: float
    estimated: tuple
    ok: bool
    instances: int = 0
    online_cpus: int = 0
    radiod_cpus: int = 0
    radiod_estimated: bool = False

    def text(self) -> str:
        line = (f'capacity: {self.instances} instance(s) need '
                f'{self.needed:.2f} cores of {self.available:g} available '
                f'({self.online_cpus} CPUs, {self.radiod_cpus} for radiod'
                f'{" (estimated)" if self.radiod_estimated else ""}, '
                f'1 for the system)')
        if self.estimated:
            line += (f'; cost estimated at {DEFAULT_COST_CORES} cores for: '
                     + ', '.join(self.estimated))
        if not self.ok:
            line += (f'\ncapacity: short by {self.needed - self.available:.2f} '
                     f'cores; narrow a receiver\'s clients = [...] in '
                     f'site-profile.toml, or pass --ignore-capacity')
        return line


def capacity_verdict(instances, *, online_cpus: int, radiod_cpus: int,
                     radiod_estimated: bool = False) -> CapacityVerdict:
    """Sum the cost of every (client, reporter) instance against the CPUs
    left after radiod's and one for the system."""
    needed = 0.0
    estimated = []
    for client, _reporter in instances:
        if client in INSTANCE_COST_CORES:
            needed += INSTANCE_COST_CORES[client]
        else:
            needed += DEFAULT_COST_CORES
            if client not in estimated:
                estimated.append(client)
    available = max(0, online_cpus - radiod_cpus - 1)
    return CapacityVerdict(
        needed=needed, available=available, estimated=tuple(estimated),
        ok=needed <= available + 1e-9, instances=len(list(instances)),
        online_cpus=online_cpus, radiod_cpus=radiod_cpus,
        radiod_estimated=radiod_estimated)
