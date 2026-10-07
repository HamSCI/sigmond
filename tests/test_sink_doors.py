"""Set aside data stored while the site sink switch read `off`
(tasks/plan-sink-control.md §10.3 item 3)."""
import os
import stat
import subprocess
from datetime import date, datetime, timedelta, timezone
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
    moved = sd.set_aside(spool, "OBS*", match_dirs=True, stamp="S")
    held = (tmp_path / "timestd" / "upload-held" / "S" / "20261005" / "AC0G_EM38ww"
            / "OBS2026-10-05T00-00")
    assert moved == [(obs, held)]
    assert held.is_dir() and not obs.exists()
    # The file source searches the whole tree; nothing may stay under the root.
    assert not list(spool.rglob("OBS*"))


def test_set_aside_moves_zips(tmp_path):
    spool, z = _mag(tmp_path)
    moved = sd.set_aside(spool, "*.zip", match_dirs=False, stamp="S")
    assert moved == [(z, tmp_path / "mag-recorder" / "upload-held" / "S" / "mag_20261005.zip")]
    assert not list(spool.rglob("*.zip"))


def test_set_aside_takes_only_the_outermost_match(tmp_path):
    spool, obs = _grape(tmp_path)
    (obs / "OBS-inner").mkdir()
    moved = sd.set_aside(spool, "OBS*", match_dirs=True, stamp="S")
    assert [src for src, _ in moved] == [obs]


def test_set_aside_on_a_missing_spool_does_nothing(tmp_path):
    assert sd.set_aside(tmp_path / "absent", "*.zip", match_dirs=False, stamp="S") == []


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
            sd.set_aside(spool, "*.zip", match_dirs=False, stamp="S")
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


# ---- fix round 1: refuse before any rename, keep what moved, fail closed ----

def _other_filesystem(spool: Path):
    """A stand-in for os.stat that reports `spool` on another device."""
    real_stat = os.stat

    def fake_stat(p, *a, **k):
        st = real_stat(p, *a, **k)
        if Path(p) == spool:
            fields = list(tuple(st))
            fields[2] = st.st_dev + 1          # st_dev
            return os.stat_result(fields)
        return st

    return fake_stat


def test_close_doors_refuses_before_it_moves_anything(tmp_path):
    g, obs = _grape(tmp_path)
    m, z = _mag(tmp_path)
    with mock.patch("sigmond.sink_doors.os.stat", side_effect=_other_filesystem(m)):
        with pytest.raises(sd.DoorError) as caught:
            sd.close_doors(NOW, grape=g, mag=m, chown=mock.Mock())
    assert "fleet admin" in str(caught.value) and caught.value.moved == []
    assert obs.is_dir() and z.exists()          # GRAPE stayed put; mag stayed put
    assert not (tmp_path / "timestd" / "upload-held").exists()
    assert not list(g.glob("*/.upload_complete"))   # and nothing got marked


def test_close_doors_skips_the_filesystem_check_for_an_absent_spool(tmp_path):
    g, obs = _grape(tmp_path)
    report = sd.close_doors(NOW, grape=g, mag=tmp_path / "absent", chown=mock.Mock())
    assert [src for src, _ in report.moved] == [obs]


def _two_zips(root: Path):
    spool = root / "mag-recorder" / "upload"
    spool.mkdir(parents=True)
    zs = [spool / "mag_20261004.zip", spool / "mag_20261005.zip"]
    for z in zs:
        z.write_bytes(b"z")
    return spool, zs


