"""Tests for scripts/proxmox/pm-heartbeat.py — the Proxmox-HOST heartbeat.

A PM (the Proxmox host itself) runs NO sigmond, no venv, no pip — plain
PVE Debian with a system python3.  So pm-heartbeat.py is a single
stdlib-only file with NO import from ``sigmond``, and this test file
loads it by PATH (the module has a dash in its filename, so it cannot be
``import``ed normally) — mirroring tests/test_fleetboard.py's pattern for
the server/heartbeat/ scripts, which face the identical "flat file, not a
package" constraint.

THE CONTRACT TEST (``test_contract_envelope_validates_against_schema``)
is the point of this file: pm-heartbeat.py inlines its own copy of
KIND/SCHEMA_VERSION/VERDICTS/PRECEDENCE (a PM cannot import
sigmond.heartbeat_schema either), and that inlined copy can drift from
the real one silently. This test is the drift guard — it feeds a real
assembled envelope through the REAL ``sigmond.heartbeat_schema.validate``
and demands an empty error list.
"""

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROXMOX = REPO / "scripts" / "proxmox"
PM_SCRIPT = PROXMOX / "pm-heartbeat.py"
SETUP_SCRIPT = PROXMOX / "pm-heartbeat-setup.sh"

if str(REPO / "lib") not in sys.path:
    sys.path.insert(0, str(REPO / "lib"))

from sigmond import heartbeat_schema  # noqa: E402

_spec = importlib.util.spec_from_file_location("pm_heartbeat", PM_SCRIPT)
pm_heartbeat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pm_heartbeat)


NOW = datetime(2026, 8, 20, 14, 5, 6, tzinfo=timezone.utc)

CONFIG = {"station": "b4-pm", "interval_sec": 300}


def rr(rc, stdout="", stderr=""):
    """A fake subprocess result — pm_heartbeat.RunResult(rc, stdout, stderr)."""
    return pm_heartbeat.RunResult(rc, stdout, stderr)


def rich_checks(**overrides):
    """The five top-level check runners a fully-healthy PM would wire.

    Keys mirror pm_heartbeat.assemble()'s expectations exactly: "now",
    "uptime_s" and one raw-returning callable per BLOCK_NAMES entry.
    """
    checks = {
        "now": lambda: NOW,
        "uptime_s": lambda: 123456.7,
        "versions": lambda: {"pveversion": "pve-manager/8.2.4", "kernel": "6.8.0-1"},
        "doctor": lambda: pm_heartbeat.build_doctor_raw(
            vmid=100,
            expect_cat=False,
            run_guest_exec=lambda vmid: rr(0),
            run_qm_status=lambda vmid: rr(0, "status: running"),
            run_qm_config=lambda vmid: rr(0, "hookscript: local:snippets/cpu-pin-100.sh\n"),
            probe_cat=lambda: {"present": False, "cpus_list": None, "schemata": None},
        ),
        "resources": lambda: {
            "loadavg": {"load1": 0.5, "load5": 0.4, "load15": 0.3},
            "disk": {"/": 42.0, "/var/lib/vz": 61.0},
            "uptime_s": 123456.7,
            "temperature": 47.5,
        },
    }
    checks.update(overrides)
    return checks


# ---------------------------------------------------------------------------
# THE CONTRACT TEST
# ---------------------------------------------------------------------------

def test_contract_envelope_validates_against_schema():
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks())
    errors = heartbeat_schema.validate(envelope)
    assert errors == []
    assert set(envelope["blocks"]) == {"versions", "doctor", "resources"}
    assert envelope["role"] == "pm"
    assert envelope["kind"] == heartbeat_schema.KIND
    assert envelope["schema_version"] == heartbeat_schema.SCHEMA_VERSION


def test_envelope_is_json_serializable():
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks())
    round_tripped = json.loads(json.dumps(envelope))
    assert round_tripped["station"] == "b4-pm"


def test_block_names_constant_is_a_strict_subset_in_report_order():
    assert pm_heartbeat.BLOCK_NAMES == ("versions", "doctor", "resources")
    assert set(pm_heartbeat.BLOCK_NAMES) <= set(heartbeat_schema.BLOCK_NAMES)


def test_inlined_verdicts_and_precedence_match_the_real_schema():
    """The drift guard for the inlined constants themselves."""
    assert pm_heartbeat.VERDICTS == heartbeat_schema.VERDICTS
    assert pm_heartbeat.PRECEDENCE == heartbeat_schema.PRECEDENCE
    assert pm_heartbeat.KIND == heartbeat_schema.KIND
    assert pm_heartbeat.SCHEMA_VERSION == heartbeat_schema.SCHEMA_VERSION


