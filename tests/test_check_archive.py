"""smd check: platform.archive (design 2026-11-12, part 1 'The baseline and later readings')."""
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from sigmond import archive_health as H
from sigmond.commands import check as C


def _r(**kw):
    base = dict(passed=True, disk_serial="Z", reallocated=0, pending=0, offline_uncorrectable=0,
                temperature_c=30)
    base.update(kw)
    return asdict(H.Reading(**base))


@pytest.fixture
def files(tmp_path, monkeypatch):
    t = tmp_path / "archive.toml"
    h = tmp_path / "health.json"
    s = tmp_path / "status.json"
    d = tmp_path / "prune.d"
    d.mkdir()
    monkeypatch.setattr(C, "ARCHIVE_TOML", t)
    monkeypatch.setattr(C, "ARCHIVE_HEALTH", h)
    monkeypatch.setattr(C, "ARCHIVER_STATUS", s)
    monkeypatch.setattr(C, "PRUNE_UNIT_FILES", lambda: sorted(d.glob("*")))
    state = {"m": "mounted"}
    monkeypatch.setattr(C, "_archive_mount_state", lambda uuid: state["m"])
    t.write_text('uuid = "u"\nmount = "/var/lib/sigmond/archive"\n')
    h.write_text(json.dumps({"baseline": _r(), "latest": _r()}))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s.write_text(json.dumps({"run_utc": now, "state": "ok", "behind": 0, "failures": 0}))
    return type("F", (), {"toml": t, "health": h, "status": s, "dropins": d, "state": state})


def test_no_archive_configured(files):
    files.toml.unlink()
    assert C.archive_line(None) is None


def test_mounted_healthy_current_is_ok(files):
    ln = C.archive_line(None)
    assert ln.mark == C.OK and ln.label == "archive"


def test_present_not_mounted_warns_with_fix(files):
    files.state["m"] = "present"
    ln = C.archive_line(None)
    assert ln.mark == C.WAIT
    assert "run: sudo mount /var/lib/sigmond/archive" in ln.text


def test_absent_drive_warns(files):
    files.state["m"] = "absent"
    assert C.archive_line(None).mark == C.WAIT


def test_grown_remapped_warns(files):
    files.health.write_text(json.dumps({"baseline": _r(), "latest": _r(reallocated=5)}))
    ln = C.archive_line(None)
    assert ln.mark == C.WAIT and "remapped" in ln.text


def test_pending_fails(files):
    files.health.write_text(json.dumps({"baseline": _r(), "latest": _r(pending=2)}))
    assert C.archive_line(None).mark == C.FAIL


def test_stale_archiver_warns(files):
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    files.status.write_text(json.dumps({"run_utc": old, "state": "ok", "behind": 0, "failures": 0}))
    assert C.archive_line(None).mark == C.WAIT


def test_full_warns(files):
    st = json.loads(files.status.read_text())
    st["state"] = "full"
    files.status.write_text(json.dumps(st))
    ln = C.archive_line(None)
    assert ln.mark == C.WAIT and "full" in ln.text


def test_old_archive_root_dropin_warns(files):
    (files.dropins / "override.conf").write_text("ExecStart=x --archive-root /mnt/timestd-archive\n")
    ln = C.archive_line(None)
    assert ln.mark == C.WAIT and "--archive-root" in ln.text


def test_text_is_ascii(files):
    files.health.write_text(json.dumps({"baseline": _r(), "latest": _r(temperature_c=60)}))
    assert C.archive_line(None).text.isascii()


def test_registered():
    assert ("archive", C.archive_line) in C.CHECKS
    assert C.CHECK_META["archive"][0] == "platform.archive"