def _second_rename_fails():
    real_rename = os.rename
    calls = {"n": 0}

    def rename(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(5, "Input/output error")
        return real_rename(src, dst)

    return rename


def test_set_aside_failing_midway_reports_what_it_moved(tmp_path):
    spool, (first, second) = _two_zips(tmp_path)
    with mock.patch("sigmond.sink_doors.os.rename", side_effect=_second_rename_fails()):
        with pytest.raises(sd.DoorError) as caught:
            sd.set_aside(spool, "*.zip", match_dirs=False, stamp="S")
    held = tmp_path / "mag-recorder" / "upload-held" / "S"
    assert caught.value.moved == [(first, held / first.name)]
    assert (held / first.name).exists() and second.exists()
    assert "stays off" in str(caught.value)


def test_set_aside_skips_a_zip_the_daemon_deleted_after_the_scan(tmp_path):
    # In discard, mag-recorder's delete_on_ack can remove a zip between the
    # rglob and the rename.  That zip needs no setting aside; the rest move.
    spool, (first, second) = _two_zips(tmp_path)
    real_rename = os.rename

    def rename(src, dst):
        if Path(src) == first:
            os.unlink(src)                      # the daemon got there first
        return real_rename(src, dst)

    with mock.patch("sigmond.sink_doors.os.rename", side_effect=rename):
        moved = sd.set_aside(spool, "*.zip", match_dirs=False, stamp="S")
    held = tmp_path / "mag-recorder" / "upload-held" / "S"
    assert moved == [(second, held / second.name)]
    assert not first.exists() and not (held / first.name).exists()


def test_set_aside_still_refuses_a_missing_destination(tmp_path):
    # FileNotFoundError while the source still exists names a fault in the
    # held directory, not a vanished package: refuse as before.
    spool, (first, second) = _two_zips(tmp_path)
    with mock.patch("sigmond.sink_doors.os.rename",
                    side_effect=FileNotFoundError(2, "No such file or directory")):
        with pytest.raises(sd.DoorError) as caught:
            sd.set_aside(spool, "*.zip", match_dirs=False, stamp="S")
    assert caught.value.moved == []
    assert first.exists() and second.exists()
    assert "stays off" in str(caught.value)


def test_close_doors_failing_in_mag_carries_the_grape_pairs_too(tmp_path):
    g, obs = _grape(tmp_path)
    m, (first, second) = _two_zips(tmp_path)
    real_rename = os.rename
    calls = {"n": 0}

    def rename(src, dst):                       # 1 = GRAPE OBS, 2 = first zip, 3 = second zip
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError(5, "Input/output error")
        return real_rename(src, dst)

    with mock.patch("sigmond.sink_doors.os.rename", side_effect=rename):
        with pytest.raises(sd.DoorError) as caught:
            sd.close_doors(NOW, grape=g, mag=m, chown=mock.Mock())
    assert [src for src, _ in caught.value.moved] == [obs, first]
    assert not list(g.glob("*/.upload_complete"))   # nothing marked after a stop


def test_a_marking_failure_carries_every_pair_moved_so_far(tmp_path):
    g, obs = _grape(tmp_path)
    m, z = _mag(tmp_path)
    chown = mock.Mock(side_effect=PermissionError(1, "Operation not permitted"))
    with pytest.raises(sd.DoorError) as caught:
        sd.close_doors(NOW, grape=g, mag=m, chown=chown)
    assert {src for src, _ in caught.value.moved} == {obs, z}
    assert "stays off" in str(caught.value)


def test_a_failed_chown_removes_the_day_directory_this_call_made(tmp_path):
    spool = tmp_path / "upload"
    spool.mkdir()
    chown = mock.Mock(side_effect=PermissionError(1, "Operation not permitted"))
    with pytest.raises(sd.DoorError) as caught:
        sd.mark_days_complete(spool, date(2026, 10, 7), chown=chown)
    assert not (spool / "20261006").exists()    # no root-owned day directory left
    assert "stays off" in str(caught.value) and caught.value.moved == []


def test_a_failed_chmod_removes_the_day_directory_this_call_made(tmp_path):
    spool = tmp_path / "upload"
    spool.mkdir()
    chmod = mock.Mock(side_effect=PermissionError(1, "Operation not permitted"))
    with pytest.raises(sd.DoorError):
        sd.mark_days_complete(spool, date(2026, 10, 7), chown=mock.Mock(), chmod=chmod)
    assert not (spool / "20261006").exists()


def test_a_failed_marker_chown_leaves_an_existing_day_directory_alone(tmp_path):
    spool = tmp_path / "upload"
    (spool / "20261006").mkdir(parents=True)
    chown = mock.Mock(side_effect=PermissionError(1, "Operation not permitted"))
    with pytest.raises(sd.DoorError):
        sd.mark_days_complete(spool, date(2026, 10, 7), chown=chown)
    assert (spool / "20261006").is_dir()                      # not ours to remove
    assert not (spool / "20261006" / ".upload_complete").exists()   # a rerun retries it


def test_a_day_directory_this_call_made_takes_the_spool_mode(tmp_path):
    spool = tmp_path / "upload"
    spool.mkdir()
    os.chmod(spool, 0o750)
    (spool / "20261006").mkdir()               # GRAPE made this one; leave its mode
    chmod = mock.Mock()
    sd.mark_days_complete(spool, date(2026, 10, 7), days=2, chown=mock.Mock(), chmod=chmod)
    assert [c.args for c in chmod.call_args_list] == [(spool / "20261005", 0o750)]


def test_the_default_chmod_applies_the_spool_mode_under_a_plain_umask(tmp_path):
    spool = tmp_path / "upload"
    spool.mkdir()
    os.chmod(spool, 0o750)
    old = os.umask(0o022)
    try:
        sd.mark_days_complete(spool, date(2026, 10, 7), days=1, chown=mock.Mock())
    finally:
        os.umask(old)
    assert stat.S_IMODE((spool / "20261006").stat().st_mode) == 0o750


def test_set_aside_moves_a_file_named_like_a_grape_package(tmp_path):
    # hs-uploader's file source takes files always and directories when
    # match_dirs is set, so a FILE named OBS-x ships from the GRAPE spool.
    spool, obs = _grape(tmp_path)
    stray = spool / "20261005" / "OBS-x"
    stray.write_bytes(b"x")
    moved = sd.set_aside(spool, "OBS*", match_dirs=True, stamp="S")
    assert {src for src, _ in moved} == {obs, stray}
    assert not stray.exists()


def test_set_aside_leaves_a_directory_named_like_a_zip_but_takes_the_zips_in_it(tmp_path):
    spool, z = _mag(tmp_path)
    lookalike = spool / "x.zip"
    lookalike.mkdir()
    inner = lookalike / "inner.zip"
    inner.write_bytes(b"z")
    moved = sd.set_aside(spool, "*.zip", match_dirs=False, stamp="S")
    assert {src for src, _ in moved} == {z, inner}
    assert lookalike.is_dir()                  # match_dirs=False: not a match itself
    assert list(spool.rglob("*.zip")) == [lookalike]    # only the directory remains


@pytest.mark.parametrize("state", ["activating", "active", "deactivating", "reloading",
                                   "maintenance", "unknown", ""])
def test_packaging_running_fails_closed(state):
    run = _systemctl({"grape-daily.service": state,
                      "mag-recorder-upload.service": "inactive"})
    assert sd.packaging_running(run=run) == ["grape-daily.service"]


@pytest.mark.parametrize("state", ["inactive", "failed"])
def test_packaging_running_counts_inactive_and_failed_idle(state):
    run = _systemctl({"grape-daily.service": state, "mag-recorder-upload.service": state})
    assert sd.packaging_running(run=run) == []


def test_packaging_running_counts_a_unit_whose_answer_has_no_stdout():
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 3, stdout=None, stderr="")
    assert sd.packaging_running(run=run) == list(sd.PACKAGING_UNITS)


