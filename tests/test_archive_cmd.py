"""`smd archive add|remove|status` with every outside effect replaced: the
disks, the RX-888, the mounts, fstab, archive.toml, and each command run.
What matters: a refused or unconfirmed drive gets no command at all; a
confirmed one gets wipe, partition, format, in that order, then one fstab
line and the config."""
from __future__ import annotations

import os
import subprocess
import types
from collections import namedtuple

import pytest

from sigmond import archive as ar
from sigmond.commands import archive as cmd

DISK = ar.Disk("sdb", 2 * 10**12, "Samsung Portable SSD", "S6XN", "4-2", "0000:05:00.3")
Usage = namedtuple("Usage", "total used free")


@pytest.fixture
def env(tmp_path, monkeypatch):
    ran = []
    state = types.SimpleNamespace(ran=ran, disks={"sda": ar.Disk("sda", 10**11, "QEMU", "", None, None),
                                                  "sdb": DISK},
                                  rx=["2-1.4"], mounts={})
    monkeypatch.setattr(ar, "FSTAB", tmp_path / "fstab")
    monkeypatch.setattr(ar, "CONFIG", tmp_path / "sigmond" / "archive.toml")
    monkeypatch.setattr(ar, "MOUNT", tmp_path / "archive")
    (tmp_path / "fstab").write_text("UUID=root / ext4 defaults 0 1\n")
    monkeypatch.setattr(cmd.ar, "list_disks", lambda *a, **k: dict(state.disks))
    monkeypatch.setattr(cmd.ar, "rx888_devpaths", lambda *a, **k: list(state.rx))
    monkeypatch.setattr(cmd.ar, "mounts_on", lambda name, *a, **k: dict(state.mounts))
    monkeypatch.setattr(cmd.ar, "mounted_sources", lambda *a, **k: {})
    monkeypatch.setattr(cmd.ar, "judge_placement",
                        lambda d, rx, *a, **k: ar.Placement("ok", "away from the RX-888")
                        if d.usb_devpath and not d.usb_devpath.startswith("2-1.")
                        else ar.Placement("refuse", "on the RX-888's hub"))
    monkeypatch.setattr(cmd.shutil, "which", lambda t: f"/usr/sbin/{t}")
    monkeypatch.setattr(cmd.shutil, "disk_usage", lambda p: Usage(2 * 10**12, 10**9, 2 * 10**12 - 10**9))
    monkeypatch.setattr(cmd, "_channels_in_buffer", lambda: 6)
    state.mount_problem = None
    monkeypatch.setattr(cmd.ar, "mount_point_problem", lambda *a, **k: state.mount_problem)

    monkeypatch.setattr(cmd, "_MOUNT_OWNER_UID", os.getuid())

    def fake_run(argv, input=None, text=None, capture_output=None, pass_fds=()):
        ran.append(argv)
        out = "abcd-1234\n" if argv[0] == "blkid" else ""
        return subprocess.CompletedProcess(argv, 0, out, "")
    monkeypatch.setattr(cmd.subprocess, "run", fake_run)

    def fake_mount(source, target):
        # The mount goes through the handle add opened on the mount point.
        assert target.startswith("/proc/self/fd/")
        assert os.path.samefile(target, ar.MOUNT)
        ran.append(["mount", source, target])
        return 0
    monkeypatch.setattr(cmd, "_libc_mount", fake_mount)
    state.landed = None       # None: the drive lands at the mount point
    monkeypatch.setattr(cmd, "_mount_points_of",
                        lambda part: [str(ar.MOUNT)] if state.landed is None else state.landed)
    monkeypatch.setattr(cmd, "_libc_umount_detach", lambda where: ran.append(["umount2", where]))
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("add asked a question"))
    # No SMART by default: health unknown, add goes on (the health fixture below overrides).
    monkeypatch.setattr(cmd, "_probe", lambda name: (None, "test: no SMART"))
    monkeypatch.setattr(cmd, "HEALTH", tmp_path / "archive-health.json")
    monkeypatch.setattr(cmd, "UDEV_RULE", tmp_path / "udev" / "90-sigmond-archive.rules")
    monkeypatch.setattr(cmd, "_unit_active", lambda unit: False)
    return state


