"""Tests for sigmond.heartbeat / sigmond.heartbeat_schema.

The station heartbeat exists because eight fleet defects were SILENT while
some metric reported success (see docs/PRODUCER-THREAT-MODEL.md, "Metrics
that lie").  The envelope is therefore assembled only from signals that
cannot lie, every field can say "I don't know", and "nothing measured"
can never render healthy.

Test order in this file is deliberate and mirrors the task brief: the
forbidden-fields test comes FIRST because it is the executable form of
the rule the whole feature exists to enforce.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond import heartbeat_schema
from sigmond.heartbeat import (
    ReaderUnavailable,
    assemble,
    parse_gap_row,
    prune_spool,
    write_tick,
)


NOW = datetime(2026, 8, 20, 14, 5, 6, tzinfo=timezone.utc)

CONFIG = {
    "station": "AC0G-B4",
    "callsign": "AC0G",
    "grid": "EM38ww22",          # deliberately > 6 chars (PII truncation)
    "interval_sec": 300,
}


# ---------------------------------------------------------------------------
# fake readers — a rich, fully-populated, all-VALID host
# ---------------------------------------------------------------------------

def rich_readers(**overrides):
    """Seven readers describing a healthy, richly-instrumented station."""
    readers = {
        "versions": lambda: {"components": {
            "sigmond": "05c2db2", "ka9q-radio": "7fca458",
            "hf-timestd": "deadbee", "hs-uploader": "f5406ca",
        }},
        "manifest": lambda: {
            "present": True,
            "blessed_source": "/etc/sigmond-appliance/manifest.txt",
            "drift": [],
        },
        "timing": lambda: {
            "source": "hf-timestd-authority",
            "t_level_active": "T2",
            "sigma_ns": 12000,
            "t_level_witnesses": ["T2", "T3"],
            "disagreement_flags": [],
            "t6_authority_state": "AUTHORITATIVE",
            "snapshot_age_s": 3.2,
        },
        "gaps": lambda: {
            "row_utc": "2026-08-20T13:00Z",
            "gaps": 0,
            "channel_hours": 6.0,
            "rate": 0.0,
            "row_age_s": 1200.0,
        },
        "uploads": lambda: {
            "readable": True,
            "pipelines": [],
            "cursors": [{
                "source_id": "b4", "dest_id": "wd30",
                "table_name": "spots", "last_ack": "2026-08-20T14:00:00Z",
                "cursor_len": 17,
            }],
        },
        "doctor": lambda: (True, []),
        "resources": lambda: {
            "llc": {"available": True, "radiod_occupancy_mib": 3.5},
            "irqs": {"xhci_hcd": {"delta_available": True,
                                  "observed_cores": [12, 13],
                                  "expected_cores": [12, 13],
                                  "per_core_count": [0, 1]}},
            "radiod": {"gaps": 0, "channel_hours": 6.0,
                       "gap_rate_per_channel_hour": 0.0},
            "udp": {"interval_s": 300.0},
        },
    }
    readers.update(overrides)
    return readers


def walk_keys(obj, path=""):
    """Yield (dotted_path, key) for every dict key at any depth."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else str(k)
            yield here, k
            yield from walk_keys(v, here)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from walk_keys(v, f"{path}[{i}]")


# ---------------------------------------------------------------------------
# 1. THE forbidden-fields test — this one is the point of the feature
# ---------------------------------------------------------------------------

FORBIDDEN = (
    "completeness_pct",
    "pending_uploads",
    "pending_count",
    "seconds_detected",
    "mismatched",
    "upload_queue_depth",
)


def test_envelope_carries_no_lying_metric_anywhere_at_any_depth():
    """No counter known to lie may appear as a key at ANY depth.

    Every name in FORBIDDEN produced a WRONG operational conclusion on
    this fleet before it was caught; see docs/PRODUCER-THREAT-MODEL.md
    ("Metrics that lie" table, and "Observability" as a threat class):

      * completeness_pct / samples_written read 100% over dropped
        blocks — radiod zero-fills, the recorder faithfully writes the
        zeros.  gap_count in the raw_buffer sidecars is the only honest
        loss signal, which is why the heartbeat's `gaps` block is fed
        by the gap-hourly TSV and nothing else.
      * pending_uploads / pending_count / upload_queue_depth count the
        SOURCE side of a transport that never DELETEs; a growing queue
        with a frozen oldest entry is normal, and a shrinking one
        proves nothing about delivery.  The heartbeat carries the
        uploader's own dead-letter and deliverable counts instead.
      * seconds_detected / mismatched are duration-shaped restatements
        of an event count; each gap costs up to +/-25.6 s of validity
        regardless of its own duration, so COUNT is the honest unit.

    Substring match, not exact match: `radiod_completeness_pct` would
    be the same lie wearing a prefix.
    """
    envelope = assemble(NOW, CONFIG, rich_readers(), uptime_s=98765.4)
    # Round-trip through JSON: this is the wire form the server sees,
    # and it proves the envelope is serializable at the same time.
    round_tripped = json.loads(json.dumps(envelope))

    offenders = [
        (where, key)
        for where, key in walk_keys(round_tripped)
        for bad in FORBIDDEN
        if bad in str(key)
    ]
    assert offenders == [], (
        "lying metric(s) present in the heartbeat envelope: "
        + ", ".join(f"{k!r} at {w}" for w, k in offenders)
    )


