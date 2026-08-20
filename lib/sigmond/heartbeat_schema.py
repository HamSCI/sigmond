"""Station-heartbeat wire schema — the producer/consumer contract.

STDLIB ONLY, and ZERO imports from ``sigmond``.  This file is rsynced
VERBATIM next to the server code on another host (the central board that
receives heartbeats), so it must import cleanly with nothing but a bare
Python on the path.  If you ever need a sigmond helper here, you have
put the code in the wrong module — put it in ``sigmond.heartbeat``
instead, which is producer-side only.

What a heartbeat is
-------------------
One JSON envelope per station per interval, assembled ONLY from signals
that cannot lie, in which every field is able to say "I don't know", and
in which "nothing was measured" can never render as healthy.  Eight
fleet defects were silent while some metric reported success; see
``docs/PRODUCER-THREAT-MODEL.md``.

Availability is NOT in the envelope
-----------------------------------
The server derives "is this station alive" from the ARRIVAL time of
heartbeats, never from anything inside one.  A station that has stopped
cannot report that it stopped, so any self-reported liveness field would
be exactly the class of lie this schema exists to remove.
"""

KIND = "station_heartbeat"
SCHEMA_VERSION = 1

# Four states, credited to scripts/capacity/drain_meter.py, which learned
# them the hard way: a two-state verdict forces "not measured" to be
# rendered as either a pass or a failure, and both are lies.
#   VALID          measured, and conclusive: this block is good.
#   INVALID        measured, and conclusive: this block is bad.
#   INCONCLUSIVE   measured, but NOT conclusive — a first run with no
#                  delta yet, a fallback source, a degraded-but-working
#                  path.  drain_meter's "no packets received" /
#                  "too few checkpoints" case.
#   INDETERMINATE  could not measure at all — the file was missing, the
#                  reader raised, the probe never ran.  drain_meter's
#                  "no socket_drops recorded" case.
# NEITHER INCONCLUSIVE NOR INDETERMINATE MAY EVER RENDER AS HEALTHY.
VERDICTS = ("VALID", "INVALID", "INCONCLUSIVE", "INDETERMINATE")

# Worst-wins ordering: INVALID > INDETERMINATE > INCONCLUSIVE > VALID.
# INDETERMINATE outranks INCONCLUSIVE on purpose — "I could not look"
# is a worse operational state than "I looked and could not be sure",
# because nobody is even collecting the evidence.  Usable directly as a
# max() key: max(verdicts, key=PRECEDENCE.__getitem__).
PRECEDENCE = {
    "VALID": 0,
    "INCONCLUSIVE": 1,
    "INDETERMINATE": 2,
    "INVALID": 3,
}

# Fixed block set, in report order.  A block is a question about the
# station that has an honest answer; adding one is a schema change on
# both sides, which is why the set is named here and not inferred.
BLOCK_NAMES = (
    "versions",
    "manifest",
    "timing",
    "gaps",
    "uploads",
    "doctor",
    "resources",
)


def validate(envelope: dict) -> list:
    """Structural errors in ``envelope``; empty list means valid.

    STRUCTURE ONLY.  This checks the frame the two sides agreed on —
    kind, schema version, station identity, the presence of a rollup and
    of well-formed blocks — and nothing else.  It deliberately does NOT
    type-check block payloads: ADDITIVE FIELDS ARE ALLOWED, everywhere,
    at every depth.  A producer one release ahead of a server must not
    be rejected for carrying a field the server has not learned about
    yet; that is how a fleet loses visibility of exactly the hosts that
    were updated most recently.

    Returns a list of human-readable error strings so a caller can log
    all of them at once rather than discovering them one restart at a
    time.
    """
    errors = []

    if not isinstance(envelope, dict):
        return [f"envelope is {type(envelope).__name__}, not a dict"]

    kind = envelope.get("kind")
    if kind != KIND:
        errors.append(f"kind is {kind!r}, expected {KIND!r}")

    if "schema_version" not in envelope:
        errors.append("schema_version missing")
    elif envelope["schema_version"] != SCHEMA_VERSION:
        errors.append(
            f"schema_version is {envelope['schema_version']!r}, "
            f"expected {SCHEMA_VERSION!r}")

    station = envelope.get("station")
    if not isinstance(station, str) or not station.strip():
        errors.append(f"station is {station!r}, expected a non-empty string")

    for field in ("emitted_at", "interval_sec", "rollup"):
        if envelope.get(field) is None:
            errors.append(f"{field} missing")

    rollup = envelope.get("rollup")
    if isinstance(rollup, dict):
        if rollup.get("verdict") not in VERDICTS:
            errors.append(
                f"rollup verdict is {rollup.get('verdict')!r}, "
                f"expected one of {VERDICTS}")
    elif rollup is not None:
        errors.append(f"rollup is {type(rollup).__name__}, not a dict")

    blocks = envelope.get("blocks")
    if blocks is None:
        # An envelope with no blocks is a verdict with no evidence
        # behind it, which is the shape of every defect this schema
        # exists to catch.
        errors.append("blocks missing")
    elif not isinstance(blocks, dict):
        errors.append(f"blocks is {type(blocks).__name__}, not a dict")
    else:
        for name in sorted(blocks):
            if name not in BLOCK_NAMES:
                errors.append(
                    f"unknown block {name!r}, expected one of {BLOCK_NAMES}")
                continue
            block = blocks[name]
            if not isinstance(block, dict):
                errors.append(
                    f"block {name!r} is {type(block).__name__}, not a dict")
                continue
            if "verdict" not in block:
                errors.append(f"block {name!r} has no verdict")
            elif block["verdict"] not in VERDICTS:
                errors.append(
                    f"block {name!r} verdict is {block['verdict']!r}, "
                    f"expected one of {VERDICTS}")
            if not block.get("reason"):
                errors.append(f"block {name!r} has no reason")

    return errors
