# tests/test_bringup_ungated.py
"""An install with nothing attached must complete.

Fargo installs the PM and VM on a Beelink with NO GPSDO, NO RX888 and NO
magnetometer, reboots, and only then gains a hub, an RX888 and a Bodnar
miniGPS.  Refusing that install makes the machine un-buildable at the moment
it is being built.

The warning must stay — losing radiod means NOTHING decodes — but a warning
is not a refusal.
"""

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _smd_source():
    return open("bin/smd").read()


def _load_smd():
    # bin/smd re-execs into the production venv unless told not to; suppress
    # that so importing the script just defines its functions (same pattern
    # as tests/test_start_autoenable.py).
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_bringup_ungated", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _minimal_local_radiod_profile():
    """A Profile with no clients and no gpsdo-monitor/mag-recorder infra, so
    only the RX888 gate is in play — no magnetometer/GPSDO dormancy branches
    fire, and the plan-builder has nothing else to resolve."""
    from sigmond.catalog import Profile
    return Profile(name="ungated-test", description="",
                   clients=(), local_radiod_infra=("igmp-querier",),
                   optional=())


def test_missing_sdr_installs_dormant_instead_of_aborting(capsys):
    """Drive the real decision in `cmd_bringup`'s local-radiod branch: with
    no SDR detected, bring-up must not fail, and the operator-facing warning
    must actually print.  Narrowest real callable that carries the decision
    is `cmd_bringup` itself (the check lives inline, not in a helper) — run
    it with --dry-run so it stops before touching the system (no root
    elevation, no real install), with a minimal profile so the plan-builder
    has nothing beyond the radiod stack to resolve, and with only the SDR
    probe stubbed (the GPSDO/magnetometer probes are read-only and safe to
    let run for real; with --require-hardware unset they can't fail the
    call regardless of what they find)."""
    smd = _load_smd()
    prof = _minimal_local_radiod_profile()

    args = Namespace(profile="ungated-test", dry_run=True,
                      non_interactive=True)

    with mock.patch.object(smd, "_detect_local_sdr", lambda: False), \
         mock.patch("sigmond.catalog.load_profiles",
                     lambda: {"ungated-test": prof}):
        rc = smd.cmd_bringup(args)

    assert rc == 0, "bringup must not fail when no SDR is attached"

    out = capsys.readouterr().out
    assert "no RX888/SDR on the USB bus" in out
    assert "NOTHING decodes" in out
    assert "adopt" in out.lower()


# The two fixed-window source scans that used to sit here are gone.  They
# read bin/smd as text around a literal string and asserted `_warn(` and
# "adopt" fell within N characters of it -- which passes against an
# implementation that keeps the hard abort and merely also warns, and fails
# on any unrelated edit that moves a line.  The behavioural test above drives
# `cmd_bringup` with no SDR attached and asserts on what it PRINTS; that is
# the guard.


def test_smd_still_parses():
    subprocess.run([sys.executable, "-c",
                    "import ast; ast.parse(open('bin/smd').read())"],
                   check=True)