def test_key_walker_actually_finds_a_planted_key():
    """The forbidden-fields test is only worth as much as its walker."""
    planted = {"a": [{"b": {"completeness_pct": 100}}]}
    found = [k for _, k in walk_keys(planted)]
    assert "completeness_pct" in found
    assert set(found) == {"a", "b", "completeness_pct"}


# ---------------------------------------------------------------------------
# 2. Unknown paths — every block must be able to say "I don't know"
# ---------------------------------------------------------------------------

class FakeFinding:
    """Stand-in for bin/smd's Finding (component/kind/detail/fixable)."""

    def __init__(self, component, kind, detail):
        self.component = component
        self.kind = kind
        self.detail = detail


@pytest.mark.parametrize("block", heartbeat_schema.BLOCK_NAMES)
def test_any_reader_raising_makes_that_block_indeterminate(block):
    def boom():
        raise RuntimeError("boom")

    env = assemble(NOW, CONFIG, rich_readers(**{block: boom}))
    assert env["blocks"][block]["verdict"] == "INDETERMINATE"
    assert env["blocks"][block]["reason"] == (
        f"{block} reader raised RuntimeError: boom")
    # "nothing measured" can never render healthy.
    assert env["rollup"]["verdict"] != "VALID"
    assert env["rollup"]["reason"].startswith(f"{block}: ")


@pytest.mark.parametrize("block", heartbeat_schema.BLOCK_NAMES)
def test_missing_reader_is_indeterminate_not_a_crash(block):
    readers = rich_readers()
    del readers[block]
    env = assemble(NOW, CONFIG, readers)
    assert env["blocks"][block]["verdict"] == "INDETERMINATE"
    assert env["blocks"][block]["reason"] == f"no {block} reader wired"


def test_reader_unavailable_message_surfaces_verbatim():
    """ReaderUnavailable is the reader's own sentence — no class-name noise."""
    def stale():
        raise ReaderUnavailable("authority.json stale (94s old)")

    env = assemble(NOW, CONFIG, rich_readers(timing=stale))
    assert env["blocks"]["timing"] == {
        "verdict": "INDETERMINATE",
        "reason": "authority.json stale (94s old)",
    }


def test_timing_none_is_indeterminate():
    env = assemble(NOW, CONFIG, rich_readers(timing=lambda: None))
    assert env["blocks"]["timing"]["verdict"] == "INDETERMINATE"


def test_versions_empty_is_indeterminate_not_valid():
    env = assemble(NOW, CONFIG,
                   rich_readers(versions=lambda: {"components": {}}))
    block = env["blocks"]["versions"]
    assert block["verdict"] == "INDETERMINATE"
    assert block["reason"] == "no component versions readable"


def test_manifest_absent_is_indeterminate_with_the_doctor_wording():
    env = assemble(NOW, CONFIG, rich_readers(manifest=lambda: {
        "present": False, "blessed_source": None, "drift": []}))
    block = env["blocks"]["manifest"]
    assert block["verdict"] == "INDETERMINATE"
    assert block["reason"] == (
        "manifest unassessed — host not on a blessed image")


def test_gaps_na_row_is_indeterminate_never_zero():
    """The sampler writes literal NA rather than a lying 0.00 rate."""
    env = assemble(NOW, CONFIG, rich_readers(gaps=lambda: {
        "row_utc": "2026-08-20T13:00Z", "gaps": None, "channel_hours": 0.0,
        "rate": None, "row_age_s": 600.0}))
    block = env["blocks"]["gaps"]
    assert block["verdict"] == "INDETERMINATE"
    assert block["reason"] == "no channels measured last sampled hour"
    assert block["data"]["gap_rate"] is None
    assert block["data"]["gaps"] is None


def test_gaps_stale_row_is_indeterminate_naming_the_age():
    env = assemble(NOW, CONFIG, rich_readers(gaps=lambda: {
        "row_utc": "2026-08-20T02:00Z", "gaps": 0, "channel_hours": 6.0,
        "rate": 0.0, "row_age_s": 9000.0}))
    block = env["blocks"]["gaps"]
    assert block["verdict"] == "INDETERMINATE"
    assert "9000s old" in block["reason"]
    assert "7200s" in block["reason"]


def test_gaps_stale_beats_a_zero_row():
    """A stale row of zeros must not read as a clean hour."""
    env = assemble(NOW, CONFIG, rich_readers(gaps=lambda: {
        "row_utc": "2026-08-19T02:00Z", "gaps": 0, "channel_hours": 6.0,
        "rate": 0.0, "row_age_s": 90000.0}))
    assert env["blocks"]["gaps"]["verdict"] == "INDETERMINATE"


def test_uploads_unreadable_is_indeterminate_before_touching_pipelines():
    """readable:false responses OMIT pipelines/cursors entirely."""
    env = assemble(NOW, CONFIG, rich_readers(uploads=lambda: {
        "readable": False,
        "reason": "OperationalError: unable to open database file"}))
    block = env["blocks"]["uploads"]
    assert block["verdict"] == "INDETERMINATE"
    assert block["reason"] == (
        "OperationalError: unable to open database file")


def test_uploads_unreadable_without_a_reason_still_indeterminate():
    env = assemble(NOW, CONFIG, rich_readers(uploads=lambda: {"readable": False}))
    assert env["blocks"]["uploads"]["verdict"] == "INDETERMINATE"
    assert env["blocks"]["uploads"]["reason"] == "upload backlog unreadable"


