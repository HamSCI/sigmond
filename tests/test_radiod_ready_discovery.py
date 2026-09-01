#!/usr/bin/env python3
"""The radiod readiness gate must not be blocked by a unit that cannot run.

`sigmond-radiod-ready` auto-discovers "the" radiod instance with
`systemctl list-units --plain "radiod@*.service"` and refuses to proceed
unless it finds exactly one. That listing includes units in FAILED state, and
it includes MASKED ones — so a stale sibling that can never run still counts.

Measured on B4 2026-09-01. `smd apply` enabled a leftover
`radiod@AC0G-B4-patched.service` whose config dates from an abandoned upgrade
branch. It could not start (the real radiod holds the RX888), so it failed,
and thereafter:

    sigmond-radiod-ready: expected one radiod instance, found
      ['radiod@AC0G-B4-patched.service', 'radiod@AC0G-B4.service'] — pass --unit
    timestd-core-recorder.service: Control process exited, status=2

The entire timing chain could not start. Masking the unit did NOT help,
because a masked-and-failed unit is still listed. Only `systemctl
reset-failed` cleared it, which nobody would think to run.

A unit that is masked, or failed, or not loaded, is not a candidate for "the
radiod instance". Discovery must say so itself rather than handing the
operator an ambiguity it could have resolved.
"""

import pytest

from sigmond import radiod_ready


# Exactly the shape `systemctl list-units --no-legend --plain` emits:
#   UNIT  LOAD  ACTIVE  SUB  DESCRIPTION...
HEALTHY = "radiod@AC0G-B4.service loaded active running AC0G-B4 radio receiver"
MASKED_FAILED = (
    "radiod@AC0G-B4-patched.service masked failed failed "
    "radiod@AC0G-B4-patched.service")
LOADED_FAILED = (
    "radiod@AC0G-B4-old.service loaded failed failed AC0G-B4 old receiver")
STARTING = "radiod@AC0G-B4.service loaded activating start AC0G-B4 radio receiver"


def test_the_only_healthy_unit_is_chosen():
    assert radiod_ready.candidate_units(HEALTHY) == ["radiod@AC0G-B4.service"]


def test_a_masked_failed_sibling_is_not_a_candidate():
    """The B4 outage: this pair made discovery ambiguous and stopped timing."""
    listing = "\n".join([MASKED_FAILED, HEALTHY])
    assert radiod_ready.candidate_units(listing) == ["radiod@AC0G-B4.service"]


def test_a_loaded_but_failed_sibling_is_not_a_candidate():
    listing = "\n".join([LOADED_FAILED, HEALTHY])
    assert radiod_ready.candidate_units(listing) == ["radiod@AC0G-B4.service"]


def test_a_unit_still_starting_is_still_a_candidate():
    """Cold start: radiod is activating and the gate exists to WAIT for it."""
    assert radiod_ready.candidate_units(STARTING) == ["radiod@AC0G-B4.service"]


def test_two_genuinely_live_instances_remain_ambiguous():
    """Real ambiguity must still be reported — this is not a rule that
    silently picks one when the operator really does run two receivers."""
    other = "radiod@AC0G-B5.service loaded active running B5 radio receiver"
    got = radiod_ready.candidate_units("\n".join([HEALTHY, other]))
    assert len(got) == 2


def test_no_units_at_all_yields_nothing():
    assert radiod_ready.candidate_units("") == []


def test_blank_and_ragged_lines_are_ignored():
    listing = "\n".join(["", "   ", HEALTHY, ""])
    assert radiod_ready.candidate_units(listing) == ["radiod@AC0G-B4.service"]
