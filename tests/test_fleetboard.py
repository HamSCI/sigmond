"""Tests for the wd30 fleetboard — server/heartbeat/{ingest,fleetboard}.py.

The server side exists to answer one question the stations cannot answer
for themselves: *is this station still here?*  Availability is derived
from heartbeat ARRIVAL times against a canonical roster, never from
anything a station said about itself — a host that has stopped cannot
report that it stopped.  The executable form of that rule is
``test_absence_overrides_self_report``; if only one test in this file
survives, it should be that one.

The server directory is NOT a package: it is rsynced flat onto wd30 next
to a verbatim copy of ``lib/sigmond/heartbeat_schema.py`` and imports it
as a sibling (``import heartbeat_schema``, resolved via ``sys.path[0]``).
These tests reproduce that flat layout by putting both directories on
``sys.path`` — importing the server modules through ``sigmond.*`` would
test a layout that does not exist on the server.
"""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "server" / "heartbeat"

if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

# On wd30 heartbeat_schema.py IS a file in the server dir (deploy-wd30.sh
# rsyncs it there).  In the repo it still lives in lib/sigmond/, so bind it
# under its flat name rather than putting lib/sigmond/ on sys.path — that
# would make every sigmond module importable as a top-level name for the
# rest of the session, which is neither the server's layout nor ours.
_schema_spec = importlib.util.spec_from_file_location(
    "heartbeat_schema", REPO / "lib" / "sigmond" / "heartbeat_schema.py")
heartbeat_schema = importlib.util.module_from_spec(_schema_spec)
_schema_spec.loader.exec_module(heartbeat_schema)
sys.modules["heartbeat_schema"] = heartbeat_schema

import fleetboard  # noqa: E402  (after the sys.path bootstrap)
import ingest  # noqa: E402
import roster_check  # noqa: E402


SCRIPTS = ("setup-wd30.sh", "deploy-wd30.sh", "authorize-stations.sh")


# ---------------------------------------------------------------------------
# fixtures / builders
# ---------------------------------------------------------------------------

def make_envelope(station="AC0G-B4", rollup="VALID",
                  reason="versions: 4 component versions read",
                  emitted_at="2026-08-20T14:05:06Z", interval_sec=300,
                  blocks=None):
    """A structurally valid envelope, shaped exactly like the producer's."""
    if blocks is None:
        blocks = {
            name: {"verdict": "VALID", "reason": f"{name} ok",
                   "data": {"probe": name}}
            for name in heartbeat_schema.BLOCK_NAMES
        }
    env = {
        "kind": heartbeat_schema.KIND,
        "schema_version": heartbeat_schema.SCHEMA_VERSION,
        "station": station,
        "callsign": "AC0G",
        "grid": "EM38ww",
        "emitted_at": emitted_at,
        "interval_sec": interval_sec,
        "uptime_s": 98765.4,
        "rollup": {"verdict": rollup, "reason": reason},
        "blocks": blocks,
    }
    assert heartbeat_schema.validate(env) == [], "builder must emit valid JSON"
    return env


