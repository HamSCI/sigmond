"""Screen ↔ smd-verb parity mapping.

Declares, for every module in ``lib/sigmond/tui/screens/``, the ``smd``
command path(s) it is the operator-facing counterpart of. This is the
data half of the parity lint in ``tests/test_tui_parity.py`` -- that test
walks the live ``bin/smd`` argparse tree and cross-checks every verb
listed here still exists, and that every screen module has an entry.
An ``smd`` verb rename or a deleted screen with a stale entry fails CI
instead of silently rotting the TUI out of sync with the CLI (see
``tasks/plan-tui-reconciliation.md``, Task 3).

Screens that call sigmond library functions in-process rather than
shelling out to ``smd`` (e.g. ``validate``, ``environment``) still
declare the verb they are the TUI face of. The sentinel ``None`` marks
a screen with genuinely no ``smd`` CLI counterpart -- a pure TUI-only
surface (live radiod control-plane views, in-memory dashboards, or a
screen composed purely of other screens).

Verb strings are space-separated command paths WITHOUT the leading
"smd", e.g. ``"admin verifier report"``, matching the walk performed by
the test.

This module must stay importable WITHOUT ``textual`` or ``rich``
installed -- core smd is stdlib-only (see repo CLAUDE.md) and the
parity test is meant to run on a bare interpreter. Do not add any
import here beyond the stdlib.
"""

from __future__ import annotations

SCREEN_VERBS: dict[str, tuple[str, ...] | None] = {
    "activity": ("watch",),
    "annotation_quality": None,
    "apply": ("apply",),
    "authority": None,
    "backup": ("config backup",),
    "components": ("component list", "install", "component update"),
    "configuration": (
        "config edit",
        "admin instance add",
        "admin instance remove",
        "admin instance migrate",
    ),
    "cpu_affinity": ("admin diag cpu-affinity",),
    "cpu_freq": ("admin diag cpu-freq",),
    "diag_net": ("admin diag net",),
    "environment": (
        "admin environment list",
        "admin environment probe",
        "admin environment describe",
    ),
    "gpsdo": ("watch gpsdo",),
    "greenfield": ("bringup", "admin readiness", "config edit"),
    "install": ("install",),
    "ka9q_watch": ("watch ka9q",),
    "lifecycle": ("start", "stop", "restart"),
    # Coarser than the screen's real behaviour: `admin log set-level` is
    # handled by a pre-argparse `sys.argv` intercept near the top of
    # bin/smd's main() (matched before the argparse tree is even built,
    # so it can accept both `set-level <level>` and
    # `set-level <client> <level>`), not an argparse subparser -- so the
    # lint's parser walk (_live_verb_paths, which only sees
    # ArgumentParser/_SubParsersAction objects) can never observe
    # "admin log set-level" as a live path.  "admin log" (the journal/
    # file-tail verb, which IS a real subparser) is the closest
    # resolvable verb and stands in for the whole screen.
    "logs": ("admin log",),
    "overview": ("status", "timing"),
    "psws": ("psws status", "psws enroll", "psws verify",
             "config register-radiod", "config upload"),
    "rac": ("admin rac",),
    "radiod": None,
    "receiver_channels": None,
    "resources": None,
    "restore": ("config restore",),
    "sdr_inventory": ("config register-radiod",),
    "textual_wizard": None,
    "timing": ("timing",),
    "timing_authority": None,
    "validate": ("admin validate",),
    "verifier": ("admin verifier report", "admin verifier rehabilitate"),
}