def test_close_doors_refuses_a_naive_time_before_it_does_anything(tmp_path):
    g, obs = _grape(tmp_path)
    with pytest.raises(ValueError):
        sd.close_doors(datetime(2026, 10, 7, 15, 30), grape=g,
                       mag=tmp_path / "absent", chown=mock.Mock())
    assert obs.is_dir()


def test_close_doors_reads_a_non_utc_time_as_utc(tmp_path):
    g, obs = _grape(tmp_path)
    # 02:30 on 8 Oct at UTC+5 is 21:30 on 7 Oct UTC.
    local = datetime(2026, 10, 8, 2, 30, tzinfo=timezone(timedelta(hours=5)))
    report = sd.close_doors(local, grape=g, mag=tmp_path / "absent", chown=mock.Mock())
    assert all("20261007T213000Z" in str(dst) for _, dst in report.moved)
    assert [m.parent.name for m in report.marked][0] == "20261006"


def test_close_doors_hands_chown_and_chmod_to_the_day_directories(tmp_path):
    g, obs = _grape(tmp_path)                   # 20261005 exists; six more get made
    chown, chmod = mock.Mock(), mock.Mock()
    sd.close_doors(NOW, grape=g, mag=tmp_path / "absent", chown=chown, chmod=chmod)
    made = {c.args[0] for c in chmod.call_args_list}
    assert made == {g / d for d in ("20261006", "20261004", "20261003",
                                    "20261002", "20261001", "20260930")}
    assert {c.args[1] for c in chmod.call_args_list} == {stat.S_IMODE(g.stat().st_mode)}
    assert g / "20261006" in {c.args[0] for c in chown.call_args_list}