def test_uploads_readable_with_no_pipelines_is_valid():
    """A fully-idle pipeline is ABSENT from `pipelines` (hs-uploader f5406ca)."""
    env = assemble(NOW, CONFIG, rich_readers(uploads=lambda: {
        "readable": True, "pipelines": [], "cursors": []}))
    block = env["blocks"]["uploads"]
    assert block["verdict"] == "VALID"
    assert block["reason"] == "no backlog, no dead letters"


def test_doctor_clean_none_is_indeterminate():
    env = assemble(NOW, CONFIG, rich_readers(doctor=lambda: (
        None, [FakeFinding("ownership", "ownership-unassessed", "boom")])))
    block = env["blocks"]["doctor"]
    assert block["verdict"] == "INDETERMINATE"
    assert "ownership-unassessed" in block["reason"]
    # nothing collected is lost, even when the sweep was incomplete
    assert block["data"]["findings"][0]["detail"] == "boom"


def test_resources_probe_errors_are_inconclusive():
    env = assemble(NOW, CONFIG, rich_readers(resources=lambda: {
        "llc": {"available": False},
        "irqs": {},
        "radiod": {"gap_rate_per_channel_hour": None},
        "errors": ["/proc/net/snmp: PermissionError"]}))
    block = env["blocks"]["resources"]
    assert block["verdict"] == "INCONCLUSIVE"
    assert "/proc/net/snmp: PermissionError" in block["reason"]
    assert block["data"]["llc"] == {"available": False}
    assert block["data"]["irq"] == {"delta_available": False}
    assert block["data"]["gap_rate_per_channel_hour"] is None


def test_timing_standalone_fallback_is_inconclusive_not_valid():
    env = assemble(NOW, CONFIG, rich_readers(timing=lambda: {
        "source": "standalone-fallback", "t_level_active": None,
        "sigma_ns": None, "t_level_witnesses": [],
        "disagreement_flags": [], "snapshot_age_s": None}))
    block = env["blocks"]["timing"]
    assert block["verdict"] == "INCONCLUSIVE"
    assert "standalone fallback" in block["reason"]


# ---------------------------------------------------------------------------
# 3. INVALID paths — measured, conclusive, bad
# ---------------------------------------------------------------------------

def test_gaps_above_zero_is_invalid_naming_count_and_rate():
    env = assemble(NOW, CONFIG, rich_readers(gaps=lambda: {
        "row_utc": "2026-08-20T13:00Z", "gaps": 7, "channel_hours": 6.0,
        "rate": 1.1667, "row_age_s": 900.0}))
    block = env["blocks"]["gaps"]
    assert block["verdict"] == "INVALID"
    assert block["reason"] == "7 gap events (rate 1.17/ch-hr)"
    assert block["data"]["gap_rate"] == pytest.approx(1.1667)


def test_manifest_drift_is_invalid_naming_components():
    env = assemble(NOW, CONFIG, rich_readers(manifest=lambda: {
        "present": True, "blessed_source": "/etc/sigmond-appliance/manifest.txt",
        "drift": [
            {"component": "ka9q-radio", "status": "moved",
             "manifest": "aaaaaaa", "live": "bbbbbbb"},
            {"component": "hf-timestd", "status": "live_only",
             "manifest": None, "live": "ccccccc"},
        ]}))
    block = env["blocks"]["manifest"]
    assert block["verdict"] == "INVALID"
    assert "ka9q-radio" in block["reason"]
    assert "hf-timestd" in block["reason"]


def test_uploads_dead_letters_are_invalid():
    env = assemble(NOW, CONFIG, rich_readers(uploads=lambda: {
        "readable": True,
        "pipelines": [
            {"name": "wsprnet", "deliverable_count": 0, "dead_letter_count": 4},
            {"name": "pskreporter", "deliverable_count": 9,
             "dead_letter_count": 0},
        ],
        "cursors": []}))
    block = env["blocks"]["uploads"]
    assert block["verdict"] == "INVALID"
    assert "wsprnet(4)" in block["reason"]


def test_uploads_deliverables_only_are_inconclusive():
    env = assemble(NOW, CONFIG, rich_readers(uploads=lambda: {
        "readable": True,
        "pipelines": [{"name": "pskreporter", "deliverable_count": 9,
                       "dead_letter_count": 0}],
        "cursors": []}))
    block = env["blocks"]["uploads"]
    assert block["verdict"] == "INCONCLUSIVE"
    assert block["reason"] == "9 deliverables retrying (pskreporter)"


def test_doctor_findings_are_invalid_listing_kinds():
    env = assemble(NOW, CONFIG, rich_readers(doctor=lambda: (False, [
        FakeFinding("ka9q-radio", "dirty", "3 modified files"),
        FakeFinding("sigmond", "unpushed", "2 commits ahead"),
    ])))
    block = env["blocks"]["doctor"]
    assert block["verdict"] == "INVALID"
    assert "dirty" in block["reason"] and "unpushed" in block["reason"]
    assert block["data"]["findings"][0]["component"] == "ka9q-radio"


