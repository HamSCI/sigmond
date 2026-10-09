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
from datetime import datetime, timedelta, timezone
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


#: What sigmond-rac-switch leaves in /run/sigmond/rac-state for a tunnel that
#: runs with every channel accepted, already split into key and value.
RAC_ON = {
    "state": "on", "wish": "on", "wish_source": "file", "registered": "yes",
    "tunnel": "running", "channels_up": "6", "channels_declared": "6",
    "rac": "507", "gateway": "gw2.wsprdaemon.org", "tier": "wsprdaemon (secure)",
    "changed_at": "2026-10-09T18:22:05Z", "changed_by": "install",
    "checked_at": "2026-10-09T19:00:02Z",
}


def rac(**changes):
    return dict(RAC_ON, **changes)


#: The clock the rac tests read: 58 seconds after RAC_ON's `checked_at`, so a
#: file the switch has just written reads fresh.
RAC_NOW = datetime(2026, 10, 9, 19, 1, 0, tzinfo=timezone.utc)


def rac_text(drop=(), **changes):
    """RAC_ON (with changes) as the switch writes it: one key=value per line."""
    return "".join(f"{k}={v}\n" for k, v in rac(**changes).items() if k not in drop)


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def rich_checks(**overrides):
    """The six top-level check runners a fully-healthy PM would wire.

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
        "rac": lambda: rac(),
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
    assert set(envelope["blocks"]) == {"versions", "doctor", "resources", "rac"}
    assert envelope["role"] == "pm"
    assert envelope["kind"] == heartbeat_schema.KIND
    assert envelope["schema_version"] == heartbeat_schema.SCHEMA_VERSION


def test_envelope_is_json_serializable():
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks())
    round_tripped = json.loads(json.dumps(envelope))
    assert round_tripped["station"] == "b4-pm"


def test_block_names_constant_is_a_strict_subset_in_report_order():
    assert pm_heartbeat.BLOCK_NAMES == ("versions", "doctor", "resources", "rac")
    assert set(pm_heartbeat.BLOCK_NAMES) <= set(heartbeat_schema.ALL_BLOCK_NAMES)
    # `rac` belongs to the host alone: the station's own block set lacks it.
    assert "rac" in heartbeat_schema.HOST_BLOCK_NAMES
    assert "rac" not in heartbeat_schema.BLOCK_NAMES


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
# rac block — the operator's switch for remote access
# (sigmond tasks/plan-remote-access.md section 4)
# ---------------------------------------------------------------------------

RAC_CASES = [
    (rac(), "VALID", "on: 6 of 6 channels"),
    (rac(channels_up="4"), "INCONCLUSIVE", "on, short of channels: 4 of 6"),
    (rac(state="off", wish="off", tunnel="stopped", channels_up="0", changed_by="vm"),
     "VALID", "off by the operator since 2026-10-09T18:22:05Z"),
    (rac(state="off", wish="off", wish_source="units", registered="no", tunnel="stopped",
         channels_up="0", channels_declared="0", changed_at="", changed_by=""),
     "VALID", "off: this station never registered for remote access"),
    (rac(state="off", wish="off"), "INCONCLUSIVE",
     "off by the operator since 2026-10-09T18:22:05Z, yet the tunnel service runs"),
    (rac(state="down", registered="no", tunnel="stopped", channels_up="0", channels_declared="0"),
     "INVALID", "wanted on, not up: not registered yet (the host retries)"),
    (rac(state="down", tunnel="stopped", channels_up="0"),
     "INVALID", "wanted on, not up: the tunnel service does not run"),
    (rac(state="down", channels_up="0"),
     "INVALID", "wanted on, not up: the gateway accepted none of 6 channels"),
    (rac(state="sideways"), "INDETERMINATE", "the switch reported no usable state ('sideways')"),
]


@pytest.mark.parametrize("raw,verdict,reason", RAC_CASES,
                         ids=[f"{c[1]}-{i}" for i, c in enumerate(RAC_CASES)])
def test_rac_block_verdicts(raw, verdict, reason):
    block = pm_heartbeat._map_rac(raw)
    assert (block["verdict"], block["reason"]) == (verdict, reason)
    assert set(block["data"]) == {"wish", "wish_source", "registered", "tunnel", "channels_up",
                                  "channels_declared", "since", "by"}


def test_the_blocks_data_carries_typed_values_not_the_files_text():
    data = pm_heartbeat._map_rac(RAC_CASES[0][0])["data"]       # the VALID, all-up case
    assert data["registered"] is True                            # "yes" in the file
    assert type(data["channels_up"]) is int
    assert type(data["channels_declared"]) is int
    assert (data["channels_up"], data["channels_declared"]) == (6, 6)


# A count that does not parse is unknown, never a measured zero: a zero would
# read "the gateway accepted none" (INVALID) or "short of channels", and
# either states a measurement the switch never made.
GARBLED = [
    (rac(channels_up="lots", channels_declared=""), "channels_up='lots' is not a count"),
    (rac(channels_up=""), "channels_up='' is not a count"),
    (rac(channels_up="-2"), "channels_up='-2' is not a count"),
    ({k: v for k, v in RAC_ON.items() if k != "channels_up"}, "channels_up is missing"),
    (rac(channels_declared=""), "channels_declared='' is not a count"),
    (rac(channels_declared="six"), "channels_declared='six' is not a count"),
    (rac(state="off", wish="off", tunnel="stopped", channels_up="0", channels_declared="?"),
     "channels_declared='?' is not a count"),                    # an operator's off included
    (rac(channels_declared="9" * 59 + "x"),
     "channels_declared='" + "9" * 40 + "' is not a count"),
]


@pytest.mark.parametrize("raw,reason_has", GARBLED,
                         ids=[f"{c[1].split()[0].split('=')[0]}-{i}" for i, c in enumerate(GARBLED)])
def test_a_garbled_channel_count_reads_indeterminate_never_zero(raw, reason_has):
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(rac=lambda: raw))
    block = envelope["blocks"]["rac"]
    assert block["verdict"] == "INDETERMINATE"
    assert reason_has in block["reason"]
    assert "9" * 41 not in block["reason"]                       # the value is cut at 40
    assert envelope["rollup"]["verdict"] != "VALID"


def test_an_operators_off_never_worsens_the_hosts_rollup():
    off = rac(state="off", wish="off", tunnel="stopped", channels_up="0", changed_by="vm")
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(rac=lambda: off))
    assert envelope["blocks"]["rac"]["verdict"] == "VALID"
    assert envelope["blocks"]["rac"]["data"]["wish"] == "off"
    assert envelope["blocks"]["rac"]["data"]["by"] == "vm"
    assert envelope["rollup"]["verdict"] == "VALID"
    assert heartbeat_schema.validate(envelope) == []


def test_wanted_on_and_not_up_turns_the_hosts_rollup_invalid():
    down = rac(state="down", tunnel="stopped", channels_up="0")
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(rac=lambda: down))
    assert envelope["rollup"]["verdict"] == "INVALID"
    assert envelope["rollup"]["reason"].startswith("rac: wanted on, not up")


def test_a_host_without_the_switch_script_claims_no_rac_block():
    # A host installed before v3.71, or one pm-align has only half brought up.
    raw = pm_heartbeat.build_rac_raw(lambda: False, lambda: 1 / 0, lambda: 1 / 0)
    assert raw is None
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(rac=lambda: None))
    assert set(envelope["blocks"]) == {"versions", "doctor", "resources"}
    assert envelope["rollup"]["verdict"] == "VALID"
    assert heartbeat_schema.validate(envelope) == []


def test_build_rac_raw_reads_the_file_the_switch_leaves():
    text = rac_text()
    for rc in (0, 3):                              # 3: wanted on, not up; still an answer
        raw = pm_heartbeat.build_rac_raw(lambda: True, lambda: rr(rc), lambda: text, now=RAC_NOW)
        assert raw == RAC_ON
    assert raw["tier"] == "wsprdaemon (secure)"     # a value with a space and brackets


@pytest.mark.parametrize("status,state_text,needle", [
    (rr(2, "", "sigmond-rac-switch: refused."), "state=on\n", "exited 2"),
    (rr(127, "", "not found"), "state=on\n", "exited 127"),
    (rr(0), "", "names no state"),
    (rr(0), "nonsense without an equals sign\n", "names no state"),
], ids=["refused", "not-runnable", "empty-file", "no-state-line"])
def test_a_switch_that_gives_no_answer_reads_indeterminate_never_valid(status, state_text, needle):
    check = lambda: pm_heartbeat.build_rac_raw(lambda: True, lambda: status, lambda: state_text,
                                               now=RAC_NOW)
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(rac=check))
    block = envelope["blocks"]["rac"]
    assert block["verdict"] == "INDETERMINATE" and needle in block["reason"]
    assert envelope["rollup"]["verdict"] != "VALID"


def test_a_state_file_that_cannot_be_read_reads_indeterminate():
    def unreadable():
        raise FileNotFoundError("/run/sigmond/rac-state")
    check = lambda: pm_heartbeat.build_rac_raw(lambda: True, lambda: rr(0), unreadable, now=RAC_NOW)
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(rac=check))
    assert envelope["blocks"]["rac"]["verdict"] == "INDETERMINATE"


# --- an old state file must not read as the present -----------------------
# The switch's write_state() swallows its own failure, so `status` can exit 0
# with /run/sigmond unwritable and leave an OLD file in place.  The file's
# `checked_at` is the only witness that the answer is current.

def _check_on(text, now=RAC_NOW):
    return lambda: pm_heartbeat.build_rac_raw(
        lambda: True, lambda: rr(0), lambda: text, now=now)


@pytest.mark.parametrize("text,needle", [
    (rac_text(checked_at=stamp(RAC_NOW - timedelta(minutes=10))), "is stale"),
    (rac_text(checked_at=stamp(RAC_NOW + timedelta(minutes=10))), "is stale"),
    (rac_text(checked_at=stamp(RAC_NOW - timedelta(seconds=301))), "is stale"),
    (rac_text(drop=("checked_at",)), "carries no usable checked_at"),
    (rac_text(checked_at=""), "carries no usable checked_at"),
    (rac_text(checked_at="last tuesday"), "carries no usable checked_at"),
], ids=["ten-minutes-back", "ten-minutes-ahead", "301-seconds-back",
        "no-checked_at", "empty-checked_at", "unparsable-checked_at"])
def test_an_old_state_file_reads_indeterminate_though_its_state_says_on(text, needle):
    assert "state=on" in text
    envelope = pm_heartbeat.assemble(CONFIG, rich_checks(rac=_check_on(text)))
    block = envelope["blocks"]["rac"]
    assert block["verdict"] == "INDETERMINATE"
    assert needle in block["reason"]
    assert pm_heartbeat.RAC_STATE_FILE in block["reason"]
    assert "stale" in block["reason"]
    assert envelope["rollup"]["verdict"] != "VALID"


@pytest.mark.parametrize("age", [timedelta(0), timedelta(minutes=1), timedelta(seconds=299),
                                 -timedelta(minutes=1)],
                         ids=["now", "one-minute-old", "299-seconds-old", "one-minute-ahead"])
def test_a_state_file_within_five_minutes_reads_as_its_state_says(age):
    text = rac_text(state="down", tunnel="stopped", channels_up="0",
                    checked_at=stamp(RAC_NOW - age))
    block = pm_heartbeat.assemble(CONFIG, rich_checks(rac=_check_on(text)))["blocks"]["rac"]
    assert block["verdict"] == "INVALID"
    assert block["reason"] == "wanted on, not up: the tunnel service does not run"


# --- the production wrappers and their wiring ------------------------------
# A fake switch in tmp_path stands in for /usr/local/sbin/sigmond-rac-switch:
# it records its argv and leaves a state file, as the real one does.

def _fake_switch(tmp_path, monkeypatch, rc=0, leaves=None, executable=True):
    argv_file, state_file = tmp_path / "argv", tmp_path / "rac-state"
    script = tmp_path / "sigmond-rac-switch"
    lines = ["#!/bin/sh", f"printf '%s\\n' \"$@\" >> '{argv_file}'"]
    if leaves is not None:
        lines.append(f"cat > '{state_file}' <<'EOF'\n{leaves}EOF")
    lines.append(f"exit {rc}")
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o755 if executable else 0o644)
    monkeypatch.setattr(pm_heartbeat, "RAC_SWITCH", str(script))
    monkeypatch.setattr(pm_heartbeat, "RAC_STATE_FILE", str(state_file))
    return argv_file, state_file


def _production_rac_block(check=None):
    """The `rac` check exactly as main() wires it, run through assemble()."""
    check = check or pm_heartbeat.build_production_checks(
        {"vmid": 100, "expect_cat": False})["rac"]
    return pm_heartbeat.assemble(CONFIG, rich_checks(rac=check))


def _now_text(**changes):
    return rac_text(checked_at=stamp(datetime.now(timezone.utc)), **changes)


def test_the_production_check_asks_the_switch_for_status_and_reads_its_file(tmp_path, monkeypatch):
    argv_file, _ = _fake_switch(tmp_path, monkeypatch, rc=0, leaves=_now_text())
    envelope = _production_rac_block()
    block = envelope["blocks"]["rac"]
    assert (block["verdict"], block["reason"]) == ("VALID", "on: 6 of 6 channels")
    assert argv_file.read_text().splitlines() == ["status"]      # nothing else, ever


def test_a_switch_that_exits_3_still_gives_its_state_through_the_production_check(
        tmp_path, monkeypatch):
    argv_file, _ = _fake_switch(
        tmp_path, monkeypatch, rc=3,
        leaves=_now_text(state="down", tunnel="stopped", channels_up="0"))
    block = _production_rac_block()["blocks"]["rac"]
    assert (block["verdict"], block["reason"]) == (
        "INVALID", "wanted on, not up: the tunnel service does not run")
    assert argv_file.read_text().splitlines() == ["status"]


def test_a_switch_that_exits_1_reads_indeterminate_and_its_file_is_never_read(
        tmp_path, monkeypatch):
    argv_file, state_file = _fake_switch(tmp_path, monkeypatch, rc=1, leaves=None)
    state_file.write_text(_now_text())                  # state=on: would read VALID if trusted
    reads = []
    real_read = pm_heartbeat._read_rac_state
    monkeypatch.setattr(pm_heartbeat, "_read_rac_state",
                        lambda: (reads.append(1), real_read())[1])
    envelope = _production_rac_block()
    block = envelope["blocks"]["rac"]
    assert block["verdict"] == "INDETERMINATE" and "exited 1" in block["reason"]
    assert reads == []
    assert argv_file.read_text().splitlines() == ["status"]
    assert envelope["rollup"]["verdict"] != "VALID"


def test_a_switch_without_the_executable_bit_claims_no_block_and_never_runs(
        tmp_path, monkeypatch):
    argv_file, _ = _fake_switch(tmp_path, monkeypatch, rc=0, leaves=_now_text(),
                                executable=False)
    envelope = _production_rac_block()
    assert "rac" not in envelope["blocks"]
    assert not argv_file.exists()
    assert heartbeat_schema.validate(envelope) == []


def test_the_emitter_reads_the_keys_the_switch_writes():
    # The switch script ships inside the wizard; its write_state() names the
    # keys.  A renamed key there would turn this block INDETERMINATE fleet-wide.
    wizard = (PROXMOX / "sigmond-wizard.sh").read_text()
    for key in ("state", "wish", "wish_source", "registered", "tunnel", "channels_up",
                "channels_declared", "changed_at", "changed_by"):
        assert f"printf '{key}=%s\\n'" in wizard, key
    assert f"RUN_DIR=\"${{SIGMOND_RAC_RUN_DIR:-{pm_heartbeat.RAC_STATE_FILE.rsplit('/', 1)[0]}}}\"" in wizard
    assert pm_heartbeat.RAC_SWITCH == "/usr/local/sbin/sigmond-rac-switch"
    # The wizard spells both paths on lines of their own; the emitter must name
    # the same two.  One moved path would make every host claim no block at all.
    assert f"RAC_STATE_FILE={pm_heartbeat.RAC_STATE_FILE}" in wizard
    assert f"RAC_SWITCH={pm_heartbeat.RAC_SWITCH}" in wizard


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
