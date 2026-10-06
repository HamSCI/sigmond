"""Set aside data stored while the site sink switch read `off`
(tasks/plan-sink-control.md §10.3 item 3)."""
import os
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from sigmond import sink_doors as sd

NOW = datetime(2026, 10, 7, 15, 30, tzinfo=timezone.utc)


def _grape(root: Path):
    spool = root / "timestd" / "upload"
    obs = spool / "20261005" / "AC0G_EM38ww" / "OBS2026-10-05T00-00"
    (obs / "ch0").mkdir(parents=True)
    (obs / "ch0" / "rf@1.h5").write_bytes(b"x")
    return spool, obs


def _mag(root: Path):
    spool = root / "mag-recorder" / "upload"
    spool.mkdir(parents=True)
    z = spool / "mag_20261005.zip"
    z.write_bytes(b"z")
    return spool, z


def _systemctl(states):
    """A stand-in for subprocess.run that answers `systemctl is-active`."""
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=states[cmd[-1]] + "\n", stderr="")
    return run


def test_packaging_running_counts_a_oneshot_that_is_activating():
    # grape-daily and mag-recorder-upload run Type=oneshot: while one works,
    # systemd reports it `activating`, never `active`.
    run = _systemctl({"grape-daily.service": "activating",
                      "mag-recorder-upload.service": "inactive"})
    assert sd.packaging_running(run=run) == ["grape-daily.service"]


def test_packaging_running_on_a_host_without_systemd_reports_nothing():
    def run(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    assert sd.packaging_running(run=run) == []


def test_packaging_running_counts_a_unit_systemd_does_not_answer_for():
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 10)
    assert sd.packaging_running(run=run) == list(sd.PACKAGING_UNITS)


def test_set_aside_moves_obs_dirs_outside_the_spool_root(tmp_path):
    spool, obs = _grape(tmp_path)
    moved = sd.set_aside(spool, "OBS*", dirs=True, stamp="S")
    held = (tmp_path / "timestd" / "upload-held" / "S" / "20261005" / "AC0G_EM38ww"
            / "OBS2026-10-05T00-00")
    assert moved == [(obs, held)]
    assert held.is_dir() and not obs.exists()
    # The file source searches the whole tree; nothing may stay under the root.
    assert not list(spool.rglob("OBS*"))


def test_set_aside_moves_zips(tmp_path):
    spool, z = _mag(tmp_path)
    moved = sd.set_aside(spool, "*.zip", dirs=False, stamp="S")
    assert moved == [(z, tmp_path / "mag-recorder" / "upload-held" / "S" / "mag_20261005.zip")]
    assert not list(spool.rglob("*.zip"))


def test_set_aside_takes_only_the_outermost_match(tmp_path):
    spool, obs = _grape(tmp_path)
    (obs / "OBS-inner").mkdir()
    moved = sd.set_aside(spool, "OBS*", dirs=True, stamp="S")
    assert [src for src, _ in moved] == [obs]


def test_set_aside_on_a_missing_spool_does_nothing(tmp_path):
    assert sd.set_aside(tmp_path / "absent", "*.zip", dirs=False, stamp="S") == []


def test_set_aside_refuses_across_filesystems(tmp_path):
    spool, z = _mag(tmp_path)
    real_stat = os.stat

    def fake_stat(p, *a, **k):
        st = real_stat(p, *a, **k)
        if Path(p) == spool:
            fields = list(tuple(st))
            fields[2] = st.st_dev + 1          # st_dev
            return os.stat_result(fields)
        return st

    with mock.patch("sigmond.sink_doors.os.stat", side_effect=fake_stat):
        with pytest.raises(sd.DoorError) as caught:
            sd.set_aside(spool, "*.zip", dirs=False, stamp="S")
    msg = str(caught.value)
    # The refusal names the spool and the next step (§10.3 item 3).
    assert f"Move {spool} onto the same filesystem as {spool.parent}" in msg
    assert "fleet admin" in msg and "stays off" in msg
    assert z.exists()                           # nothing moved


def test_mark_days_complete_writes_seven_markers(tmp_path):
    spool = tmp_path / "upload"
    spool.mkdir()
    marks = sd.mark_days_complete(spool, date(2026, 10, 7), chown=mock.Mock())
    assert [m.parent.name for m in marks] == [
        "20261006", "20261005", "20261004", "20261003", "20261002", "20261001", "20260930"]
    assert all(m.name == ".upload_complete" and m.is_file() for m in marks)


def test_new_day_dirs_and_markers_take_the_spool_owner(tmp_path):
    spool = tmp_path / "upload"
    spool.mkdir()
    (spool / "20261006").mkdir()               # GRAPE already made this one
    st = spool.stat()
    chown = mock.Mock()
    sd.mark_days_complete(spool, date(2026, 10, 7), days=2, chown=chown)
    owned = [c.args for c in chown.call_args_list]
    assert (spool / "20261005", st.st_uid, st.st_gid) in owned          # created here
    assert (spool / "20261006" / ".upload_complete", st.st_uid, st.st_gid) in owned
    assert (spool / "20261005" / ".upload_complete", st.st_uid, st.st_gid) in owned
    assert all(args[0] != spool / "20261006" for args in owned)      # existing dir untouched


def test_mark_days_complete_on_a_missing_spool_does_nothing(tmp_path):
    assert sd.mark_days_complete(tmp_path / "absent", date(2026, 10, 7)) == []


def test_close_doors_covers_both_spools_under_one_utc_stamp(tmp_path):
    g, obs = _grape(tmp_path)
    m, z = _mag(tmp_path)
    report = sd.close_doors(NOW, grape=g, mag=m, chown=mock.Mock())
    assert {src for src, _ in report.moved} == {obs, z}
    assert all("20261007T153000Z" in str(dst) for _, dst in report.moved)
    assert len(report.marked) == 7
