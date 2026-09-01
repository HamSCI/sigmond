#!/usr/bin/env python3
"""`smd apply` must not resurrect a unit the operator deliberately stopped.

On B4 2026-09-01 a leftover `radiod@AC0G-B4-patched.service`, whose config
survived an abandoned upgrade branch, was enabled and started by `smd apply`
during a component update scoped to two LIBRARIES. It could not run — the real
radiod holds the RX888 — so it crash-looped, and its failed state then blocked
`timestd-core-recorder` from starting at all. The whole timing chain went down.

It happened TWICE in one day: once in the morning, once in the evening after
the operator had `disable`d it in between. Disabling did not survive the next
apply, which is precisely the problem — apply treats "not enabled" as a state
to correct rather than as a decision someone made.

Masking is how an operator says "never run this". `smd apply` must read that
as intent, not as drift to reconcile.
"""

import pytest

from sigmond import unit_intent


def test_a_normal_unit_is_managed():
    assert unit_intent.should_manage("radiod@AC0G-B4.service", "enabled") is True


def test_a_disabled_unit_is_still_managed():
    """Disabled means "not started at boot", not "never run" — apply may
    legitimately enable it. This is the case that made masking necessary."""
    assert unit_intent.should_manage("radiod@AC0G-B4.service", "disabled") is True


def test_a_masked_unit_is_left_alone():
    """The B4 outage. Masking is an explicit instruction, not drift."""
    assert unit_intent.should_manage(
        "radiod@AC0G-B4-patched.service", "masked") is False


def test_a_runtime_masked_unit_is_left_alone():
    assert unit_intent.should_manage(
        "radiod@AC0G-B4-patched.service", "masked-runtime") is False


def test_an_unknown_state_is_managed_rather_than_silently_skipped():
    """Skipping on an unrecognised state would hide units from apply, which
    is the opposite failure. Only an explicit mask means "leave it"."""
    assert unit_intent.should_manage("radiod@x.service", "static") is True
    assert unit_intent.should_manage("radiod@x.service", "") is True


def test_the_filter_partitions_a_real_unit_set():
    states = {
        "radiod@AC0G-B4.service": "enabled",
        "radiod@AC0G-B4-patched.service": "masked",
    }
    manage, skipped = unit_intent.partition(states)
    assert manage == ["radiod@AC0G-B4.service"]
    assert skipped == ["radiod@AC0G-B4-patched.service"]