def test_missing_station_raises():
    with pytest.raises(ValueError):
        pm_heartbeat.assemble({"interval_sec": 300}, rich_checks())


# ---------------------------------------------------------------------------
# Wedge mapping — the point of the whole feature
# ---------------------------------------------------------------------------

def test_guest_agent_wedged_when_vm_running_and_agent_dead():
    raw = pm_heartbeat.build_doctor_raw(
        vmid=100, expect_cat=False,
        run_guest_exec=lambda vmid: rr(255, "", "QEMU guest agent is not running"),
        run_qm_status=lambda vmid: rr(0, "status: running"),
        run_qm_config=lambda vmid: rr(0, "hookscript: local:snippets/x.sh\n"),
        probe_cat=lambda: {"present": False},
    )
    block = pm_heartbeat._map_doctor(raw)
    kinds = [f["kind"] for f in block["data"]["findings"]]
    assert "guest-agent-wedged" in kinds
    assert block["verdict"] == "INVALID"


def test_vm_stopped_not_wedged_when_agent_dead_and_vm_stopped():
    raw = pm_heartbeat.build_doctor_raw(
        vmid=100, expect_cat=False,
        run_guest_exec=lambda vmid: rr(255, "", "QEMU guest agent is not running"),
        run_qm_status=lambda vmid: rr(0, "status: stopped"),
        run_qm_config=lambda vmid: rr(0, "hookscript: local:snippets/x.sh\n"),
        probe_cat=lambda: {"present": False},
    )
    block = pm_heartbeat._map_doctor(raw)
    kinds = [f["kind"] for f in block["data"]["findings"]]
    assert "vm-stopped" in kinds
    assert "guest-agent-wedged" not in kinds
    assert block["verdict"] == "INVALID"


def test_guest_exec_rc0_is_no_finding():
    raw = pm_heartbeat.build_doctor_raw(
        vmid=100, expect_cat=False,
        run_guest_exec=lambda vmid: rr(0),
        run_qm_status=lambda vmid: rr(0, "status: running"),
        run_qm_config=lambda vmid: rr(0, "hookscript: local:snippets/x.sh\n"),
        probe_cat=lambda: {"present": False},
    )
    block = pm_heartbeat._map_doctor(raw)
    kinds = [f["kind"] for f in block["data"]["findings"]]
    assert not any("guest" in k for k in kinds)
    assert block["verdict"] == "VALID"
    assert block["reason"] == "3 checks passed"


def test_guest_exec_other_error_is_verbatim_stderr():
    raw = pm_heartbeat.build_doctor_raw(
        vmid=100, expect_cat=False,
        run_guest_exec=lambda vmid: rr(1, "", "connection refused"),
        run_qm_status=lambda vmid: rr(0, "status: running"),
        run_qm_config=lambda vmid: rr(0, "hookscript: local:snippets/x.sh\n"),
        probe_cat=lambda: {"present": False},
    )
    block = pm_heartbeat._map_doctor(raw)
    findings = block["data"]["findings"]
    matches = [f for f in findings if f["kind"] == "guest-exec-error"]
    assert len(matches) == 1
    assert "connection refused" in matches[0]["detail"]


def test_hookscript_missing_is_a_finding_and_invalid():
    raw = pm_heartbeat.build_doctor_raw(
        vmid=100, expect_cat=False,
        run_guest_exec=lambda vmid: rr(0),
        run_qm_status=lambda vmid: rr(0, "status: running"),
        run_qm_config=lambda vmid: rr(0, "onboot: 1\n"),
        probe_cat=lambda: {"present": False},
    )
    block = pm_heartbeat._map_doctor(raw)
    kinds = [f["kind"] for f in block["data"]["findings"]]
    assert "hookscript-missing" in kinds
    assert block["verdict"] == "INVALID"


# ---------------------------------------------------------------------------
# CAT gate
# ---------------------------------------------------------------------------

def test_cat_expected_and_missing_is_a_finding():
    raw = pm_heartbeat.build_doctor_raw(
        vmid=100, expect_cat=True,
        run_guest_exec=lambda vmid: rr(0),
        run_qm_status=lambda vmid: rr(0, "status: running"),
        run_qm_config=lambda vmid: rr(0, "hookscript: local:snippets/x.sh\n"),
        probe_cat=lambda: {"present": False, "cpus_list": None, "schemata": None},
    )
    block = pm_heartbeat._map_doctor(raw)
    kinds = [f["kind"] for f in block["data"]["findings"]]
    assert "cat-groups-missing" in kinds
    assert block["verdict"] == "INVALID"