def test_timing_disagreement_flags_are_invalid():
    env = assemble(NOW, CONFIG, rich_readers(timing=lambda: {
        "source": "hf-timestd-authority", "t_level_active": "T6",
        "sigma_ns": 70000000, "t_level_witnesses": ["T2"],
        "disagreement_flags": ["T6_VS_T2_EPOCH", "T6_JUDGE_CRITICAL"],
        "t6_authority_state": "DEGRADED", "snapshot_age_s": 1.0}))
    block = env["blocks"]["timing"]
    assert block["verdict"] == "INVALID"
    assert "T6_VS_T2_EPOCH" in block["reason"]
    assert "T6_JUDGE_CRITICAL" in block["reason"]
    assert block["data"]["t6_authority_state"] == "DEGRADED"


def test_all_healthy_rolls_up_valid():
    env = assemble(NOW, CONFIG, rich_readers(), uptime_s=1.0)
    assert env["rollup"]["verdict"] == "VALID", env["blocks"]
    assert all(b["verdict"] == "VALID" for b in env["blocks"].values())


# ---------------------------------------------------------------------------
# 4. Rollup precedence
# ---------------------------------------------------------------------------

def _blocks_with(**verdicts):
    return {name: {"verdict": verdicts.get(name, "VALID"),
                   "reason": f"{name} reason"}
            for name in heartbeat_schema.BLOCK_NAMES}


@pytest.mark.parametrize("verdicts,expected", [
    ({}, "VALID"),
    ({"timing": "INCONCLUSIVE"}, "INCONCLUSIVE"),
    ({"timing": "INDETERMINATE"}, "INDETERMINATE"),
    ({"timing": "INVALID"}, "INVALID"),
    ({"timing": "INCONCLUSIVE", "gaps": "INDETERMINATE"}, "INDETERMINATE"),
    ({"timing": "INDETERMINATE", "gaps": "INVALID"}, "INVALID"),
    ({"timing": "INCONCLUSIVE", "gaps": "INVALID",
      "doctor": "INDETERMINATE"}, "INVALID"),
    ({name: "INCONCLUSIVE" for name in heartbeat_schema.BLOCK_NAMES},
     "INCONCLUSIVE"),
])
def test_rollup_worst_wins(verdicts, expected):
    from sigmond.heartbeat import rollup as rollup_fn
    assert rollup_fn(_blocks_with(**verdicts))["verdict"] == expected


def test_rollup_tie_resolves_to_first_block_in_declared_order():
    from sigmond.heartbeat import rollup as rollup_fn
    # manifest precedes doctor in BLOCK_NAMES
    out = rollup_fn(_blocks_with(doctor="INVALID", manifest="INVALID"))
    assert out["verdict"] == "INVALID"
    assert out["reason"] == "manifest: manifest reason"


def test_rollup_reason_points_at_the_evidence():
    env = assemble(NOW, CONFIG, rich_readers(gaps=lambda: {
        "row_utc": "2026-08-20T13:00Z", "gaps": 2, "channel_hours": 6.0,
        "rate": 0.33, "row_age_s": 100.0}))
    assert env["rollup"]["reason"] == "gaps: 2 gap events (rate 0.33/ch-hr)"


def test_rollup_of_nothing_is_indeterminate_never_valid():
    from sigmond.heartbeat import rollup as rollup_fn
    assert rollup_fn({})["verdict"] == "INDETERMINATE"


def test_precedence_table_covers_every_verdict():
    assert set(heartbeat_schema.PRECEDENCE) == set(heartbeat_schema.VERDICTS)
    assert (heartbeat_schema.PRECEDENCE["INVALID"]
            > heartbeat_schema.PRECEDENCE["INDETERMINATE"]
            > heartbeat_schema.PRECEDENCE["INCONCLUSIVE"]
            > heartbeat_schema.PRECEDENCE["VALID"])


# ---------------------------------------------------------------------------
# 5. write_tick / prune
# ---------------------------------------------------------------------------

def test_write_tick_is_atomic_and_leaves_no_tmp(tmp_path):
    env = assemble(NOW, CONFIG, rich_readers())
    path = write_tick(env, spool_dir=tmp_path)

    assert path.name == "AC0G-B4_20260820T140506Z.json"
    assert path.parent == tmp_path
    assert list(tmp_path.glob("*.tmp")) == []
    assert json.loads(path.read_text()) == env


def test_write_tick_creates_the_spool_dir(tmp_path):
    env = assemble(NOW, CONFIG, rich_readers())
    spool = tmp_path / "deep" / "spool"
    path = write_tick(env, spool_dir=spool)
    assert path.exists()


def test_write_tick_removes_its_tmp_when_the_write_fails(tmp_path, monkeypatch):
    env = assemble(NOW, CONFIG, rich_readers())

    def explode(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)
    with pytest.raises(OSError):
        write_tick(env, spool_dir=tmp_path)
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob("*.json")) == []


def test_write_tick_station_name_cannot_escape_the_spool(tmp_path):
    env = assemble(NOW, dict(CONFIG, station="../../etc/evil"),
                   rich_readers())
    path = write_tick(env, spool_dir=tmp_path)
    assert path.parent == tmp_path
    assert "/" not in path.name.replace(".json", "")


