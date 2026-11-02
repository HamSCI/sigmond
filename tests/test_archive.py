"""smd archive, phase 1 (tasks/plan-external-storage.md): finding the drive,
judging its placement against the RX-888, and the fstab and config text.

The sysfs trees here copy the shape of a real one: /sys/block/<disk>/device
links into /sys/devices/pci.../usbN/<devpath>/<devpath>:1.0/hostN/..., and
/sys/bus/usb/devices/<devpath> links to the same USB device directory."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from sigmond import archive as ar

PCI_A = "0000:00:14.0"     # the controller the RX-888 sits on
PCI_B = "0000:05:00.3"     # a second controller


def _usb_dev(root: Path, pci: str, bus: int, devpath: str, vid: str = "0781",
             pid: str = "5581", serial: str = "") -> Path:
    d = root / "devices" / "pci0000:00" / pci / f"usb{bus}"
    for i in range(len(devpath.split("."))):
        d = d / ".".join(devpath.split(".")[:i + 1])
    d.mkdir(parents=True, exist_ok=True)
    (d / "idVendor").write_text(vid + "\n")
    (d / "idProduct").write_text(pid + "\n")
    if serial:
        (d / "serial").write_text(serial + "\n")
    usb = root / "bus" / "usb" / "devices"
    usb.mkdir(parents=True, exist_ok=True)
    link = usb / devpath
    if not link.exists():
        link.symlink_to(os.path.relpath(d, usb))
    return d


def _disk(root: Path, name: str, scsi_dir: Path, sectors: int,
          model: str = "Portable SSD", vendor: str = "Samsung") -> None:
    scsi_dir.mkdir(parents=True, exist_ok=True)
    (scsi_dir / "model").write_text(model + "\n")
    (scsi_dir / "vendor").write_text(vendor + "\n")
    b = root / "block" / name
    b.mkdir(parents=True, exist_ok=True)
    (b / "size").write_text(f"{sectors}\n")
    (b / "device").symlink_to(os.path.relpath(scsi_dir, b))


def _usb_disk(root: Path, name: str, pci: str, bus: int, devpath: str,
              sectors: int = 3907029168, serial: str = "S6XNNS0T") -> None:
    d = _usb_dev(root, pci, bus, devpath, serial=serial)
    _disk(root, name, d / f"{devpath}:1.0" / "host2" / "target2:0:0" / "2:0:0:0", sectors)


@pytest.fixture
def station(tmp_path):
    """The VM's own disk on virtio-scsi, and an RX-888 on port 4 of the hub
    at 2-1, on controller A."""
    root = tmp_path / "sys"
    _disk(root, "sda", root / "devices" / "pci0000:00" / "0000:00:05.0" / "virtio1"
          / "host0" / "target0:0:0" / "0:0:0:0", 524288000, "QEMU HARDDISK", "QEMU")
    _usb_dev(root, PCI_A, 2, "2-1", vid="2109", pid="0817")        # the hub
    _usb_dev(root, PCI_A, 2, "2-1.4", vid="04b4", pid="00f1")      # the RX-888
    (root / "block" / "loop0").mkdir(parents=True)
    return root


def _judge(root: Path, name: str):
    disks = ar.list_disks(root / "block")
    return ar.judge_placement(disks[name], ar.rx888_devpaths(root / "bus" / "usb" / "devices"),
                              root / "bus" / "usb" / "devices")


def test_finds_the_rx888(station):
    assert ar.rx888_devpaths(station / "bus" / "usb" / "devices") == ["2-1.4"]


def test_lists_whole_disks_and_skips_loop(station):
    _usb_disk(station, "sdb", PCI_B, 4, "4-2")
    disks = ar.list_disks(station / "block")
    assert set(disks) == {"sda", "sdb"}
    assert disks["sda"].usb_devpath is None
    sdb = disks["sdb"]
    assert (sdb.usb_devpath, sdb.controller) == ("4-2", PCI_B)
    assert sdb.size_bytes == 3907029168 * 512
    assert sdb.model == "Samsung Portable SSD"
    assert sdb.serial == "S6XNNS0T"     # read from the USB device, not the SCSI one


def test_drive_on_another_controller_is_ok(station):
    _usb_disk(station, "sdb", PCI_B, 4, "4-2")
    assert _judge(station, "sdb").verdict == "ok"


def test_drive_on_the_rx888_hub_is_refused(station):
    _usb_disk(station, "sdb", PCI_A, 2, "2-1.2")
    p = _judge(station, "sdb")
    assert p.verdict == "refuse"
    assert "RX-888's hub (2-1)" in p.reason


def test_drive_in_a_hub_nested_inside_the_rx888_hub_is_refused(station):
    # A seven-port hub is often two hubs chained: 2-1.1 is the inner one.
    _usb_dev(station, PCI_A, 2, "2-1.1", vid="2109", pid="2817")
    _usb_disk(station, "sdb", PCI_A, 2, "2-1.1.3")
    assert _judge(station, "sdb").verdict == "refuse"


def test_drive_on_the_rx888_controller_but_another_hub_warns(station):
    _usb_disk(station, "sdb", PCI_A, 2, "2-2")
    p = _judge(station, "sdb")
    assert p.verdict == "warn"
    assert PCI_A in p.reason


def test_a_hub_named_like_a_prefix_is_not_the_rx888_hub(station):
    # 2-11 starts with "2-1" as text, but it is a different port.
    _usb_disk(station, "sdb", PCI_B, 2, "2-11")
    assert _judge(station, "sdb").verdict != "refuse"


def test_rx888_on_a_root_port_refuses_nothing_on_its_bus(tmp_path):
    root = tmp_path / "sys"
    _usb_dev(root, PCI_A, 2, "2-3", vid="04b4", pid="00f1")
    _usb_disk(root, "sdb", PCI_A, 2, "2-1.2")
    assert _judge(root, "sdb").verdict == "warn"


def test_the_vms_own_disk_is_refused(station):
    p = _judge(station, "sda")
    assert p.verdict == "refuse" and "not on USB" in p.reason


def test_no_rx888_at_all_is_ok(tmp_path):
    root = tmp_path / "sys"
    _usb_disk(root, "sdb", PCI_A, 2, "2-1.2")
    assert _judge(root, "sdb").verdict == "ok"


def test_mounts_on_matches_the_disk_and_its_partitions_only(tmp_path):
    pm = tmp_path / "mounts"
    pm.write_text("/dev/sda1 / ext4 rw 0 0\n/dev/sdb1 /media/x ext4 rw 0 0\n"
                  "/dev/sdba1 /other ext4 rw 0 0\ntmpfs /run tmpfs rw 0 0\n")
    assert ar.mounts_on("sdb", pm) == {"/dev/sdb1": "/media/x"}
    assert ar.mounts_on("sdc", pm) == {}


def test_partition_names():
    assert ar.partition_name("sdb") == "sdb1"
    assert ar.partition_name("nvme0n1") == "nvme0n1p1"


def test_fstab_line_never_holds_up_a_boot():
    line = ar.fstab_line("1234-abcd")
    assert line.startswith("UUID=1234-abcd /var/lib/sigmond/archive ext4 ")
    assert "nofail" in line and "x-systemd.device-timeout=10s" in line


def test_fstab_keeps_one_archive_line_and_everything_else():
    before = ("# /etc/fstab\nUUID=root / ext4 defaults 0 1\n"
              "UUID=old /var/lib/sigmond/archive ext4 nofail 0 2\n"
              "#UUID=x /var/lib/sigmond/archive ext4 nofail 0 2\n")
    after = ar.fstab_with_archive(before, "new")
    assert after.count("/var/lib/sigmond/archive ext4 nofail,") == 1
    assert "UUID=old" not in after
    assert "UUID=root / ext4 defaults 0 1" in after
    assert "#UUID=x" in after                      # comments stay
    assert ar.fstab_with_archive(after, "new") == after
    gone = ar.fstab_without_archive(after)
    assert "UUID=new" not in gone and "UUID=root" in gone


def test_config_names_the_drive_and_the_archivers_defaults(station):
    _usb_disk(station, "sdb", PCI_B, 4, "4-2")
    text = ar.config_text("u-1", ar.list_disks(station / "block")["sdb"], "2026-11-02T13:00:00Z")
    import tomllib
    cfg = tomllib.loads(text)
    assert cfg["uuid"] == "u-1" and cfg["label"] == "SIGMOND-ARCHIVE"
    assert cfg["mount"] == "/var/lib/sigmond/archive"
    assert (cfg["mode"], cfg["high_water_pct"], cfg["kept_ceiling_pct"]) == ("all", 90, 25)


def test_days_fit_at_fifteen_gb_per_channel_day():
    assert ar.days_fit(2 * 10**12) == pytest.approx(22.2, abs=0.1)
    assert ar.days_fit(90 * 10**9, channels=1) == pytest.approx(6.0)
    assert ar.days_fit(10**12, channels=0) == 0.0


def test_a_drive_cannot_add_keys_to_the_config():
    import tomllib
    evil = ar.Disk("sdb", 1, 'x"\nmode = "none', 'S1"\x00\x1b[2J', "4-2", None)
    cfg = tomllib.loads(ar.config_text("u", evil, "t"))
    assert cfg["mode"] == "all"
    assert cfg["model"] == 'x"mode = "none'
    assert "\x00" not in cfg["serial"] and "\x1b" not in cfg["serial"]


def test_mount_point_must_be_a_real_root_directory(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "archive"
    link.symlink_to(target)
    assert "symlink" in ar.mount_point_problem(link)
    assert "does not exist" in ar.mount_point_problem(tmp_path / "nope")
    plain = tmp_path / "plain"
    plain.mkdir()
    if os.geteuid() != 0:
        assert "not owned by root" in ar.mount_point_problem(plain)
    f = tmp_path / "file"
    f.write_text("")
    assert "not a directory" in ar.mount_point_problem(f)


def test_the_root_directory_passes_the_mount_point_check():
    assert ar.mount_point_problem(Path("/")) is None