def test_cat_not_expected_and_missing_is_data_only_valid_possible():
    raw = pm_heartbeat.build_doctor_raw(
        vmid=100, expect_cat=False,
        run_guest_exec=lambda vmid: rr(0),
        run_qm_status=lambda vmid: rr(0, "status: running"),
        run_qm_config=lambda vmid: rr(0, "hookscript: local:snippets/x.sh\n"),
        probe_cat=lambda: {"present": False, "cpus_list": None, "schemata": None},
    )
    block = pm_heartbeat._map_doctor(raw)
    assert block["verdict"] == "VALID"
    assert block["data"]["cat"]["present"] is False
    assert block["data"]["cat"]["expect_cat"] is False
    kinds = [f["kind"] for f in block["data"]["findings"]]
    assert "cat-groups-missing" not in kinds


def test_cat_present_records_cpus_list_and_schemata():
    raw = pm_heartbeat.build_doctor_raw(
        vmid=100, expect_cat=True,
        run_guest_exec=lambda vmid: rr(0),
        run_qm_status=lambda vmid: rr(0, "status: running"),
        run_qm_config=lambda vmid: rr(0, "hookscript: local:snippets/x.sh\n"),
        probe_cat=lambda: {"present": True, "cpus_list": "12-13", "schemata": "L3:0=fff"},
    )
    block = pm_heartbeat._map_doctor(raw)
    assert block["verdict"] == "VALID"
    assert block["data"]["cat"]["cpus_list"] == "12-13"
    assert block["data"]["cat"]["schemata"] == "L3:0=fff"


# ---------------------------------------------------------------------------
# rollup
# ---------------------------------------------------------------------------

def test_rollup_is_worst_of_present():
    blocks = {
        "versions": {"verdict": "VALID", "reason": "ok"},
        "doctor": {"verdict": "INVALID", "reason": "wedged"},
        "resources": {"verdict": "INCONCLUSIVE", "reason": "partial"},
    }
    result = pm_heartbeat.rollup(blocks)
    assert result["verdict"] == "INVALID"
    assert result["reason"].startswith("doctor: ")


def test_rollup_indeterminate_outranks_inconclusive():
    blocks = {
        "versions": {"verdict": "INCONCLUSIVE", "reason": "meh"},
        "doctor": {"verdict": "INDETERMINATE", "reason": "unreadable"},
        "resources": {"verdict": "VALID", "reason": "ok"},
    }
    result = pm_heartbeat.rollup(blocks)
    assert result["verdict"] == "INDETERMINATE"


def test_rollup_no_blocks_is_indeterminate():
    result = pm_heartbeat.rollup({})
    assert result["verdict"] == "INDETERMINATE"


# ---------------------------------------------------------------------------
# every check-raised path -> INDETERMINATE, never VALID
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("block", pm_heartbeat.BLOCK_NAMES)
def test_any_check_raising_makes_that_block_indeterminate_never_valid(block):
    def boom():
        raise RuntimeError("boom")

    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(**{block: boom}))
    assert envelope["blocks"][block]["verdict"] == "INDETERMINATE"
    assert envelope["rollup"]["verdict"] != "VALID"


@pytest.mark.parametrize("block", pm_heartbeat.BLOCK_NAMES)
def test_missing_check_is_indeterminate_not_a_crash(block):
    checks = rich_checks()
    del checks[block]
    envelope = pm_heartbeat.assemble(CONFIG, checks)
    assert envelope["blocks"][block]["verdict"] == "INDETERMINATE"


def test_doctor_raw_all_three_subchecks_raising_is_indeterminate():
    def boom(*_a, **_k):
        raise OSError("qm: command not found")

    raw = pm_heartbeat.build_doctor_raw(
        vmid=100, expect_cat=False,
        run_guest_exec=boom, run_qm_status=boom, run_qm_config=boom,
        probe_cat=boom,
    )
    block = pm_heartbeat._map_doctor(raw)
    assert block["verdict"] == "INDETERMINATE"
    assert raw["checks_run"] == 0


