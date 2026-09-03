"""The RX888's USB interrupts must land off radiod's fence.

⛔ AC0G-ND, 2026-09-03.  `sigmond-rx888-irq-affinity` used to CO-LOCATE the
xhci IRQs with radiod, on the reasoning that radiod's reserved pair "has ample
headroom and is the data's consumer".  That premise holds on B4 (34 radiod
threads, 13 of 16 L3 ways) and fails on ND, which runs the same pin on the same
silicon behind an identically configured PM while carrying 50 radiod threads on
10 ways — 17 WSPR bands + 9 FT8 + 9 FT4 + 6 metrology at 129.6 Msps.

With the interrupts on radiod's only physical core the host stopped draining
the USB ring::

    RX888 RTP<->GPS offset STEPPED +1.000 s over 1.0 s
    (~129612687 samples missing; USB xfer failures +0; cumulative +164.661 s)

"USB xfer failures +0" is the signature: the device never errored.  Decoders
then broke in order of coherent integration length — WSPR (120 s), FT8
(12.6 s), FT4 (4.5 s) — so it presented as "FT4 decodes but FT8 doesn't"
rather than as one fault.  A/B on the same station: IRQs on radiod's pair lost
samples within 6-13 minutes of every start; IRQs on the OS pair held at zero
steps with FT8 back to ~100 spots a cycle.

B4 had already been migrated by hand on 2026-08-28 via its override file, and
that never reached the image, so the reference station and every new station
disagreed silently for a week.  These tests exist so the default cannot drift
back without saying so.

The derivation runs under bash against a FAKE topology, so it needs no USB bus,
no radiod and no root.
"""
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "sigmond-rx888-irq-affinity"


def _fake_world(tmp: Path, *, cores: list[tuple[int, int]], irqs: list[int]) -> dict:
    """Build a fake sysfs/proc.  `cores` is a list of (cpu_a, cpu_b) SMT pairs."""
    cpu_root = tmp / "cpu"
    for a, b in cores:
        for cpu in (a, b):
            d = cpu_root / f"cpu{cpu}" / "topology"
            d.mkdir(parents=True, exist_ok=True)
            (d / "thread_siblings_list").write_text(f"{a},{b}\n")
    interrupts = tmp / "interrupts"
    lines = ["           CPU0       CPU1\n"]
    for irq in irqs:
        lines.append(f" {irq}:          0          0   PCI-MSIX-0000:02:00.0  0-edge  xhci_hcd\n")
    lines.append(" 30:          0          0   IO-APIC   1-edge  i8042\n")
    interrupts.write_text("".join(lines))
    irq_root = tmp / "irq"
    for irq in irqs:
        d = irq_root / str(irq)
        d.mkdir(parents=True, exist_ok=True)
        (d / "smp_affinity_list").write_text("0-15\n")
    return {
        "SIGMOND_CPU_ROOT": str(cpu_root),
        "SIGMOND_PROC_INTERRUPTS": str(interrupts),
        "SIGMOND_IRQ_ROOT": str(irq_root),
    }


def _run(tmp: Path, env_extra: dict, *, fence: str | None,
         override: str | None = None) -> tuple[str, dict]:
    """Run the script with a faked world.  Returns (stdout, {irq: affinity})."""
    env = dict(os.environ)
    env.update(env_extra)
    etc = tmp / "etc"
    etc.mkdir(exist_ok=True)
    # The script reads the fence from systemctl; stub systemctl on PATH.
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    if fence is None:
        body = '#!/bin/bash\nexit 0\n'
    else:
        body = (
            '#!/bin/bash\n'
            'case "$*" in\n'
            '  *"list-units"*) echo "radiod@TEST.service loaded active running x" ;;\n'
            f'  *"CPUAffinity"*) echo "{fence}" ;;\n'
            '  *) exit 0 ;;\n'
            'esac\n'
        )
    (bin_dir / "systemctl").write_text(body)
    (bin_dir / "systemctl").chmod(0o755)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    script = SCRIPT.read_text()
    if override is None:
        # neutralise the override branch: point it at a path that cannot exist
        script = script.replace("/etc/sigmond/rx888-irq-cpus",
                                str(tmp / "no-such-override"))
    else:
        ovr = etc / "rx888-irq-cpus"
        ovr.write_text(override + "\n")
        script = script.replace("/etc/sigmond/rx888-irq-cpus", str(ovr))
    patched = tmp / "script.sh"
    patched.write_text(script)
    patched.chmod(0o755)

    r = subprocess.run(["bash", str(patched)], env=env,
                       capture_output=True, text=True, timeout=60)
    affinities = {}
    irq_root = Path(env_extra["SIGMOND_IRQ_ROOT"])
    for d in sorted(irq_root.iterdir()):
        affinities[d.name] = (d / "smp_affinity_list").read_text().strip()
    return r.stdout + r.stderr, affinities


