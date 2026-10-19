"""`smd status` draws meteor-scatter's inventory `msqp` block (release-v373 row 13)."""
import copy

from sigmond.msqp_view import msqp_lines

BLOCK = {
    "written_at": 1760000000.0,
    "archive_root": "/var/lib/meteor-scatter/msqp/x",
    "archive": {"bytes": 3 * 1024 ** 3, "oldest_day": "20261001", "kept_today": 7,
                "bands": {"6m": {"bytes": 1, "oldest_day": "20261001", "kept_today": 7}}},
    "frontend_last_packet_age_sec": 0.4,
    "channels": [
        {"band": "6m", "scale_db": -3.0, "archived": 7, "archive_failures": 0,
         "archiving": True, "noise_log_failures": 0, "noise_log": True,
         "noise": {"calibrated_dbfs": -100.0, "last_dbfs": -98.5, "alarmed": False,
                   "since": None}},
    ],
}


def text(lines):
    return "\n".join(t for _, t in lines)


def test_healthy_block_shows_every_requested_fact():
    lines = msqp_lines({"msqp": BLOCK})
    t = text(lines)
    assert "3.0 GB" in t and "2026-10-01" in t and "7 recordings kept today" in t
    assert "6m" in t and "-98.5 dBFS" in t and "-100.0 dBFS" in t and "+1.5 dB" in t
    assert "front end: ok" in t
    assert all(level == "info" for level, _ in lines)


def test_null_block_says_the_daemon_has_not_refreshed():
    lines = msqp_lines({"msqp": None})
    assert len(lines) == 1 and lines[0][0] == "warn"
    assert "5 minutes" in lines[0][1]


def test_old_meteor_scatter_without_the_key_prints_nothing():
    assert msqp_lines({"instance": "x"}) == []


def test_alarmed_band_and_failed_archive_are_warnings():
    b = copy.deepcopy(BLOCK)
    b["channels"][0]["noise"]["alarmed"] = True
    b["channels"][0]["archiving"] = False
    b["channels"][0]["archive_failures"] = 3
    warns = [t for lvl, t in msqp_lines({"msqp": b}) if lvl == "warn"]
    assert any("outside its calibration" in w for w in warns)
    assert any("archiving has failed (3" in w for w in warns)


def test_uncalibrated_band_and_missing_frontend():
    b = copy.deepcopy(BLOCK)
    b["channels"][0]["noise"].update(calibrated_dbfs=None)
    b["frontend_last_packet_age_sec"] = None
    lines = msqp_lines({"msqp": b})
    assert "not calibrated" in text(lines)
    assert ("warn", "msqp front end: no status packet seen") in lines


def test_stale_frontend_warns():
    b = copy.deepcopy(BLOCK)
    b["frontend_last_packet_age_sec"] = 300
    assert any(l == "warn" and "300 s ago" in t for l, t in msqp_lines({"msqp": b}))


def test_empty_archive():
    b = copy.deepcopy(BLOCK)
    b["archive"] = {"bytes": 0, "oldest_day": None, "kept_today": 0, "bands": {}}
    assert "oldest day none" in text(msqp_lines({"msqp": b}))