@pytest.mark.parametrize("station", [".b4", "..b4", ".", "..", "./b4"])
def test_write_tick_station_name_can_never_produce_a_dotfile(tmp_path,
                                                             station):
    """A dotfile tick is a PERMANENTLY INVISIBLE heartbeat.

    The server ingest skips dotfiles by design (they are never ours, and
    the drop's only completeness guarantee is the .json suffix).  A
    station configured as ".b4" would therefore spool, upload and be
    ignored on arrival, with every hop reporting success — the exact
    silent-failure shape this feature exists to remove.
    """
    env = assemble(NOW, dict(CONFIG, station=station), rich_readers())
    path = write_tick(env, spool_dir=tmp_path)
    assert not path.name.startswith("."), path.name
    assert path.name.endswith(".json")
    assert path.parent == tmp_path


def test_prune_removes_only_ticks_older_than_24h(tmp_path):
    now = time.time()
    fresh = tmp_path / "s_20260820T140506Z.json"
    old = tmp_path / "s_20260819T010203Z.json"
    keeper_tmp = tmp_path / "s_20260819T010203Z.json.tmp"
    for p in (fresh, old, keeper_tmp):
        p.write_text("{}")
    os.utime(fresh, (now - 3600, now - 3600))
    os.utime(old, (now - 25 * 3600, now - 25 * 3600))
    os.utime(keeper_tmp, (now - 25 * 3600, now - 25 * 3600))

    pruned = prune_spool(tmp_path, now=now)

    assert pruned == 1
    assert fresh.exists()
    assert not old.exists()
    # only *.json is pruned; a stray .tmp is a defect to notice, not to
    # quietly sweep up
    assert keeper_tmp.exists()


def test_write_tick_prunes_while_keeping_the_newest(tmp_path):
    ancient = tmp_path / "AC0G-B4_20260101T000000Z.json"
    ancient.write_text("{}")
    old = time.time() - 48 * 3600
    os.utime(ancient, (old, old))

    env = assemble(NOW, CONFIG, rich_readers())
    path = write_tick(env, spool_dir=tmp_path)

    assert path.exists()
    assert not ancient.exists()


# ---------------------------------------------------------------------------
# 6. validate()
# ---------------------------------------------------------------------------

def test_validate_accepts_assemble_output():
    env = assemble(NOW, CONFIG, rich_readers(), uptime_s=1.0)
    assert heartbeat_schema.validate(env) == []


def test_validate_accepts_an_all_unknown_envelope():
    """An envelope where NOTHING could be read is still well-formed."""
    def boom():
        raise RuntimeError("boom")

    env = assemble(NOW, CONFIG, {n: boom for n in heartbeat_schema.BLOCK_NAMES})
    assert heartbeat_schema.validate(env) == []
    assert env["rollup"]["verdict"] == "INDETERMINATE"


@pytest.mark.parametrize("mutate,needle", [
    (lambda e: e.update(kind="something_else"), "kind"),
    (lambda e: e.update(schema_version=99), "schema_version"),
    (lambda e: e.pop("schema_version"), "schema_version"),
    (lambda e: e.pop("station"), "station"),
    (lambda e: e.update(station=""), "station"),
    (lambda e: e.update(station=None), "station"),
    (lambda e: e.pop("emitted_at"), "emitted_at"),
    (lambda e: e.pop("interval_sec"), "interval_sec"),
    (lambda e: e.pop("rollup"), "rollup"),
    (lambda e: e["rollup"].update(verdict="OK"), "rollup verdict"),
    (lambda e: e["blocks"].update(sunspots={"verdict": "VALID",
                                            "reason": "r"}), "unknown block"),
    (lambda e: e["blocks"]["gaps"].pop("verdict"), "no verdict"),
    (lambda e: e["blocks"]["gaps"].pop("reason"), "no reason"),
    (lambda e: e["blocks"]["gaps"].update(verdict="PROBABLY_FINE"), "gaps"),
    (lambda e: e.pop("blocks"), "blocks missing"),
])
def test_validate_rejects_structural_damage(mutate, needle):
    env = assemble(NOW, CONFIG, rich_readers())
    mutate(env)
    errors = heartbeat_schema.validate(env)
    assert errors, f"expected an error mentioning {needle!r}"
    assert any(needle in e for e in errors), errors


def test_validate_allows_additive_fields_everywhere():
    env = assemble(NOW, CONFIG, rich_readers())
    env["future_field"] = {"anything": 1}
    env["blocks"]["timing"]["confidence"] = 0.9
    env["blocks"]["timing"]["data"]["new_tier"] = "T7"
    assert heartbeat_schema.validate(env) == []


def test_validate_rejects_a_non_dict():
    assert heartbeat_schema.validate(["not", "a", "dict"])


# ---------------------------------------------------------------------------
# 7. station is required
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("config", [
    {"interval_sec": 300},
    {"station": "", "interval_sec": 300},
    {"station": "   ", "interval_sec": 300},
    {"station": None, "interval_sec": 300},
    {"station": 42, "interval_sec": 300},
])
def test_assemble_without_a_station_raises(config):
    """Failing the unit loudly beats emitting an unattributable heartbeat."""
    with pytest.raises(ValueError, match="station"):
        assemble(NOW, config, rich_readers())


def test_grid_is_truncated_to_six_characters():
    env = assemble(NOW, CONFIG, rich_readers())
    assert env["grid"] == "EM38ww"


def test_grid_none_stays_none():
    env = assemble(NOW, dict(CONFIG, grid=None), rich_readers())
    assert env["grid"] is None