def test_doctor_raw_one_subcheck_raising_is_unassessed_finding_invalid():
    raw = pm_heartbeat.build_doctor_raw(
        vmid=100, expect_cat=False,
        run_guest_exec=lambda vmid: rr(0),
        run_qm_status=lambda vmid: rr(0, "status: running"),
        run_qm_config=lambda vmid: (_ for _ in ()).throw(OSError("boom")),
        probe_cat=lambda: {"present": False},
    )
    block = pm_heartbeat._map_doctor(raw)
    kinds = [f["kind"] for f in block["data"]["findings"]]
    assert "hookscript-unassessed" in kinds
    assert block["verdict"] == "INVALID"
    assert raw["checks_run"] == 2


def test_versions_both_unreadable_is_indeterminate():
    raw = {"pveversion": None, "kernel": None}
    block = pm_heartbeat._map_versions(raw)
    assert block["verdict"] == "INDETERMINATE"


def test_versions_one_readable_is_valid():
    raw = {"pveversion": None, "kernel": "6.8.0-1"}
    block = pm_heartbeat._map_versions(raw)
    assert block["verdict"] == "VALID"


def test_resources_probe_raising_is_indeterminate_via_assemble():
    def boom():
        raise OSError("statvfs failed")
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(resources=boom))
    assert envelope["blocks"]["resources"]["verdict"] == "INDETERMINATE"


def test_resources_missing_disk_path_is_null_not_a_crash():
    raw = pm_heartbeat.build_resources_raw(
        read_loadavg=lambda: {"load1": 0.1, "load5": 0.1, "load15": 0.1},
        read_disk_used_pct=lambda path: None if path == "/nonexistent" else 50.0,
        read_uptime=lambda: 1000.0,
        read_temperature=lambda: None,
    )
    block = pm_heartbeat._map_resources(raw)
    assert block["verdict"] == "VALID"
    assert block["data"]["temperature_c"] is None


# ---------------------------------------------------------------------------
# forbidden lying keys — the same recursive key-walk ban as test_heartbeat.py
# ---------------------------------------------------------------------------

FORBIDDEN = (
    "completeness_pct",
    "pending_uploads",
    "pending_count",
    "seconds_detected",
    "mismatched",
    "upload_queue_depth",
)


def walk_keys(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else str(k)
            yield here, k
            yield from walk_keys(v, here)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from walk_keys(v, f"{path}[{i}]")


def test_envelope_carries_no_lying_metric_anywhere_at_any_depth():
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks())
    round_tripped = json.loads(json.dumps(envelope))
    offenders = [
        (where, key)
        for where, key in walk_keys(round_tripped)
        for bad in FORBIDDEN
        if bad in str(key)
    ]
    assert offenders == [], (
        "lying metric(s) present in the PM heartbeat envelope: "
        + ", ".join(f"{k!r} at {w}" for w, k in offenders)
    )


# ---------------------------------------------------------------------------
# spool
# ---------------------------------------------------------------------------

def test_write_tick_is_atomic_no_tmp_left_behind(tmp_path):
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks())
    final = pm_heartbeat.write_tick(envelope, tmp_path)
    assert final.exists()
    assert list(tmp_path.glob("*.tmp")) == []
    written = json.loads(final.read_text())
    assert written["station"] == "b4-pm"


def test_write_tick_prunes_to_newest_48(tmp_path):
    for i in range(52):
        envelope = pm_heartbeat.assemble(
            {"station": "b4-pm", "interval_sec": 300},
            rich_checks(now=lambda i=i: datetime(2026, 8, 20, 0, 0, i % 60,
                                                  tzinfo=timezone.utc)))
        # Force distinct filenames even when the second wraps: patch the
        # emitted_at directly so each tick's stamp is unique.
        envelope["emitted_at"] = f"2026-08-20T00:{i // 60:02d}:{i % 60:02d}Z"
        pm_heartbeat.write_tick(envelope, tmp_path)
    remaining = list(tmp_path.glob("*.json"))
    assert len(remaining) == 48


def test_ship_spool_failure_keeps_all_files(tmp_path):
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks())
    pm_heartbeat.write_tick(envelope, tmp_path)
    before = list(tmp_path.glob("*.json"))
    assert before

    def fail_sftp(batch_text):
        return rr(1, "", "Connection timed out")

    result = pm_heartbeat.ship_spool(tmp_path, "incoming", fail_sftp)
    assert result["ok"] is False
    after = list(tmp_path.glob("*.json"))
    assert len(after) == len(before)


def test_ship_spool_success_deletes_shipped_files(tmp_path):
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks())
    pm_heartbeat.write_tick(envelope, tmp_path)
    assert list(tmp_path.glob("*.json"))

    def ok_sftp(batch_text):
        return rr(0, "", "")

    result = pm_heartbeat.ship_spool(tmp_path, "incoming", ok_sftp)
    assert result["ok"] is True
    assert list(tmp_path.glob("*.json")) == []


