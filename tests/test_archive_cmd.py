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
    assert tools[4:] == ["blkid", "systemctl", "chattr", "mount", "systemctl", "systemctl"]
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