def test_emitted_at_is_utc_iso8601_z():
    env = assemble(NOW, CONFIG, rich_readers())
    assert env["emitted_at"] == "2026-08-20T14:05:06Z"
    # epoch seconds are accepted too, and land on the same instant
    from_epoch = assemble(NOW.timestamp(), CONFIG, rich_readers())
    assert from_epoch["emitted_at"] == env["emitted_at"]


def test_uptime_is_none_not_zero_when_unavailable():
    env = assemble(NOW, CONFIG, rich_readers())
    assert env["uptime_s"] is None


# ---------------------------------------------------------------------------
# 8. gap-hourly TSV parsing — the cross-repo format contract
# ---------------------------------------------------------------------------

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gap-hourly.tsv"


def test_fixture_header_matches_the_samplers_own_header():
    """If gap_hourly.HEADER ever changes, this fixture is stale."""
    from sigmond.gap_hourly import HEADER
    assert FIXTURE.read_text().splitlines()[0] == HEADER


def test_parse_gap_row_reads_the_last_data_row():
    now = datetime(2026, 8, 20, 13, 30, tzinfo=timezone.utc).timestamp()
    row = parse_gap_row(FIXTURE.read_text(), now=now)
    assert row["row_utc"] == "2026-08-20T13:00Z"
    assert row["gaps"] is None            # literal NA -> not measured
    assert row["rate"] is None
    assert row["channel_hours"] == 0.0
    assert row["row_age_s"] == pytest.approx(1800.0)


def test_parse_gap_row_reads_counts_and_rates():
    text = "\n".join(FIXTURE.read_text().splitlines()[:3])  # ends on the 3-gap row
    now = datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc).timestamp()
    row = parse_gap_row(text, now=now)
    assert row["gaps"] == 3
    assert row["rate"] == pytest.approx(0.5)
    assert row["channel_hours"] == pytest.approx(6.0)
    assert row["row_age_s"] == pytest.approx(1800.0)


def test_parse_gap_row_header_only_file_raises():
    from sigmond.gap_hourly import HEADER
    with pytest.raises(ReaderUnavailable, match="no data rows"):
        parse_gap_row(HEADER + "\n")


def test_parse_gap_row_empty_file_raises():
    with pytest.raises(ReaderUnavailable, match="no data rows"):
        parse_gap_row("")


def test_parse_gap_row_malformed_row_raises():
    with pytest.raises(ReaderUnavailable, match="malformed"):
        parse_gap_row("2026-08-20T13:00Z\t0\n")


def test_parse_gap_row_unparseable_timestamp_has_no_age():
    row = parse_gap_row("not-a-time\t0\t6.00\t0.00\t1\n", now=1000.0)
    assert row["row_age_s"] is None
    # ... and that becomes INDETERMINATE, never a fresh clean hour
    env = assemble(NOW, CONFIG, rich_readers(gaps=lambda: row))
    assert env["blocks"]["gaps"]["verdict"] == "INDETERMINATE"


def test_parses_a_row_actually_produced_by_the_sampler(tmp_path):
    """Cross-check against gap_hourly's real writer, not a hand-typed row.

    This is the live end of the cross-repo contract: whatever
    build_row()/append_row() emit must parse here, including the
    honest NA row an empty window produces.
    """
    from sigmond.gap_hourly import append_row, build_row

    out = tmp_path / "gap-hourly.tsv"
    fields = build_row(base=str(tmp_path / "no-such-raw-buffer"))
    append_row(out, fields)

    row = parse_gap_row(out.read_text())
    assert row["gaps"] is None           # empty window -> NA, never 0
    assert row["rate"] is None
    assert row["channel_hours"] == 0.0
    assert row["row_age_s"] is not None and row["row_age_s"] < 120

    env = assemble(NOW, CONFIG, rich_readers(gaps=lambda: row))
    assert env["blocks"]["gaps"]["verdict"] == "INDETERMINATE"
    assert env["blocks"]["gaps"]["reason"] == (
        "no channels measured last sampled hour")


# ---------------------------------------------------------------------------
# 9. Default (production) readers — the wiring, exercised without a host
# ---------------------------------------------------------------------------

from sigmond.heartbeat import (  # noqa: E402  (grouped with its own section)
    HeartbeatPaths,
    _read_authority,
    _read_backlog,
    _read_gap_tsv,
    default_readers,
    read_uptime,
)

AUTHORITY_V1 = {
    "schema": "v1",
    "utc_published": "2026-08-20T14:05:00Z",
    "a_level": "A1",
    "t_level_active": "T2",
    "t_level_available": ["T2", "T3"],
    "t_level_witnesses": ["T3"],
    "rtp_to_utc_offset_ns": 123456,
    "sigma_ns": 9000,
    "stations_contributing": [],
    "last_transition_utc": None,
    "disagreement_flags": [],
    "t6_authority_state": "AUTHORITATIVE",
}


def test_read_authority_missing_file(tmp_path):
    with pytest.raises(ReaderUnavailable, match="^authority.json missing$"):
        _read_authority(str(tmp_path / "nope.json"))


def test_read_authority_stale_names_the_age(tmp_path):
    path = tmp_path / "authority.json"
    path.write_text(json.dumps(AUTHORITY_V1))
    now = time.time()
    os.utime(path, (now - 120, now - 120))
    with pytest.raises(ReaderUnavailable) as excinfo:
        _read_authority(str(path), now=now)
    assert str(excinfo.value) == "authority.json stale (120s old)"