# -- the config hook around close_doors (Task 5) ----------------------------

def test_the_config_hook_refuses_on_a_door_error_and_stamps_utc():
    import sigmond.commands.config as cfg
    seen = {}

    def fake(now, **kw):
        seen["now"] = now
        return sd.DoorReport()

    with mock.patch.object(sd, "packaging_running", return_value=[]):
        with mock.patch.object(sd, "close_doors", side_effect=sd.DoorError("cross-fs")):
            assert cfg._close_doors() == 1
        with mock.patch.object(sd, "close_doors", side_effect=fake):
            assert cfg._close_doors() == 0
    assert seen["now"].utcoffset() is not None
    assert seen["now"].utcoffset().total_seconds() == 0


def test_the_config_hook_refuses_while_packaging_runs(capsys):
    # §10.3 item 3: never move a package that a unit still writes.
    import sigmond.commands.config as cfg
    with mock.patch.object(sd, "packaging_running",
                           return_value=["grape-daily.service"]), \
            mock.patch.object(sd, "close_doors") as close:
        assert cfg._close_doors() == 1
    close.assert_not_called()
    out = "".join(capsys.readouterr())
    assert "grape-daily.service" in out
    assert "again" in out and "stays off" in out


def test_the_config_hook_prints_what_moved_before_a_door_error(capsys):
    # A rename or marking step can fail after some moves.  The message names
    # one held directory; the pairs say where everything went.
    import sigmond.commands.config as cfg
    a, b = Path("/spool/20261005/OBS2026-10-05T00-00"), Path("/held/20261007T153000Z/OBS")
    err = sd.DoorError("could not mark 20261006", moved=[(a, b)])
    with mock.patch.object(sd, "packaging_running", return_value=[]), \
            mock.patch.object(sd, "close_doors", side_effect=err):
        assert cfg._close_doors() == 1
    out = "".join(capsys.readouterr())
    assert str(a) in out and str(b) in out
    assert "could not mark 20261006" in out
    # What moved comes first, so the error is the last thing the operator reads.
    assert out.index(str(a)) < out.index("could not mark 20261006")


def test_the_config_hook_prints_what_moved_and_what_it_marked(capsys):
    import sigmond.commands.config as cfg
    a, b = Path("/spool/20261005/OBS2026-10-05T00-00"), Path("/held/20261007T153000Z/OBS")
    marked = [Path("/spool/20261006/.complete"), Path("/spool/20261004/.complete")]
    report = sd.DoorReport(moved=[(a, b)], marked=marked)
    with mock.patch.object(sd, "packaging_running", return_value=[]), \
            mock.patch.object(sd, "close_doors", return_value=report):
        assert cfg._close_doors() == 0
    out = "".join(capsys.readouterr())
    assert str(a) in out and str(b) in out
    assert "marked 2 earlier GRAPE days packaged" in out