class OsPairDerivationTest(unittest.TestCase):

    #: Both live stations: 7 SMT pairs, radiod fenced onto the last one.
    CORES = [(0, 1), (2, 3), (4, 5), (6, 7), (8, 9), (10, 11), (12, 13)]

    def test_irqs_land_off_the_fence_on_the_live_layout(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            env = _fake_world(tmp, cores=self.CORES, irqs=[50, 59])
            out, aff = _run(tmp, env, fence="12 13")
        self.assertEqual(aff, {"50": "0,1", "59": "0,1"},
                         f"expected the OS pair, got {aff}\n{out}")
        self.assertIn("OS pair", out)

    def test_a_fence_at_the_bottom_does_not_get_the_irqs(self):
        # ⛔ Hardcoding "0-1" would put the interrupts INSIDE the fence here.
        # This is why the OS pair is derived from topology.
        with TemporaryDirectory() as td:
            tmp = Path(td)
            env = _fake_world(tmp, cores=self.CORES, irqs=[50])
            out, aff = _run(tmp, env, fence="0 1")
        self.assertEqual(aff["50"], "2,3", f"got {aff}\n{out}")

    def test_a_fence_in_the_middle_skips_only_its_own_core(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            env = _fake_world(tmp, cores=self.CORES, irqs=[50])
            out, aff = _run(tmp, env, fence="0-1")   # range form, not space form
        self.assertEqual(aff["50"], "2,3", f"range fence not parsed\n{out}")

    def test_radiod_owning_every_core_falls_back_to_co_location(self):
        # Nowhere else to put them; pinning into the fence still beats letting
        # the balancer drift them, but the log must say which model it chose.
        with TemporaryDirectory() as td:
            tmp = Path(td)
            env = _fake_world(tmp, cores=[(0, 1)], irqs=[50])
            out, aff = _run(tmp, env, fence="0 1")
        self.assertEqual(aff["50"], "0 1".replace(" ", ","))
        self.assertIn("co-located", out)

    def test_no_fence_at_all_leaves_the_kernel_alone(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            env = _fake_world(tmp, cores=self.CORES, irqs=[50])
            out, aff = _run(tmp, env, fence=None)
        self.assertEqual(aff["50"], "0-15",
                         "an unfenced station must keep kernel IRQ balancing")

    def test_the_override_still_wins_and_can_restore_co_location(self):
        # ⛔ Guards the guard: the flipped default must not remove an
        # operator's ability to go back to the T6-era model, which
        # gap-hourly.tsv remains the arbiter of.
        with TemporaryDirectory() as td:
            tmp = Path(td)
            env = _fake_world(tmp, cores=self.CORES, irqs=[50])
            out, aff = _run(tmp, env, fence="12 13", override="12-13")
        self.assertEqual(aff["50"], "12-13", f"override ignored\n{out}")
        self.assertIn("override", out)

    def test_only_xhci_irqs_are_touched(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            env = _fake_world(tmp, cores=self.CORES, irqs=[50])
            # irq 30 (i8042) exists in the interrupt table but has no dir here
            out, aff = _run(tmp, env, fence="12 13")
        self.assertNotIn("30", aff)


if __name__ == "__main__":
    unittest.main()


# --- `smd doctor` must be able to SEE this in one command -----------------

import importlib.machinery                                    # noqa: E402
import importlib.util                                         # noqa: E402
import sys                                                    # noqa: E402

sys.path.insert(0, str(REPO / "lib"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_irq_under_test", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_irq_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class DoctorSeesIrqDriftTest(unittest.TestCase):
    """Finding this by hand cost an hour of comparing two machines."""

    def test_irqs_inside_the_fence_are_reported(self):
        f = smd._rx888_irq_findings(
            read_fence=lambda: "12 13",
            read_xhci_affinity=lambda: {"50": "12-13", "59": "12-13"})
        self.assertEqual(len(f), 2)
        self.assertEqual(f[0].kind, "rx888-irq-in-fence")
        self.assertIn("12-13", f[0].detail)
        # The finding must name the mechanism, not just the fact.
        self.assertIn("obeys neither nice levels nor unit", f[0].detail)
        self.assertFalse(f[0].fixable, "doctor must not restart a receiver")

    def test_irqs_on_the_os_pair_are_silent(self):
        self.assertEqual(
            smd._rx888_irq_findings(read_fence=lambda: "12 13",
                                    read_xhci_affinity=lambda: {"50": "0,1"}),
            [])

    def test_partial_overlap_still_reported(self):
        # One thread of the fence is enough to contend for the core.
        f = smd._rx888_irq_findings(read_fence=lambda: "12 13",
                                    read_xhci_affinity=lambda: {"50": "11-12"})
        self.assertEqual(len(f), 1)

    def test_an_unfenced_station_says_nothing(self):
        # No fence means kernel IRQ balancing, which is correct and not drift.
        self.assertEqual(
            smd._rx888_irq_findings(read_fence=lambda: "",
                                    read_xhci_affinity=lambda: {"50": "0-15"}),
            [])

    def test_unreadable_irq_table_is_not_a_finding(self):
        # Absence of evidence must not be reported as a fault.
        self.assertEqual(
            smd._rx888_irq_findings(read_fence=lambda: "12 13",
                                    read_xhci_affinity=lambda: {}),
            [])