def test_read_authority_fresh_returns_the_carried_fields(tmp_path):
    path = tmp_path / "authority.json"
    path.write_text(json.dumps(AUTHORITY_V1))
    now = time.time()
    os.utime(path, (now - 5, now - 5))

    out = _read_authority(str(path), now=now)

    assert out["source"] == "hf-timestd-authority"
    assert out["t_level_active"] == "T2"
    assert out["sigma_ns"] == 9000
    assert out["t_level_witnesses"] == ["T3"]
    assert out["disagreement_flags"] == []
    assert out["t6_authority_state"] == "AUTHORITATIVE"
    assert out["snapshot_age_s"] == pytest.approx(5.0, abs=0.5)
    # and it maps to a VALID timing block
    env = assemble(NOW, CONFIG, rich_readers(timing=lambda: out))
    assert env["blocks"]["timing"]["verdict"] == "VALID"


def test_read_authority_unsupported_schema_is_unavailable(tmp_path):
    path = tmp_path / "authority.json"
    path.write_text(json.dumps(dict(AUTHORITY_V1, schema="v2")))
    with pytest.raises(ReaderUnavailable, match="unsupported schema"):
        _read_authority(str(path), now=time.time())


def test_read_authority_corrupt_json_is_a_surprise_not_a_tidy_state(tmp_path):
    """A corrupt authority file must read as a surprise, not an expected
    state — assemble attaches the exception class so it looks like one."""
    path = tmp_path / "authority.json"
    path.write_text("{not json")
    now = time.time()
    os.utime(path, (now - 1, now - 1))

    with pytest.raises(ValueError):
        _read_authority(str(path), now=now)

    env = assemble(NOW, CONFIG,
                   rich_readers(timing=lambda: _read_authority(str(path), now=now)))
    block = env["blocks"]["timing"]
    assert block["verdict"] == "INDETERMINATE"
    assert "JSONDecodeError" in block["reason"]


def test_read_gap_tsv_missing_file_is_unavailable(tmp_path):
    with pytest.raises(ReaderUnavailable, match="never ran"):
        _read_gap_tsv(str(tmp_path / "gap-hourly.tsv"))


def test_read_gap_tsv_reads_the_fixture(tmp_path):
    path = tmp_path / "gap-hourly.tsv"
    path.write_text(FIXTURE.read_text())
    row = _read_gap_tsv(str(path),
                        now=datetime(2026, 8, 20, 13, 30,
                                     tzinfo=timezone.utc).timestamp())
    assert row["row_utc"] == "2026-08-20T13:00Z"


def test_default_readers_doctor_is_indeterminate_until_injected():
    """collect_findings lives in bin/smd; heartbeat.py must never import it."""
    readers = default_readers(HeartbeatPaths())
    with pytest.raises(ReaderUnavailable, match="doctor findings not wired"):
        readers["doctor"]()

    env = assemble(NOW, CONFIG, dict(rich_readers(), doctor=readers["doctor"]))
    assert env["blocks"]["doctor"] == {
        "verdict": "INDETERMINATE", "reason": "doctor findings not wired"}


def test_default_readers_uses_the_injected_doctor_reader():
    readers = default_readers(HeartbeatPaths(), doctor_reader=lambda: (True, []))
    assert readers["doctor"]() == (True, [])
    assert set(readers) == set(heartbeat_schema.BLOCK_NAMES)


