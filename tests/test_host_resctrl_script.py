"""scripts/proxmox/sigmond-host-resctrl.sh against a fake resctrl tree.

SIGMOND_RESCTRL_ROOT / SIGMOND_CPUINFO point the script at a temp dir, where
writes to schemata / cpus_list are plain files: the test reads back what the
script wrote.  (The kernel's move-on-write semantics are not modelled; the
script writes each group's exact list, so the files say what it asked for.)
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "proxmox" / "sigmond-host-resctrl.sh"

AMD_FLAGS = "flags\t\t: fpu sse cat_l3 cdp_l3 mba\n"


def _tree(tmp_path, *, flags=AMD_FLAGS, gran="1", mb_default="2048",
          with_mb=True):
    root = tmp_path / "resctrl"
    (root / "info" / "L3").mkdir(parents=True)
    (root / "info" / "L3" / "cbm_mask").write_text("ffff\n")
    schem = "    L3:0=ffff\n"
    if with_mb:
        (root / "info" / "MB").mkdir()
        (root / "info" / "MB" / "bandwidth_gran").write_text(gran + "\n")
        schem += f"    MB:0={mb_default}\n"
    (root / "schemata").write_text(schem)
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("model name\t: AMD Ryzen 5 5560U with Radeon Graphics\n"
                       + flags)
    return root, cpuinfo


def _run(root, cpuinfo, *args):
    env = dict(os.environ, SIGMOND_RESCTRL_ROOT=str(root),
               SIGMOND_CPUINFO=str(cpuinfo))
    r = subprocess.run(["bash", str(SCRIPT), *args], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr + r.stdout
    return r.stdout


def _read(root, group, name):
    return (root / group / name).read_text().strip()


def test_without_decoder_args_behaviour_is_unchanged(tmp_path):
    root, ci = _tree(tmp_path)
    out = _run(root, ci, "6,7,8,9", "0,1,2,3,4,5,10,11")
    assert _read(root, "radiod", "schemata") == "L3:0=3ff"
    assert _read(root, "others", "schemata") == "L3:0=fc00"
    assert _read(root, "others", "cpus_list") == "0,1,2,3,4,5,10,11"
    assert _read(root, "radiod", "cpus_list") == "6,7,8,9"
    assert not (root / "decoders").exists()
    assert "decoders" not in out


def test_amd_mba_caps_decoders_at_8_gbps(tmp_path):
    root, ci = _tree(tmp_path)
    out = _run(root, ci, "6,7,8,9", "0,1,2,3,4,5,10,11", "0.62",
               "0,1,2,3,4,5", "8")
    assert _read(root, "decoders", "schemata").splitlines() == [
        "L3:0=fc00", "MB:0=64"]
    assert _read(root, "decoders", "cpus_list") == "0,1,2,3,4,5"
    # Housekeeping pCPUs stay in `others`, uncapped.
    assert _read(root, "others", "cpus_list") == "10,11"
    assert "MB" not in _read(root, "others", "schemata")
    assert _read(root, "radiod", "cpus_list") == "6,7,8,9"
    assert "MB:0=64 (8 GB/s)" in out


def test_ranges_in_cpu_lists(tmp_path):
    root, ci = _tree(tmp_path)
    _run(root, ci, "6-9", "0-5,10-11", "0.62", "0-5", "8")
    assert _read(root, "others", "cpus_list") == "10,11"


def test_cap_clamped_to_unthrottled_max(tmp_path):
    root, ci = _tree(tmp_path)
    _run(root, ci, "6,7", "0-5", "0.62", "0-5", "1000")
    assert "MB:0=2048" in _read(root, "decoders", "schemata")


def test_intel_percentage_scale_is_skipped(tmp_path):
    root, ci = _tree(tmp_path, gran="10", mb_default="100")
    out = _run(root, ci, "6,7,8,9", "0,1,2,3,4,5,10,11", "0.62",
               "0,1,2,3,4,5", "8")
    assert "percentage" in out and "Intel" in out
    assert not (root / "decoders").exists()
    assert _read(root, "others", "cpus_list") == "0,1,2,3,4,5,10,11"


def test_no_mba_flag_is_skipped(tmp_path):
    root, ci = _tree(tmp_path, flags="flags\t\t: fpu cat_l3\n")
    out = _run(root, ci, "6,7", "0-5,8-11", "0.62", "0-5", "8")
    assert "no MBA" in out
    assert not (root / "decoders").exists()


def test_no_mb_resource_is_skipped(tmp_path):
    root, ci = _tree(tmp_path, with_mb=False)
    out = _run(root, ci, "6,7", "0-5,8-11", "0.62", "0-5", "8")
    assert "no MB resource" in out
    assert not (root / "decoders").exists()


def test_no_cat_is_a_noop_even_with_decoder_args(tmp_path):
    root, ci = _tree(tmp_path, flags="flags\t\t: fpu mba\n")
    out = _run(root, ci, "6,7", "0-5", "0.62", "0-5", "8")
    assert "no L3 CAT" in out
    assert not (root / "radiod").exists()
    assert not (root / "decoders").exists()


def test_header_documents_the_restart_hazard():
    head = SCRIPT.read_text().split("set -u", 1)[0]
    assert "STEPPED" in head and "2026-11-06" in head