def _args(**kw):
    base = dict(archive_command="add", device="sdb", confirm="sdb", dry_run=False, forget=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_confirmed_add_formats_mounts_and_records(env):
    assert cmd.cmd_archive(_args()) == 0
    tools = [a[0] for a in env.ran]
    assert tools[:4] == ["wipefs", "sfdisk", "udevadm", "mkfs.ext4"]
    assert env.ran[3][-1] == "/dev/sdb1" and "SIGMOND-ARCHIVE" in env.ran[3]
    assert tools[4:] == ["blkid", "systemctl", "chattr", "mount", "systemctl", "systemctl",
                         "udevadm", "umount", "systemctl", "systemctl"]
    fstab = ar.FSTAB.read_text()
    assert "UUID=root / ext4 defaults 0 1" in fstab
    assert f"UUID=abcd-1234 {ar.MOUNT} ext4 nofail," in fstab
    import tomllib
    assert tomllib.loads(ar.CONFIG.read_text())["uuid"] == "abcd-1234"
    assert list(ar.FSTAB.parent.glob("fstab.pre-archive-*"))


def test_a_drive_on_the_rx888_hub_gets_no_command(env):
    env.disks["sdc"] = ar.Disk("sdc", 10**12, "x", "y", "2-1.2", "0000:00:14.0")
    assert cmd.cmd_archive(_args(device="sdc", confirm="sdc")) == 1
    assert env.ran == []


def test_the_vms_own_disk_gets_no_command(env):
    assert cmd.cmd_archive(_args(device="sda", confirm="sda")) == 1
    assert env.ran == []


def test_a_confirmation_naming_another_disk_formats_nothing(env):
    assert cmd.cmd_archive(_args(confirm="sda")) == 1
    assert env.ran == [] and "archive" not in ar.FSTAB.read_text()


def test_a_mounted_drive_is_refused(env):
    env.mounts = {"/dev/sdb1": "/media/usb"}
    assert cmd.cmd_archive(_args()) == 1
    assert env.ran == []


def test_dry_run_changes_nothing(env):
    before = ar.FSTAB.read_text()
    assert cmd.cmd_archive(_args(dry_run=True, confirm=None)) == 0
    assert env.ran == []
    assert ar.FSTAB.read_text() == before and not ar.CONFIG.exists()


def test_unknown_device(env):
    assert cmd.cmd_archive(_args(device="sdz", confirm="sdz")) == 1
    assert env.ran == []


def test_remove_forget_drops_the_line_and_config(env):
    assert cmd.cmd_archive(_args()) == 0
    env.ran.clear()
    assert cmd.cmd_archive(_args(archive_command="remove", forget=True)) == 0
    assert str(ar.MOUNT) not in ar.FSTAB.read_text()
    assert not ar.CONFIG.exists()
    assert "UUID=root / ext4 defaults 0 1" in ar.FSTAB.read_text()


def test_status_without_a_drive(env, capsys):
    assert cmd.cmd_archive(_args(archive_command="status")) == 0
    assert "no archive drive" in capsys.readouterr().err


def test_a_mount_point_that_fails_the_check_is_not_mounted(env):
    env.mount_problem = "/var/lib/sigmond/archive is a symlink"
    assert cmd.cmd_archive(_args()) == 1
    assert "mount" not in [a[0] for a in env.ran]
    assert "systemctl" not in [a[0] for a in env.ran]


def test_a_mount_point_swapped_for_a_symlink_is_not_mounted(env, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    ar.MOUNT.symlink_to(elsewhere)
    assert cmd.cmd_archive(_args()) == 1
    assert "mount" not in [a[0] for a in env.ran]


def test_a_mount_that_lands_elsewhere_is_detached_and_fails(env):
    env.landed = ["/var/lib/sigmond/moved"]
    assert cmd.cmd_archive(_args()) == 1
    assert ["umount2", "/var/lib/sigmond/moved"] in env.ran


def test_sbin_joins_an_operators_path(monkeypatch):
    # AI6VN-1, 2026-11-12: hamsci's PATH has no sbin, so `--dry-run` stopped
    # on "wipefs not found".
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin")
    cmd._with_sbin_on_path()
    assert os.environ["PATH"].split(os.pathsep)[-2:] == ["/usr/sbin", "/sbin"]
    cmd._with_sbin_on_path()
    assert os.environ["PATH"].count("/usr/sbin") == 1


def test_add_gives_the_archiver_its_own_folder(env, monkeypatch):
    me = types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
    monkeypatch.setattr(cmd.pwd, "getpwnam",
                        lambda u: me if u == "timestd" else (_ for _ in ()).throw(KeyError(u)))
    assert cmd.cmd_archive(_args()) == 0
    d = ar.MOUNT / "timestd"
    assert d.is_dir() and d.stat().st_uid == os.getuid()


def test_add_without_the_client_user_makes_no_folder(env, monkeypatch):
    def nobody(u):
        raise KeyError(u)
    monkeypatch.setattr(cmd.pwd, "getpwnam", nobody)
    assert cmd.cmd_archive(_args()) == 0
    assert not (ar.MOUNT / "timestd").exists()


def test_everything_add_says_goes_to_one_stream(env, capsys):
    # Piped stdout printed "found …" and the command list after the success
    # lines; all of it goes to stderr now, in order.
    assert cmd.cmd_archive(_args()) == 0
    out, errtext = capsys.readouterr()
    assert out == ""
    assert errtext.index("found") < errtext.index("$ wipefs") < errtext.index("archive drive mounted")


# ---- 2026-11-13: SMART health before the erase, automount, chattr, UAS quirk, smartd

from sigmond import archive_health as H


def _reading(**kw):
    base = dict(passed=True, model="ST2000LM007-1R8174", disk_serial="ZDZJHMBB", reallocated=0,
                pending=0, offline_uncorrectable=0, temperature_c=30, dev_type="sat",
                selftest_status="passed")
    base.update(kw)
    return H.Reading(**base)


@pytest.fixture
def health(env, monkeypatch, tmp_path):
    hs = types.SimpleNamespace(reading=_reading(), why="", selftests=[], uas_answer=False,
                               rebinds=[])
    monkeypatch.setattr(cmd, "_probe", lambda name: (hs.reading, hs.why))
    monkeypatch.setattr(cmd, "_short_self_test",
                        lambda name, t: hs.selftests.append(name) or "passed")
    monkeypatch.setattr(cmd, "HEALTH", tmp_path / "archive-health.json")
    monkeypatch.setattr(cmd, "MODPROBE_CONF", tmp_path / "modprobe.d" / "sigmond-archive-usb.conf")
    monkeypatch.setattr(cmd, "_usb_ids", lambda devpath: ("0bc2", "2344"))
    monkeypatch.setattr(cmd, "_rebind_usb", lambda devpath: hs.rebinds.append(devpath) or True)
    monkeypatch.setattr(cmd, "_set_runtime_quirk", lambda line: None)
    return hs


def test_add_refuses_fail(env, health):
    health.reading = _reading(pending=8)
    assert cmd.cmd_archive(_args()) == 1
    assert "wipefs" not in [a[0] for a in env.ran]


def test_add_accept_unhealthy_needs_name(env, health):
    health.reading = _reading(pending=8)
    assert cmd.cmd_archive(_args(accept_unhealthy="sdc")) == 1
    assert "wipefs" not in [a[0] for a in env.ran]
    assert cmd.cmd_archive(_args(accept_unhealthy="sdb")) == 0
    assert "wipefs" in [a[0] for a in env.ran]


def test_add_unknown_health_continues(env, health):
    health.reading, health.why = None, "the drive's USB bridge passes no SMART data"
    assert cmd.cmd_archive(_args()) == 0


def test_add_runs_short_self_test_unless_told(env, health):
    assert cmd.cmd_archive(_args()) == 0
    assert health.selftests == ["sdb"]
    health.selftests.clear()
    env.ran.clear()
    ar.FSTAB.write_text("UUID=root / ext4 defaults 0 1\n")
    assert cmd.cmd_archive(_args(no_self_test=True)) == 0
    assert health.selftests == []


def test_add_dry_run_reads_only(env, health):
    assert cmd.cmd_archive(_args(dry_run=True, confirm=None)) == 0
    assert health.selftests == [] and env.ran == []
    assert not cmd.HEALTH.exists()


def test_add_writes_baseline_and_keys(env, health):
    assert cmd.cmd_archive(_args()) == 0
    import json
    import tomllib
    doc = json.loads(cmd.HEALTH.read_text())
    assert doc["baseline"]["disk_serial"] == "ZDZJHMBB"
    cfg = tomllib.loads(ar.CONFIG.read_text())
    assert (cfg["disk_serial"], cfg["smart_dev_type"]) == ("ZDZJHMBB", "sat")


def test_add_masks_smartd_and_enables_health_timer(env, health):
    assert cmd.cmd_archive(_args()) == 0
    assert ["systemctl", "mask", "--now", "smartmontools.service"] in env.ran
    assert ["systemctl", "enable", "--now", "sigmond-archive-health.timer"] in env.ran


def test_add_chattr_before_mount(env, health):
    assert cmd.cmd_archive(_args()) == 0
    tools = [a[0] for a in env.ran]
    assert tools.index("chattr") < tools.index("mount")
    assert env.ran[tools.index("chattr")] == ["chattr", "+i", str(ar.MOUNT)]


def test_add_offers_uas_quirk(env, health, monkeypatch):
    health.reading, health.why = None, "the drive's USB bridge blocks SMART under UAS; ..."
    seq = iter([(None, health.why), (_reading(), "")])
    monkeypatch.setattr(cmd, "_probe", lambda name: next(seq))
    assert cmd.cmd_archive(_args(switch_uas=True)) == 0
    assert cmd.MODPROBE_CONF.read_text().strip() == "options usb-storage quirks=0bc2:2344:u"
    assert health.rebinds == ["4-2"]


def test_add_upgrades_old_fstab_line(env, health):
    ar.FSTAB.write_text("UUID=root / ext4 defaults 0 1\n"
                        f"UUID=abcd-1234 {ar.MOUNT} ext4 nofail,noatime,x-systemd.device-timeout=10s 0 2\n")
    ar.CONFIG.parent.mkdir(parents=True, exist_ok=True)
    ar.CONFIG.write_text('uuid = "abcd-1234"\nmount = "%s"\nserial = "S6XN"\n' % ar.MOUNT)
    assert cmd.cmd_archive(_args(upgrade=True, device=None, confirm=None)) == 0
    assert "x-systemd.automount" in ar.FSTAB.read_text()
    assert "wipefs" not in [a[0] for a in env.ran]
    import tomllib
    cfg = tomllib.loads(ar.CONFIG.read_text())
    assert cfg["uuid"] == "abcd-1234" and cfg["archive_dtype"] == "f16"


# ---- 2026-11-13: health refresh, test --long, eject, forget

def _seed_drive(env, health, tmp_path):
    assert cmd.cmd_archive(_args()) == 0
    env.ran.clear()


def test_health_refresh_updates_latest_only(env, health, tmp_path, monkeypatch):
    _seed_drive(env, health, tmp_path)
    import json
    base = json.loads(cmd.HEALTH.read_text())["baseline"]
    monkeypatch.setattr(cmd.ar, "mount_state", lambda *a, **k: "mounted")
    monkeypatch.setattr(cmd, "_read_once", lambda name, t: (_reading(reallocated=4), ""))
    assert cmd.cmd_archive(_args(archive_command="health-refresh")) == 0
    doc = json.loads(cmd.HEALTH.read_text())
    assert doc["baseline"] == base
    assert doc["latest"]["reallocated"] == 4
    assert oct(os.stat(cmd.HEALTH).st_mode & 0o777) == "0o644"


def test_health_refresh_skips_when_absent(env, health, tmp_path, monkeypatch):
    _seed_drive(env, health, tmp_path)
    monkeypatch.setattr(cmd.ar, "mount_state", lambda *a, **k: "absent")
    monkeypatch.setattr(cmd, "_read_once", lambda *a: pytest.fail("read an absent drive"))
    assert cmd.cmd_archive(_args(archive_command="health-refresh")) == 0
    import json
    assert json.loads(cmd.HEALTH.read_text())["latest_state"] == "absent"


def test_health_refresh_standby_keeps_previous(env, health, tmp_path, monkeypatch):
    _seed_drive(env, health, tmp_path)
    import json
    before = json.loads(cmd.HEALTH.read_text())["latest"]
    monkeypatch.setattr(cmd.ar, "mount_state", lambda *a, **k: "mounted")
    monkeypatch.setattr(cmd, "_read_once", lambda name, t: (None, "standby"))
    assert cmd.cmd_archive(_args(archive_command="health-refresh")) == 0
    doc = json.loads(cmd.HEALTH.read_text())
    assert doc["latest"] == before and "skipped_standby_utc" in doc


def test_test_long_starts_and_returns(env, health, tmp_path, monkeypatch):
    _seed_drive(env, health, tmp_path)
    started = []
    monkeypatch.setattr(cmd.H, "start_long_test", lambda name, t, run=None: started.append(name) or True)
    assert cmd.cmd_archive(_args(archive_command="test", long=True)) == 0
    assert started == ["sdb"]


def test_eject_waits_for_archiver_and_unmounts(env, health, tmp_path, monkeypatch, capsys):
    _seed_drive(env, health, tmp_path)
    states = iter(["active", "active", "inactive"])
    monkeypatch.setattr(cmd, "_unit_active", lambda unit: next(states) == "active")
    monkeypatch.setattr(cmd.time, "sleep", lambda s: None)
    assert cmd.cmd_archive(_args(archive_command="eject")) == 0
    stop = [a for a in env.ran if a[:2] == ["systemctl", "stop"]][0]
    esc = cmd._unit_name(ar.MOUNT)
    assert stop == ["systemctl", "stop", f"{esc}.automount", f"{esc}.mount"]
    assert "safe to unplug" in capsys.readouterr().err


def test_forget_removes_timer(env, health, tmp_path):
    _seed_drive(env, health, tmp_path)
    assert cmd.cmd_archive(_args(archive_command="remove", forget=True)) == 0
    assert ["systemctl", "disable", "--now", "sigmond-archive-health.timer"] in env.ran


def test_unit_name_escapes_like_systemd():
    from pathlib import Path
    assert cmd._unit_name(Path("/var/lib/sigmond/archive")) == "var-lib-sigmond-archive"
    assert cmd._unit_name(Path("/mnt/a-b")) == "mnt-a\\x2db"


# ---- 2026-11-13: status from files and mountinfo; days held and days that fit

@pytest.fixture
def status_files(env, health, tmp_path, monkeypatch):
    assert cmd.cmd_archive(_args()) == 0
    import json
    s = tmp_path / "archive-status.json"
    s.write_text(json.dumps({
        "run_utc": "2026-11-13T15:00:00Z", "state": "ok", "behind": 0, "failures": 0,
        "copied": 12, "kept_f32": 0, "last_error": None, "bytes_per_day": 40e9,
        "channels": {"WWV_10000": {"oldest": "20261101", "newest": "20261112", "days": 12,
                                   "bytes": 80e9, "dtype": "complex-f16"},
                     "WWV_5000": {"oldest": "20261105", "newest": "20261112", "days": 8,
                                  "bytes": 50e9, "dtype": "complex-f16"}}}))
    monkeypatch.setattr(cmd, "ARCHIVER_STATUS", s)
    return s


def test_status_never_stats_mount_when_away(env, status_files, monkeypatch, capsys):
    monkeypatch.setattr(cmd.ar, "mount_state", lambda *a, **k: "absent")
    monkeypatch.setattr(cmd.shutil, "disk_usage", lambda p: pytest.fail("touched the mount"))
    assert cmd.cmd_archive(_args(archive_command="status")) == 0
    assert "not plugged in" in capsys.readouterr().err


def test_status_present_not_mounted_shows_fix(env, status_files, monkeypatch, capsys):
    monkeypatch.setattr(cmd.ar, "mount_state", lambda *a, **k: "present")
    monkeypatch.setattr(cmd.shutil, "disk_usage", lambda p: pytest.fail("touched the mount"))
    assert cmd.cmd_archive(_args(archive_command="status")) == 0
    assert f"sudo mount {ar.MOUNT}" in capsys.readouterr().err


def test_status_days_held_per_channel_and_fit(env, status_files, monkeypatch, capsys):
    monkeypatch.setattr(cmd.ar, "mount_state", lambda *a, **k: "mounted")
    assert cmd.cmd_archive(_args(archive_command="status")) == 0
    out = capsys.readouterr().err
    assert "WWV_10000" in out and "12 days" in out and "20261101 to 20261112" in out
    # free 2e12 - 1e9 at the measured 40 GB/day -> about 50 days (not the 15 GB/channel guess)
    assert "about 50 more days at the measured 40.0 GB/day" in out
    assert "export-f32" in out


# ---- final-review fixes: the automount is live at once, and comes back after eject/remove

@pytest.fixture
def udev(env, tmp_path, monkeypatch):
    p = tmp_path / "udev" / "90-sigmond-archive.rules"
    monkeypatch.setattr(cmd, "UDEV_RULE", p)
    monkeypatch.setattr(cmd, "_unit_active", lambda unit: False)
    return p


def test_add_activates_automount(env, health, udev):
    assert cmd.cmd_archive(_args()) == 0
    u = cmd._unit_name(ar.MOUNT)
    seq = [a for a in env.ran if a[0] in ("umount", "systemctl") and
           (a[0] == "umount" or a[1] == "start")]
    assert seq[-3:] == [["umount", str(ar.MOUNT)], ["systemctl", "start", f"{u}.automount"],
                        ["systemctl", "start", f"{u}.mount"]]
    rule = udev.read_text()
    assert 'ENV{ID_FS_UUID}=="abcd-1234"' in rule and f"start {u}.automount" in rule


def test_remove_waits_and_stops_automount(env, health, udev, monkeypatch):
    assert cmd.cmd_archive(_args()) == 0
    env.ran.clear()
    states = iter([True, False])
    monkeypatch.setattr(cmd, "_unit_active", lambda unit: next(states, False))
    monkeypatch.setattr(cmd.time, "sleep", lambda s: None)
    assert cmd.cmd_archive(_args(archive_command="remove")) == 0
    u = cmd._unit_name(ar.MOUNT)
    assert ["systemctl", "stop", f"{u}.automount", f"{u}.mount"] in env.ran
    assert udev.exists()              # a replug brings the automount back


def test_forget_removes_udev_rule(env, health, udev):
    assert cmd.cmd_archive(_args()) == 0
    assert cmd.cmd_archive(_args(archive_command="remove", forget=True)) == 0
    assert not udev.exists()


def test_upgrade_never_switches_uas_while_mounted(env, health, udev, monkeypatch):
    assert cmd.cmd_archive(_args()) == 0
    env.ran.clear()
    monkeypatch.setattr(cmd.ar, "mount_state", lambda *a, **k: "mounted")
    monkeypatch.setattr(cmd, "_probe", lambda name: (None, "the drive's USB bridge blocks SMART under UAS; x"))
    import sigmond.storage.guard as G
    monkeypatch.setattr(G, "seal_bare_dir", lambda *a, **k: "already")
    assert cmd.cmd_archive(_args(upgrade=True, device=None, confirm=None, switch_uas=True)) == 0
    assert health.rebinds == []


def test_unit_active_counts_activating(monkeypatch):
    # A oneshot run in progress reads "activating", not "active" (AI6VN-1, 11-13:
    # --upgrade's umount hit an archiver run it had not waited for).
    import subprocess as sp
    for state, want in (("active", True), ("activating", True), ("deactivating", True),
                        ("reloading", True), ("inactive", False), ("failed", False)):
        monkeypatch.setattr(cmd.subprocess, "run",
                            lambda argv, _s=state, **kw: sp.CompletedProcess(
                                argv, 0 if _s == "active" else 3, _s + "\n", ""))
        assert cmd._unit_active("x.service") is want, state


# ---- re-attach after a reinstall (Rob 2026-11-14: AI6VN-1 lost its archive config)

@pytest.fixture
def labelled(env, health, monkeypatch):
    monkeypatch.setattr(cmd, "_partition_label", lambda part: ar.LABEL)
    return env


def test_labelled_drive_reattaches_without_format(labelled):
    assert cmd.cmd_archive(_args()) == 0
    tools = [a[0] for a in labelled.ran]
    assert "wipefs" not in tools and "mkfs.ext4" not in tools
    assert "UUID=abcd-1234" in ar.FSTAB.read_text()
    import tomllib
    assert tomllib.loads(ar.CONFIG.read_text())["uuid"] == "abcd-1234"


def test_reattach_restores_settings_and_baseline_from_drive(labelled):
    meta = ar.MOUNT / ".sigmond"
    meta.mkdir(parents=True)
    (meta / "archive.toml").write_text(
        'uuid = "abcd-1234"\nmount = "/old"\nhigh_water_pct = 80\nevict_first = ["/x/wav"]\n'
        'archive_dtype = "f16"\nserial = "S6XN"\n')
    import json
    (meta / "archive-health.json").write_text(json.dumps(
        {"baseline": {"disk_serial": "OLD", "reallocated": 0}, "baseline_utc": "2026-11-12T21:47:00Z",
         "latest": {"disk_serial": "OLD"}, "latest_utc": "2026-11-13T00:00:00Z"}))
    assert cmd.cmd_archive(_args()) == 0
    import tomllib
    cfg = tomllib.loads(ar.CONFIG.read_text())
    assert cfg["high_water_pct"] == 80 and cfg["evict_first"] == ["/x/wav"]
    assert cfg["mount"] == str(ar.MOUNT)
    doc = json.loads(cmd.HEALTH.read_text())
    assert doc["baseline_utc"] == "2026-11-12T21:47:00Z"


def test_format_flag_erases_a_labelled_drive(labelled):
    assert cmd.cmd_archive(_args(format=True)) == 0
    assert "mkfs.ext4" in [a[0] for a in labelled.ran]


def test_add_keeps_a_copy_of_the_settings_on_the_drive(env, health):
    assert cmd.cmd_archive(_args()) == 0
    import tomllib
    assert tomllib.loads((ar.MOUNT / ".sigmond" / "archive.toml").read_text())["uuid"] == "abcd-1234"
    assert (ar.MOUNT / ".sigmond" / "archive-health.json").exists()