def drop(drop_dir, name, obj, mtime=None):
    """Write a file into the drop as the SFTP rename would leave it."""
    path = Path(drop_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    text = obj if isinstance(obj, str) else json.dumps(obj)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture()
def drop_dir(tmp_path):
    d = tmp_path / "srv" / "incoming"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "var" / "heartbeats.db"


def rows(db, table="heartbeats"):
    conn = ingest.open_db(str(db))
    try:
        cur = conn.execute(f"SELECT * FROM {table} ORDER BY id")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def seed(db, station, received_at, envelope=None):
    """Put one arrival in the DB without going through the drop dir."""
    env = envelope if envelope is not None else make_envelope(station=station)
    conn = ingest.open_db(str(db))
    try:
        ingest.record_heartbeat(conn, station, received_at, env)
        conn.commit()
    finally:
        conn.close()


ROSTER = [
    {"name": "AC0G-B4", "profile": "dasi2", "role": "reference",
     "frozen": None, "canary": True},
    {"name": "DASI002", "profile": "dasi2", "role": "station",
     "frozen": "awaiting parts", "canary": False},
]


# ---------------------------------------------------------------------------
# 1. ingest — the drop dir
# ---------------------------------------------------------------------------

def test_valid_drop_is_ingested_and_unlinked(drop_dir, db_path):
    env = make_envelope()
    path = drop(drop_dir, "AC0G-B4_20260820T140506Z.json", env, mtime=1000.0)

    result = ingest.ingest_once(str(drop_dir), str(db_path))

    assert result["ingested"] == 1
    assert result["errors"] == 0
    assert not path.exists(), "an ingested file must not be left in the drop"
    (row,) = rows(db_path)
    assert row["station"] == "AC0G-B4"
    assert row["received_at"] == pytest.approx(1000.0)
    assert row["emitted_at"] == "2026-08-20T14:05:06Z"
    assert row["schema_version"] == 1
    assert row["rollup_verdict"] == "VALID"
    assert json.loads(row["payload"]) == env


def test_received_at_is_file_mtime_never_the_self_reported_emitted_at(
        drop_dir, db_path):
    # The whole availability story rests on this: the server clock at
    # rename time, not a timestamp the station chose.
    env = make_envelope(emitted_at="1999-01-01T00:00:00Z")
    drop(drop_dir, "AC0G-B4_20260820T140506Z.json", env, mtime=1787236000.0)

    ingest.ingest_once(str(drop_dir), str(db_path))

    (row,) = rows(db_path)
    assert row["received_at"] == pytest.approx(1787236000.0)
    assert row["emitted_at"] == "1999-01-01T00:00:00Z"


def test_malformed_json_is_quarantined_with_a_reject_row(drop_dir, db_path):
    path = drop(drop_dir, "AC0G-B4_20260820T140506Z.json", "{not json",
                mtime=1234.0)
    quarantine = drop_dir.parent / "quarantine"

    result = ingest.ingest_once(str(drop_dir), str(db_path))

    assert result["quarantined"] == 1
    assert result["ingested"] == 0
    assert not path.exists()
    assert (quarantine / path.name).exists()
    assert rows(db_path) == []
    (reject,) = rows(db_path, "rejects")
    assert reject["station_guess"] == "AC0G-B4"
    assert reject["filename"] == "AC0G-B4_20260820T140506Z.json"
    assert reject["received_at"] == pytest.approx(1234.0)
    assert "json" in reject["reason"].lower()


def test_schema_invalid_envelope_is_quarantined_not_ingested(
        drop_dir, db_path):
    env = make_envelope()
    env["kind"] = "not_a_heartbeat"          # fails validate(), parses fine
    path = drop(drop_dir, "DASI002_20260820T140506Z.json", env)

    result = ingest.ingest_once(str(drop_dir), str(db_path))

    assert result["quarantined"] == 1
    assert rows(db_path) == []
    assert (drop_dir.parent / "quarantine" / path.name).exists()
    (reject,) = rows(db_path, "rejects")
    assert reject["station_guess"] == "DASI002"
    assert "kind" in reject["reason"]


def test_quarantine_dir_is_created_when_absent(drop_dir, db_path):
    quarantine = drop_dir.parent / "quarantine"
    assert not quarantine.exists()
    drop(drop_dir, "x_20260820T140506Z.json", "{{{")

    ingest.ingest_once(str(drop_dir), str(db_path))

    assert quarantine.is_dir()


def test_partial_uploads_and_dotfiles_are_never_touched(drop_dir, db_path):
    env = make_envelope()
    part = drop(drop_dir, "AC0G-B4_20260820T140506Z.json.part", env)
    dot = drop(drop_dir, ".hidden.json", env)
    other = drop(drop_dir, "notes.txt", "hello")

    result = ingest.ingest_once(str(drop_dir), str(db_path))

    assert result["ingested"] == 0
    assert result["quarantined"] == 0
    assert part.exists() and dot.exists() and other.exists()
    assert rows(db_path) == []


@pytest.mark.parametrize("filename,expected", [
    ("AC0G-B4_20260820T140506Z.json", "AC0G-B4"),
    ("dasi-002-x_20260820T140506Z.json", "dasi-002-x"),
    ("noseparator.json", "unknown"),
    ("_20260820T140506Z.json", "unknown"),
    ("", "unknown"),
])
def test_station_guess_from_filename(filename, expected):
    assert ingest.station_guess(filename) == expected


def test_a_file_that_vanishes_mid_scan_is_skipped_silently(
        drop_dir, db_path, monkeypatch):
    # Concurrent sftp: .part-then-rename means we can lose the race but
    # never see a half file.  Losing the race is not an error.
    drop(drop_dir, "AC0G-B4_20260820T140506Z.json", make_envelope())
    real = ingest.read_bytes

    def vanish(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(ingest, "read_bytes", vanish)
    result = ingest.ingest_once(str(drop_dir), str(db_path))
    monkeypatch.setattr(ingest, "read_bytes", real)

    assert result["skipped"] == 1
    assert result["errors"] == 0
    assert result["ingested"] == 0


def test_unexpected_read_error_is_counted_and_exits_nonzero(
        drop_dir, db_path, monkeypatch):
    drop(drop_dir, "AC0G-B4_20260820T140506Z.json", make_envelope())

    def boom(path):
        raise PermissionError(path)

    monkeypatch.setattr(ingest, "read_bytes", boom)
    assert ingest.main(["--drop-dir", str(drop_dir), "--db", str(db_path)]) == 1


def test_clean_pass_exits_zero_and_is_idempotent(drop_dir, db_path):
    drop(drop_dir, "AC0G-B4_20260820T140506Z.json", make_envelope())

    argv = ["--drop-dir", str(drop_dir), "--db", str(db_path)]
    assert ingest.main(argv) == 0
    assert len(rows(db_path)) == 1
    # Second pass: nothing left to do, still clean, no duplicate row.
    assert ingest.main(argv) == 0
    assert len(rows(db_path)) == 1


def test_empty_drop_dir_and_missing_drop_dir_are_clean(tmp_path, db_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert ingest.main(["--drop-dir", str(empty), "--db", str(db_path)]) == 0
    missing = tmp_path / "nope"
    assert ingest.main(["--drop-dir", str(missing), "--db", str(db_path)]) == 0


def test_quarantine_collision_does_not_clobber_the_earlier_evidence(
        drop_dir, db_path):
    quarantine = drop_dir.parent / "quarantine"
    drop(drop_dir, "AC0G-B4_20260820T140506Z.json", "{bad one")
    ingest.ingest_once(str(drop_dir), str(db_path))
    drop(drop_dir, "AC0G-B4_20260820T140506Z.json", "{bad two")
    ingest.ingest_once(str(drop_dir), str(db_path))

    kept = sorted(p.name for p in quarantine.iterdir())
    assert len(kept) == 2, kept
    assert len(rows(db_path, "rejects")) == 2


def test_index_exists_on_station_and_received_at(db_path):
    conn = ingest.open_db(str(db_path))
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")]
    finally:
        conn.close()
    assert any("station" in n for n in names), names


# ---------------------------------------------------------------------------
# 2. derive_status — absence detection
# ---------------------------------------------------------------------------

NOW = 1_787_236_000.0


def test_fresh_arrivals_are_available_and_show_the_envelope_rollup(db_path):
    seed(db_path, "AC0G-B4", NOW - 60,
         make_envelope(rollup="INCONCLUSIVE", reason="uploads: backlog 3"))
    seed(db_path, "DASI002", NOW - 30)

    conn = ingest.open_db(str(db_path))
    try:
        statuses = fleetboard.derive_status(conn, ROSTER, NOW)
    finally:
        conn.close()

    by_name = {s["station"]: s for s in statuses}
    b4 = by_name["AC0G-B4"]
    assert b4["availability"]["verdict"] == "VALID"
    # The envelope's own rollup is what the top verdict shows while the
    # station is demonstrably present.
    assert b4["top"]["verdict"] == "INCONCLUSIVE"
    assert b4["top"]["reason"] == "uploads: backlog 3"
    assert b4["last_received_at"] == pytest.approx(NOW - 60)
    assert b4["silent_for_s"] == pytest.approx(60)
    assert b4["canary"] is True
    assert by_name["DASI002"]["frozen"] == "awaiting parts"


def test_absence_overrides_self_report(db_path):
    """THE test: a station that stopped reporting goes red regardless of
    what its last heartbeat claimed about itself."""
    # Last word from this station was a clean bill of health...
    seed(db_path, "AC0G-B4", NOW - 3600,
         make_envelope(rollup="VALID", reason="everything is fine"))
    seed(db_path, "DASI002", NOW - 30)

    conn = ingest.open_db(str(db_path))
    try:
        statuses = fleetboard.derive_status(conn, ROSTER, NOW)
    finally:
        conn.close()

    b4 = {s["station"]: s for s in statuses}["AC0G-B4"]
    assert b4["availability"]["verdict"] == "INVALID"
    assert b4["availability"]["reason"] == "silent 60m"
    # ...and it must NOT be what the board shows.
    assert b4["top"]["verdict"] == "INVALID"
    assert b4["top"]["reason"] == "silent 60m"
    assert "fine" not in b4["top"]["reason"]
    # The healthy station beside it is unaffected.
    assert {s["station"]: s for s in statuses}["DASI002"]["top"][
        "verdict"] == "VALID"


def test_never_heard_roster_station_is_indeterminate(db_path):
    seed(db_path, "AC0G-B4", NOW - 30)

    conn = ingest.open_db(str(db_path))
    try:
        statuses = fleetboard.derive_status(conn, ROSTER, NOW)
    finally:
        conn.close()

    d2 = {s["station"]: s for s in statuses}["DASI002"]
    assert d2["availability"]["verdict"] == "INDETERMINATE"
    assert d2["availability"]["reason"] == "never heard"
    assert d2["top"]["verdict"] == "INDETERMINATE"
    assert d2["last_received_at"] is None
    assert d2["silent_for_s"] is None
    # Every block reads "could not measure" — never a blank that could be
    # mistaken for health.
    assert set(d2["blocks"]) == set(heartbeat_schema.BLOCK_NAMES)
    assert all(b["verdict"] == "INDETERMINATE" for b in d2["blocks"].values())


def test_interval_from_the_envelope_beats_the_default(db_path):
    # 200 s of silence: fine at the 300 s default (3x = 900 s), late at
    # the envelope's declared 60 s cadence (3x = 180 s).
    seed(db_path, "AC0G-B4", NOW - 200, make_envelope(interval_sec=60))

    conn = ingest.open_db(str(db_path))
    try:
        statuses = fleetboard.derive_status(conn, ROSTER, NOW)
    finally:
        conn.close()

    b4 = {s["station"]: s for s in statuses}["AC0G-B4"]
    assert b4["availability"]["verdict"] == "INVALID"


def test_default_interval_used_when_the_envelope_has_none(db_path):
    env = make_envelope()
    env.pop("interval_sec")
    seed(db_path, "AC0G-B4", NOW - 200, env)

    conn = ingest.open_db(str(db_path))
    try:
        statuses = fleetboard.derive_status(conn, ROSTER, NOW)
    finally:
        conn.close()

    b4 = {s["station"]: s for s in statuses}["AC0G-B4"]
    assert b4["availability"]["verdict"] == "VALID"


def test_only_the_latest_arrival_decides(db_path):
    seed(db_path, "AC0G-B4", NOW - 9000, make_envelope(rollup="INVALID",
                                                       reason="old news"))
    seed(db_path, "AC0G-B4", NOW - 10, make_envelope(rollup="VALID",
                                                     reason="fresh news"))

    conn = ingest.open_db(str(db_path))
    try:
        statuses = fleetboard.derive_status(conn, ROSTER, NOW)
    finally:
        conn.close()

    b4 = {s["station"]: s for s in statuses}["AC0G-B4"]
    assert b4["top"]["reason"] == "fresh news"


def test_blocks_without_a_data_key_do_not_break_the_board(db_path):
    blocks = {n: {"verdict": "VALID", "reason": f"{n} ok"}
              for n in heartbeat_schema.BLOCK_NAMES}
    blocks["timing"] = {"verdict": "INDETERMINATE",
                        "reason": "authority.json stale (94s old)"}
    seed(db_path, "AC0G-B4", NOW - 10, make_envelope(blocks=blocks))

    conn = ingest.open_db(str(db_path))
    try:
        statuses = fleetboard.derive_status(conn, ROSTER, NOW)
    finally:
        conn.close()

    b4 = {s["station"]: s for s in statuses}["AC0G-B4"]
    assert b4["blocks"]["timing"] == {
        "verdict": "INDETERMINATE", "reason": "authority.json stale (94s old)"}


def test_unreadable_payload_is_indeterminate_not_healthy(db_path):
    conn = ingest.open_db(str(db_path))
    try:
        conn.execute(
            "INSERT INTO heartbeats(station, received_at, emitted_at,"
            " schema_version, rollup_verdict, payload) VALUES (?,?,?,?,?,?)",
            ("AC0G-B4", NOW - 10, "2026-08-20T14:05:06Z", 1, "VALID",
             "{ truncated"))
        conn.commit()
        statuses = fleetboard.derive_status(conn, ROSTER, NOW)
    finally:
        conn.close()

    b4 = {s["station"]: s for s in statuses}["AC0G-B4"]
    assert b4["availability"]["verdict"] == "VALID"      # it did arrive
    assert b4["top"]["verdict"] == "INDETERMINATE"       # but says nothing


def test_status_rows_follow_roster_order(db_path):
    conn = ingest.open_db(str(db_path))
    try:
        statuses = fleetboard.derive_status(conn, ROSTER, NOW)
    finally:
        conn.close()
    assert [s["station"] for s in statuses] == [r["name"] for r in ROSTER]


# ---------------------------------------------------------------------------
# 3. unexpected stations + rejects
# ---------------------------------------------------------------------------

def test_unexpected_station_is_segregated_never_merged(db_path):
    seed(db_path, "AC0G-B4", NOW - 30)
    seed(db_path, "MYSTERY-1", NOW - 40)
    seed(db_path, "MYSTERY-1", NOW - 20)

    conn = ingest.open_db(str(db_path))
    try:
        statuses = fleetboard.derive_status(conn, ROSTER, NOW)
        unexpected = fleetboard.unexpected_stations(conn, ROSTER, NOW)
    finally:
        conn.close()

    assert "MYSTERY-1" not in [s["station"] for s in statuses]
    assert unexpected == [{"station": "MYSTERY-1", "count": 2,
                           "last_seen": pytest.approx(NOW - 20)}]


def test_unexpected_window_excludes_stale_strangers(db_path):
    seed(db_path, "MYSTERY-1", NOW - 90000)

    conn = ingest.open_db(str(db_path))
    try:
        assert fleetboard.unexpected_stations(conn, ROSTER, NOW) == []
        assert len(fleetboard.unexpected_stations(
            conn, ROSTER, NOW, window_s=200000)) == 1
    finally:
        conn.close()


def test_rejects_summary_counts_the_window(db_path, drop_dir):
    drop(drop_dir, "AC0G-B4_20260820T140506Z.json", "{bad", mtime=NOW - 100)
    drop(drop_dir, "GHOST_20260820T140506Z.json", "{bad", mtime=NOW - 200000)
    ingest.ingest_once(str(drop_dir), str(db_path))

    conn = ingest.open_db(str(db_path))
    try:
        summary = fleetboard.rejects_summary(conn, NOW)
    finally:
        conn.close()

    assert summary["count"] == 1
    assert summary["window_s"] == 86400
    assert summary["recent"][0]["station_guess"] == "AC0G-B4"


# ---------------------------------------------------------------------------
# 4. renderer
# ---------------------------------------------------------------------------

def crafted_statuses():
    """One station per verdict state, so every CSS class must appear."""
    out = []
    for i, (verdict, reason) in enumerate([
            ("VALID", "all good"),
            ("INVALID", "silent 60m"),
            ("INCONCLUSIVE", "uploads: backlog 3"),
            ("INDETERMINATE", "never heard")]):
        out.append({
            "station": f"STN-{i}",
            "profile": "dasi2",
            "role": "station",
            "availability": {"verdict": verdict, "reason": reason},
            "top": {"verdict": verdict, "reason": reason},
            "last_received_at": None if verdict == "INDETERMINATE" else 1.0,
            "silent_for_s": None if verdict == "INDETERMINATE" else 42.0,
            "emitted_at": "2026-08-20T14:05:06Z",
            "blocks": {n: {"verdict": verdict, "reason": f"{n}: {reason}"}
                       for n in heartbeat_schema.BLOCK_NAMES},
            "frozen": None,
            "canary": i == 0,
        })
    return out


def test_render_html_shows_every_verdict_class_and_needs_no_js():
    html = fleetboard.render_html(
        crafted_statuses(),
        [{"station": "MYSTERY-1", "count": 3, "last_seen": 1.0}],
        {"count": 2, "window_s": 86400, "recent": []},
        1_787_236_000.0)

    for verdict in heartbeat_schema.VERDICTS:
        assert f"v-{verdict}" in html, verdict
    assert "<script" not in html.lower()
    assert 'http-equiv="refresh"' in html
    assert 'content="60"' in html
    for name in heartbeat_schema.BLOCK_NAMES:
        assert name in html
    assert "MYSTERY-1" in html
    assert "unexpected" in html.lower()
    assert "2" in html                              # the rejects count
    assert "2026-08-20" in html                     # generated-at footer


def test_render_html_escapes_station_names():
    statuses = crafted_statuses()
    statuses[0]["station"] = "<script>alert(1)</script>"
    html = fleetboard.render_html(statuses, [], {"count": 0,
                                                 "window_s": 86400,
                                                 "recent": []}, 1.0)
    assert "<script" not in html.lower()
    assert "&lt;script&gt;" in html


def test_block_cells_are_not_colour_only():
    # Colour alone excludes anyone reading this on a bad screen or with a
    # colour-vision deficiency, and a red/grey confusion here is the
    # difference between "measured bad" and "not measured at all".
    html = fleetboard.render_html(
        crafted_statuses(), [], {"count": 0, "window_s": 86400, "recent": []},
        1_787_236_000.0)
    for label in fleetboard.VERDICT_LABEL.values():
        assert label in html, label


def test_render_html_survives_an_empty_fleet():
    html = fleetboard.render_html([], [], {"count": 0, "window_s": 86400,
                                           "recent": []}, 1.0)
    assert "<html" in html.lower()


# ---------------------------------------------------------------------------
# 5. CLI / page assembly
# ---------------------------------------------------------------------------

def write_roster(tmp_path, roster=None):
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(ROSTER if roster is None else roster))
    return path


def test_once_writes_the_page_and_exits(tmp_path, db_path):
    seed(db_path, "AC0G-B4", time.time() - 10)
    out = tmp_path / "board.html"

    rc = fleetboard.main(["--once", str(out), "--db", str(db_path),
                          "--roster", str(write_roster(tmp_path))])

    assert rc == 0
    html = out.read_text()
    assert "AC0G-B4" in html and "DASI002" in html


def test_serving_without_an_explicit_bind_address_is_refused(tmp_path,
                                                             db_path):
    # No default of 0.0.0.0: the operator names the LAN/WireGuard address.
    with pytest.raises(SystemExit) as exc:
        fleetboard.main(["--db", str(db_path),
                         "--roster", str(write_roster(tmp_path))])
    assert exc.value.code != 0


def test_load_roster_rejects_a_non_roster_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"name": "AC0G-B4"}')
    with pytest.raises(ValueError):
        fleetboard.load_roster(str(bad))


def test_load_roster_rejects_an_empty_roster(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("[]")
    with pytest.raises(ValueError):
        fleetboard.load_roster(str(empty))


# ---------------------------------------------------------------------------
# 6. roster_check helper + shell scripts
# ---------------------------------------------------------------------------

def test_roster_check_refuses_an_empty_roster(tmp_path, capsys):
    path = tmp_path / "roster.json"
    path.write_text("[]")
    assert roster_check.main(["--check", str(path)]) == 1


def test_roster_check_refuses_a_non_list(tmp_path):
    path = tmp_path / "roster.json"
    path.write_text('{"name": "x"}')
    assert roster_check.main(["--check", str(path)]) == 1


def test_roster_check_refuses_unparseable_json(tmp_path):
    path = tmp_path / "roster.json"
    path.write_text("{{{")
    assert roster_check.main(["--check", str(path)]) == 1


def test_roster_check_prints_names(tmp_path, capsys):
    path = write_roster(tmp_path)
    assert roster_check.main(["--names", str(path)]) == 0
    assert capsys.readouterr().out.split() == ["AC0G-B4", "DASI002"]


def test_roster_check_refuses_a_nameless_entry(tmp_path):
    path = write_roster(tmp_path, [{"profile": "dasi2"}])
    assert roster_check.main(["--check", str(path)]) == 1


@pytest.mark.parametrize("script", SCRIPTS)
def test_shell_scripts_parse(script):
    path = SERVER / script
    assert path.exists(), path
    assert os.access(path, os.X_OK), f"{script} must be executable"
    subprocess.run(["bash", "-n", str(path)], check=True)


@pytest.mark.parametrize("unit", [
    "hamsci-hb-ingest.service", "hamsci-hb-ingest.timer",
    "hamsci-fleetboard.service"])
def test_units_exist_and_run_as_wsprdaemon(unit):
    text = (SERVER / "units" / unit).read_text()
    if unit.endswith(".service"):
        assert "User=wsprdaemon" in text
        assert "/opt/hamsci-fleetboard/" in text


def test_setup_script_never_defaults_the_board_to_all_interfaces():
    text = (SERVER / "units" / "hamsci-fleetboard.service").read_text()
    assert "--bind" in text
    assert "0.0.0.0" not in text


def test_sshd_snippet_keeps_authorized_keys_outside_the_chroot():
    text = (SERVER / "setup-wd30.sh").read_text()
    # Keys live outside /srv/hamsci-hb so the chrooted account can never
    # edit the list of keys that confines it.
    assert "/etc/ssh/authorized_keys.d" in text
    assert "AuthorizedKeysFile $KEYS_FILE" in text
    assert "ChrootDirectory $DROP_ROOT" in text
    assert "/srv/hamsci-hb" in text
    assert "ForceCommand internal-sftp" in text
    assert "sshd -t" in text          # validate before any reload


def test_http_handler_serves_the_board_and_404s_elsewhere(tmp_path, db_path):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    seed(db_path, "AC0G-B4", time.time() - 10)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), fleetboard._BoardHandler)
    httpd.db_path = str(db_path)
    httpd.roster_loader = lambda: ROSTER
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with urllib.request.urlopen(base + "/", timeout=5) as resp:
            body = resp.read().decode()
        assert resp.status == 200
        assert "AC0G-B4" in body
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(base + "/etc/passwd", timeout=5)
        assert exc.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