def test_ship_spool_empty_spool_is_a_noop():
    def unexpected(_batch):
        raise AssertionError("sftp should not be invoked on an empty spool")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        result = pm_heartbeat.ship_spool(d, "incoming", unexpected)
    assert result == {"shipped": 0, "attempted": 0, "ok": True}


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def test_load_config_missing_required_raises(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('sftp_user = "hamsci-hb"\n')
    with pytest.raises(pm_heartbeat.ConfigError):
        pm_heartbeat.load_config(path)


def test_load_config_applies_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'station = "b4-pm"\nvmid = 100\ndest_host = "wd30.wsprdaemon.org"\n')
    cfg = pm_heartbeat.load_config(path)
    assert cfg["dest_port"] == 22
    assert cfg["sftp_user"] == "hamsci-hb"
    assert cfg["remote_path"] == "incoming"
    assert cfg["interval_sec"] == 300
    assert cfg["expect_cat"] is False
    assert cfg["key_path"] == "/etc/pm-heartbeat/id_ed25519"


# ---------------------------------------------------------------------------
# setup script
# ---------------------------------------------------------------------------

def test_setup_script_exists_executable_and_parses():
    assert SETUP_SCRIPT.exists(), SETUP_SCRIPT
    assert SETUP_SCRIPT.stat().st_mode & 0o111, "must be executable"
    subprocess.run(["bash", "-n", str(SETUP_SCRIPT)], check=True)


def test_setup_script_never_echoes_a_private_key():
    text = SETUP_SCRIPT.read_text()
    # The pubkey (.pub) is fine to print; the private key file's CONTENTS
    # must never be cat'd/echoed.
    assert "cat \"$KEY_PATH\"\n" not in text
    assert 'cat "$KEY_PATH" ' not in text


@pytest.mark.parametrize("cohort,expect_host,expect_port", [
    ("dasi2", "wd30.wsprdaemon.org", "38222"),
    ("public", "wsprdaemon.org", "38222"),
])
def test_print_config_cohort_presets(cohort, expect_host, expect_port):
    proc = subprocess.run(
        ["bash", str(SETUP_SCRIPT), "--station", "b4-pm", "--vmid", "100",
         "--cohort", cohort, "--print-config"],
        capture_output=True, text=True, check=True)
    assert f'dest_host = "{expect_host}"' in proc.stdout
    assert f"dest_port = {expect_port}" in proc.stdout
    assert 'station = "b4-pm"' in proc.stdout
    assert "vmid = 100" in proc.stdout


def test_print_config_explicit_dest_host_overrides_cohort():
    proc = subprocess.run(
        ["bash", str(SETUP_SCRIPT), "--station", "b4-pm", "--vmid", "100",
         "--cohort", "dasi2", "--dest-host", "example.org",
         "--dest-port", "2222", "--print-config"],
        capture_output=True, text=True, check=True)
    assert 'dest_host = "example.org"' in proc.stdout
    assert "dest_port = 2222" in proc.stdout


def test_print_config_no_side_effects_and_no_root_required():
    proc = subprocess.run(
        ["bash", str(SETUP_SCRIPT), "--station", "b4-pm", "--vmid", "100",
         "--cohort", "public", "--print-config"],
        capture_output=True, text=True)
    assert proc.returncode == 0


def test_print_config_expect_cat_flag():
    proc = subprocess.run(
        ["bash", str(SETUP_SCRIPT), "--station", "b4-pm", "--vmid", "100",
         "--cohort", "dasi2", "--expect-cat", "--print-config"],
        capture_output=True, text=True, check=True)
    assert "expect_cat = true" in proc.stdout


def test_missing_destination_without_cohort_or_dest_host_errors():
    proc = subprocess.run(
        ["bash", str(SETUP_SCRIPT), "--station", "b4-pm", "--vmid", "100",
         "--print-config"],
        capture_output=True, text=True)
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# units
# ---------------------------------------------------------------------------

def test_timer_uses_different_randomized_delay_than_the_station_units():
    text = (PROXMOX / "pm-heartbeat.timer").read_text()
    assert "OnCalendar=*:0/5" in text
    assert "RandomizedDelaySec=45" in text
    assert "Persistent=false" in text


def test_service_execstart_points_at_installed_path():
    text = (PROXMOX / "pm-heartbeat.service").read_text()
    assert "/usr/local/sbin/pm-heartbeat.py" in text
    assert "Type=oneshot" in text
