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
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("add asked a question"))
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
    assert tools[4:] == ["blkid", "systemctl", "mount"]
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
