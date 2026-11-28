"""The Proxmox host's own periodic jobs must never take CPU from a guest's
network path.  KFS-OMNI-SW-PM 2026-11-28 02:00:17Z: pm-heartbeat (5.7 s CPU)
and pm-storage (8.2 s CPU) started together, filled both host housekeeping
cores (10, 11; 0-9 are the guest's) for ~8 s, starved the guest's vhost
thread, and the guest's radiod multicast fell from 23k to 7-11k pkt/s with
UDP SndbufErrors and qdisc drops: 20-90 lost-sample gaps per band on the
decoder VM every 5 minutes.  At idle priority they run only on spare CPU."""
import configparser
from pathlib import Path

import pytest

PROXMOX = Path(__file__).resolve().parent.parent / "scripts" / "proxmox"


@pytest.mark.parametrize("unit", ["pm-heartbeat.service", "pm-storage.service"])
def test_host_periodic_job_runs_at_idle_priority(unit):
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string((PROXMOX / unit).read_text())
    svc = cp["Service"]
    assert svc.get("CPUSchedulingPolicy") == "idle"
    assert svc.get("Nice") == "19"
    assert svc.get("IOSchedulingClass") == "idle"