def test_heartbeat_module_does_not_reach_into_bin_smd():
    """No import machinery: the library must not depend on the CLI.

    Checked against the parsed AST, not the text, so the module is free
    to EXPLAIN in prose why it does not do this.
    """
    import ast

    import sigmond.heartbeat as hb

    tree = ast.parse(Path(hb.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not any(m.startswith("importlib") for m in imported), imported
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "SourceFileLoader" not in (names | attrs)


def test_read_backlog_unreachable_module_is_unavailable(tmp_path):
    paths = HeartbeatPaths(hs_uploader_src=str(tmp_path / "no-such-src"),
                           watermarks_db=str(tmp_path / "watermarks.db"))
    with pytest.raises(ReaderUnavailable, match="hs_uploader.backlog"):
        _read_backlog(paths)


def test_read_uptime_parses_proc_uptime(tmp_path):
    path = tmp_path / "uptime"
    path.write_text("123456.78 987654.32\n")
    assert read_uptime(str(path)) == pytest.approx(123456.78)


def test_read_uptime_is_none_not_zero_on_failure(tmp_path):
    assert read_uptime(str(tmp_path / "nope")) is None


def test_heartbeat_schema_imports_nothing_from_sigmond():
    """It is rsynced verbatim next to the server code on another host."""
    source = Path(heartbeat_schema.__file__).read_text()
    for line in source.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("import "), stripped
        assert not stripped.startswith("from "), stripped


# ---------------------------------------------------------------------------
# 10. Review fixes — states that used to render healthy and must not
# ---------------------------------------------------------------------------

# Mirrors authority_manager._build_bootstrap_pending_state(): a real,
# fresh, schema-v1 authority.json published while the bootstrap
# coordinator has probing gated.  No tier, no witnesses, no offset, and
# — crucially — no disagreement flags, so every other check passes.
BOOTSTRAP_PENDING = {
    "source": "hf-timestd-authority",
    "t_level_active": None,
    "sigma_ns": None,
    "t_level_witnesses": [],
    "disagreement_flags": [],
    "t6_authority_state": None,
    "snapshot_age_s": 2.0,
}


def test_bootstrap_pending_authority_is_inconclusive_not_valid():
    env = assemble(NOW, CONFIG, rich_readers(timing=lambda: BOOTSTRAP_PENDING))
    block = env["blocks"]["timing"]
    assert block["verdict"] == "INCONCLUSIVE"
    assert block["reason"] == (
        "authority present but no adjudicated tier and no witnesses")
    assert env["rollup"]["verdict"] == "INCONCLUSIVE"


def test_tier_t0_is_inconclusive_not_valid():
    env = assemble(NOW, CONFIG, rich_readers(timing=lambda: dict(
        BOOTSTRAP_PENDING, t_level_active="T0", t_level_witnesses=["T3"])))
    block = env["blocks"]["timing"]
    assert block["verdict"] == "INCONCLUSIVE"
    assert block["reason"] == "authority present but no adjudicated tier"


def test_tier_active_but_no_witnesses_is_inconclusive():
    env = assemble(NOW, CONFIG, rich_readers(timing=lambda: dict(
        BOOTSTRAP_PENDING, t_level_active="T2", sigma_ns=9000,
        t_level_witnesses=[])))
    block = env["blocks"]["timing"]
    assert block["verdict"] == "INCONCLUSIVE"
    assert block["reason"] == "authority present but no witnesses"
    assert env["rollup"]["verdict"] != "VALID"


def test_a_witnessed_tier_is_still_valid():
    """The fix must not swallow the genuinely-good case."""
    env = assemble(NOW, CONFIG, rich_readers())
    assert env["blocks"]["timing"]["verdict"] == "VALID"
    assert env["blocks"]["timing"]["reason"] == (
        "T2 active, sigma 12000 ns, 2 witness(es)")


@pytest.mark.parametrize("bad", ["VALIID", "ok", "", None, 0, "valid"])
def test_unknown_verdict_never_rolls_up_valid(bad):
    """Fail closed: a verdict this schema cannot interpret is not health."""
    from sigmond.heartbeat import rollup as rollup_fn
    blocks = _blocks_with()
    blocks["timing"]["verdict"] = bad
    out = rollup_fn(blocks)
    assert out["verdict"] == "INDETERMINATE"
    assert out["verdict"] in heartbeat_schema.VERDICTS
    assert "unknown verdict" in out["reason"]
    assert "timing" in out["reason"]


def test_block_with_no_verdict_key_never_rolls_up_valid():
    from sigmond.heartbeat import rollup as rollup_fn
    blocks = _blocks_with()
    blocks["gaps"].pop("verdict")
    assert rollup_fn(blocks)["verdict"] == "INDETERMINATE"


def test_unknown_verdict_loses_to_a_real_invalid():
    from sigmond.heartbeat import rollup as rollup_fn
    blocks = _blocks_with(doctor="INVALID")
    blocks["timing"]["verdict"] = "VALIID"
    out = rollup_fn(blocks)
    assert out["verdict"] == "INVALID"
    assert out["reason"].startswith("doctor: ")


def test_interval_sec_defaults_so_the_envelope_always_validates():
    config = {"station": "AC0G-B4", "callsign": "AC0G", "grid": "EM38ww"}
    env = assemble(NOW, config, rich_readers())
    assert env["interval_sec"] == 300
    assert heartbeat_schema.validate(env) == []


def test_explicit_interval_sec_is_preserved():
    env = assemble(NOW, dict(CONFIG, interval_sec=60), rich_readers())
    assert env["interval_sec"] == 60


def test_doctor_not_clean_with_no_findings_is_indeterminate():
    """Contradictory input fails toward caution, not toward healthy."""
    env = assemble(NOW, CONFIG, rich_readers(doctor=lambda: (False, [])))
    block = env["blocks"]["doctor"]
    assert block["verdict"] == "INDETERMINATE"
    assert block["reason"] == "doctor reported not-clean but no findings"
    assert env["rollup"]["verdict"] != "VALID"


def test_doctor_clean_true_with_no_findings_is_still_valid():
    env = assemble(NOW, CONFIG, rich_readers(doctor=lambda: (True, [])))
    assert env["blocks"]["doctor"]["verdict"] == "VALID"


def test_future_stamped_gap_row_is_indeterminate():
    env = assemble(NOW, CONFIG, rich_readers(gaps=lambda: {
        "row_utc": "2026-08-20T15:00Z", "gaps": 0, "channel_hours": 6.0,
        "rate": 0.0, "row_age_s": -3300.0}))
    block = env["blocks"]["gaps"]
    assert block["verdict"] == "INDETERMINATE"
    assert block["reason"] == "gap row is future-stamped (clock step?)"
    assert env["rollup"]["verdict"] != "VALID"


def test_future_stamped_row_with_gaps_still_reports_indeterminate():
    """Age is not evidence when the two clocks disagree."""
    env = assemble(NOW, CONFIG, rich_readers(gaps=lambda: {
        "row_utc": "2026-08-20T15:00Z", "gaps": 4, "channel_hours": 6.0,
        "rate": 0.67, "row_age_s": -60.0}))
    assert env["blocks"]["gaps"]["verdict"] == "INDETERMINATE"
