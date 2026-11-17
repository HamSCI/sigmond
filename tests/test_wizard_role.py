"""The wizard's station role question, and its refusal of "radiod only".

A radiod-only station runs radiod and nothing else: it publishes the SDR's
streams on the LAN for a decoder host elsewhere.  On the appliance that is
REFUSED for now: the decoder VM has only the host-only link, so its radiod
cannot publish to a decoder on another machine.  Chosen at the question, by
--role or by SIGMOND_ROLE, "radiod only" ends the wizard non-zero, pointing at
docs/operator/radiod-only.md's bare-metal path, before anything is written.

The role plumbing stays in the wizard, unreachable past that refusal, for when
the VM can publish: no wsprnet reporter id or PSWS ids, `profile =
"radiod-only"` under [station] in site-profile.toml, the VM gate, the role
records, the summary line.  The unit tests of those pieces below keep them
from rotting; the flow tests are refusal tests.

A full-role run must be byte-identical to the wizard before the role existed:
the GOLDEN_* strings below were captured from that wizard.
"""
import os
import re
import subprocess
import textwrap
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"
FIRSTRUN = REPO / "bin" / "sigmond-firstrun-bringup"
LINES = WIZARD.read_text().splitlines(True)

ROLE_NOTE = "Radiod-only: radiod publishes on the LAN; decode it from your decoder host"


def shell_func(name: str) -> str:
    out, depth, keep = [], 0, False
    for ln in LINES:
        if not keep and (ln.startswith(f"{name}() {{") or ln.startswith(f"{name}(){{")):
            keep = True
        if keep:
            out.append(ln)
            depth += ln.count("{") - ln.count("}")
            if depth <= 0:
                break
    assert out, f"{name} not found in the wizard"
    return "".join(out)


def block(first: str, last: str) -> str:
    """Top-level wizard text from the line `first` through `last` and the `)`
    that closes its command substitution."""
    i = next(n for n, l in enumerate(LINES) if l.rstrip("\n") == first)
    j = next(n for n in range(i, len(LINES)) if LINES[n].rstrip("\n") == last)
    assert LINES[j + 1].rstrip("\n") == ")"
    return "".join(LINES[i:j + 2])


def bash(script: str, stdin: str = "", env=None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], input=stdin, capture_output=True,
                          text=True, timeout=30, env=env)


# -- site-profile.toml ---------------------------------------------------------

PROFILE_PRE = r'''
set -u
uploads_toml(){ printf '[uploads]\nenabled = false\nmode = "discard"\n'; }
date(){ echo 2026-11-01T00:00Z; }
CALLSIGN=AC0G; GRID=EM38ww; ANTENNA="T2FD 80 ft"; REPORTER=AC0G/B4; VMNAME=AC0G-B4
'''
PSWS = 'PSWS_ID=S000123; PSWS_GRAPE=172; PSWS_MAG=84\n'
NO_PSWS = 'PSWS_ID=""; PSWS_GRAPE=""; PSWS_MAG=""\n'


def profile(role: str, psws: str = PSWS) -> str:
    r = bash(PROFILE_PRE + f"STATION_ROLE={role}\n" + psws
             + block('PSWS_TOML=""', "PEOF") + 'printf %s "$PROFILE"')
    assert r.returncode == 0, r.stderr
    return r.stdout


GOLDEN_PROFILE_PSWS = '# Written by the Sigmond appliance first-boot wizard 2026-11-01T00:00Z\n[station]\ncallsign    = "AC0G"\ngrid_square = "EM38ww"\ndescription = "T2FD 80 ft"\n\n[psws]\nenabled    = true\nstation_id = "S000123"\n\n[psws.instruments]\n"hf-timestd"   = "172"\n"mag-recorder" = "84"\n\n[reporters]\nreporter_id = "AC0G/B4"\n[uploads]\nenabled = false\nmode = "discard"\n\n[host]\nhostname = "AC0G-B4"'

GOLDEN_PROFILE_NO_PSWS = '# Written by the Sigmond appliance first-boot wizard 2026-11-01T00:00Z\n[station]\ncallsign    = "AC0G"\ngrid_square = "EM38ww"\ndescription = "T2FD 80 ft"\n\n[reporters]\nreporter_id = "AC0G/B4"\n[uploads]\nenabled = false\nmode = "discard"\n\n[host]\nhostname = "AC0G-B4"'

GOLDEN_SUMMARY_FRESH = "──────────────────────────────────────────────────────\n Sigmond station configured: AC0G/B4 @ EM38ww\n Host (Proxmox):\n   login:   root — password set at install (image default:\n            hamsci-sigmond — CHANGE IT: run 'passwd')\n   ssh:     ssh root@192.168.1.10\n   web GUI: https://192.168.1.10:8006\n Decoder VM 100:\n   IP:      192.168.1.11\n   login:   sigmond or hamsci — SAME password as this host's root\n            (ONE password per appliance; remote root ssh is disabled)\n   shell:   sigmond-vm   ← run this on the host: finds the VM's\n            current IP and logs you in as sigmond (keyed)\n   ssh:     ssh sigmond@192.168.1.11   (from other machines;\n            sigmond-vm --ip prints the current address)\n   console: qm terminal 100  (Ctrl+O exits; sigmond or root)\n SDR/radiod: radiod ACTIVE\n RAC:       RAC #221\n Switch:    paired\n PSWS:      station S000123\n Site sink switch: upload — spots and noise go to wsprdaemon.org\n            from the first cycle.  To keep this\n            station quiet, run in the VM: smd sink off\n Rerun wizard:  sigmond-setup --reconfigure\n This summary is saved in /root/sigmond-setup-summary.txt\n──────────────────────────────────────────────────────"

GOLDEN_SUMMARY_CONFIGURED = "──────────────────────────────────────────────────────\n Sigmond station configured: AC0G/B4 @ EM38ww\n Host (Proxmox):\n   login:   root — password set at install (image default:\n            hamsci-sigmond — CHANGE IT: run 'passwd')\n   ssh:     ssh root@192.168.1.10\n   web GUI: https://192.168.1.10:8006\n Decoder VM 100:\n   IP:      192.168.1.11\n   login:   sigmond or hamsci — SAME password as this host's root\n            (ONE password per appliance; remote root ssh is disabled)\n   shell:   sigmond-vm   ← run this on the host: finds the VM's\n            current IP and logs you in as sigmond (keyed)\n   ssh:     ssh sigmond@192.168.1.11   (from other machines;\n            sigmond-vm --ip prints the current address)\n   console: qm terminal 100  (Ctrl+O exits; sigmond or root)\n SDR/radiod: radiod ACTIVE\n RAC:       RAC #221\n Switch:    paired\n PSWS:      station S000123\n\n Rerun wizard:  sigmond-setup --reconfigure\n This summary is saved in /root/sigmond-setup-summary.txt\n──────────────────────────────────────────────────────"

@pytest.mark.parametrize("psws,golden", [(PSWS, GOLDEN_PROFILE_PSWS),
                                         (NO_PSWS, GOLDEN_PROFILE_NO_PSWS)],
                         ids=["psws", "no-psws"])
def test_a_full_station_writes_todays_site_profile_byte_for_byte(psws, golden):
    assert profile("full", psws) == golden


def test_a_radiod_only_station_declares_its_profile_under_station():
    text = profile("radiod-only", NO_PSWS)
    doc = tomllib.loads(text)
    assert doc["station"]["profile"] == "radiod-only"
    assert doc["station"]["callsign"] == "AC0G"
    assert doc["station"]["grid_square"] == "EM38ww"
    assert doc["station"]["description"] == "T2FD 80 ft"
    assert doc["host"]["hostname"] == "AC0G-B4"
    # Nothing a decoder reads: no reporter id was asked, and no PSWS ids.
    assert "reporters" not in doc
    assert "psws" not in doc
    assert text.isascii()


def test_a_radiod_only_profile_drops_psws_ids_left_from_an_earlier_answer():
    doc = tomllib.loads(profile("radiod-only", PSWS))
    assert "psws" not in doc


def test_first_run_bringup_reads_the_radiod_only_profile(tmp_path):
    # The same sed the first-run unit runs, against the file the wizard writes.
    m = re.search(r"PROFILE=\$\(sed -n '([^']*)'", FIRSTRUN.read_text())
    assert m, "first-run profile sed not found"
    f = tmp_path / "site-profile.toml"
    f.write_text(profile("radiod-only", NO_PSWS))
    out = subprocess.run(["sed", "-n", m.group(1), str(f)], capture_output=True,
                         text=True, timeout=30).stdout.split()
    assert out == ["radiod-only"]
    f.write_text(profile("full"))
    assert subprocess.run(["sed", "-n", m.group(1), str(f)], capture_output=True,
                          text=True, timeout=30).stdout.strip() == ""


# -- the summary ---------------------------------------------------------------

SUMMARY_PRE = "set -u\n_COLOUR_OK=0\n" + shell_func("_hl") + r'''
ipurl(){ case "${1:-}" in *:*) printf '[%s]\n' "$1";; *) printf '%s\n' "${1:-}";; esac; }
REPORTER=AC0G/B4; GRID=EM38ww; HOSTIP=192.168.1.10; VMID=100; VMIP=192.168.1.11; SSH_STATE=keyed
RADIOD_STATE="radiod ACTIVE"; RAC_STATE="RAC #221"; PAIR_STATE=paired; PSWS_STATE="station S000123"
'''


def summary(role: str, uploads: str) -> str:
    r = bash(SUMMARY_PRE + f"STATION_ROLE={role}\nUPLOADS_TOML={uploads}\n"
             + block('SINK_NOTE=""', "SEOF") + 'printf %s "$SUMMARY"')
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.mark.parametrize("uploads,golden", [("x", GOLDEN_SUMMARY_FRESH),
                                            ('""', GOLDEN_SUMMARY_CONFIGURED)],
                         ids=["fresh", "configured"])
def test_a_full_station_prints_todays_summary_byte_for_byte(uploads, golden):
    assert summary("full", uploads) == golden


def test_a_radiod_only_summary_says_where_decoding_happens():
    out = summary("radiod-only", "x")
    assert ROLE_NOTE in out
    assert ROLE_NOTE.isascii()
    # Nothing to upload, so no word about the sink switch.
    assert "Site sink switch" not in out


# -- the question sequence -----------------------------------------------------

LEAVES = ("preflight_devices", "ask_role", "ask_reporter", "ask_station_call",
          "ask_grid", "ask_antenna", "ask_wifi", "require_uplink", "ask_rac",
          "ask_psws", "ask_ntp", "ask_names")


def question_sequence() -> str:
    """The wizard's top-level question calls, preflight through ask_names."""
    i = next(n for n, l in enumerate(LINES) if l == "preflight_devices\n")
    j = next(n for n in range(i, len(LINES)) if LINES[n] == "ask_names\n")
    return "".join(LINES[i:j + 1])


REFUSAL_WORDS = ("not available on the appliance", "host-only link",
                 "docs/operator/radiod-only.md", "Bare metal", "Nothing was written")


def run_questions(role: str) -> subprocess.CompletedProcess:
    """The question sequence with every leaf stubbed; ask_role answers
    `role`.  The appliance refusal is the real one."""
    leaves = tuple(n for n in LEAVES if n != "ask_role")
    stubs = "".join(f'{n}(){{ echo {n}; }}\n' for n in leaves + ("radiod_only_vm_gate",))
    stubs += f'ask_role(){{ echo ask_role; STATION_ROLE={role}; }}\n'
    script = ("set -u\nsay(){ echo \"[wizard] $*\"; }\n" + stubs
              + shell_func("station_is_radiod_only") + shell_func("ask_station_id")
              + shell_func("ask_psws_for_role")
              + shell_func("radiod_only_appliance_refusal")
              + "STATION_ROLE=full\n" + question_sequence()
              + 'echo "PSWS=[${PSWS_ID-unset}|${PSWS_GRAPE-unset}|${PSWS_MAG-unset}]"\n')
    return bash(script)


def asked(role: str) -> list[str]:
    r = run_questions(role)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.split()


def test_a_full_station_asks_todays_questions_after_the_role():
    assert asked("full")[:-1] == [
        "preflight_devices", "ask_role", "radiod_only_vm_gate", "ask_reporter", "ask_grid", "ask_antenna",
        "ask_wifi", "require_uplink", "ask_rac", "ask_psws", "ask_ntp", "ask_names"]


def test_choosing_radiod_only_at_the_question_is_refused_on_the_appliance():
    r = run_questions("radiod-only")
    assert r.returncode != 0
    words = [w for w in r.stdout.split() if not w.startswith("[")]
    # Nothing after the role question runs: no VM gate, no further question.
    asked_here = [w for w in words if w in LEAVES + ("radiod_only_vm_gate",)]
    assert asked_here == ["preflight_devices", "ask_role"], r.stdout
    for w in REFUSAL_WORDS:
        assert w in r.stdout, (w, r.stdout)


def test_the_station_call_question_names_no_wsprnet_and_sets_the_callsign():
    r = bash("set -u\nrd(){ read \"$@\"; }\n" + shell_func("ask_station_call")
             + 'ask_station_call; echo "R=$REPORTER C=$CALLSIGN"', stdin="!!\nac0g/b4\n")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == "R=AC0G/B4 C=AC0G"
    assert "wsprnet" not in shell_func("ask_station_call").lower()


# -- choosing the role ---------------------------------------------------------

def _top_refusal() -> str:
    """The wizard's line right after `station_role_check || exit 2`."""
    i = next(n for n, l in enumerate(LINES) if l == "station_role_check || exit 2\n")
    j = next(n for n in range(i + 1, len(LINES))
             if "radiod_only_appliance_refusal" in LINES[n])
    return LINES[j]


TOP_REFUSAL = _top_refusal()


def role_init(tmp_path, *args, env_role=None, persisted=None):
    mark = tmp_path / "mark"
    mark.mkdir(exist_ok=True)
    if persisted is not None:
        (mark / "station-role").write_text(persisted + "\n")
    env = {k: v for k, v in os.environ.items() if k != "SIGMOND_ROLE"}
    if env_role is not None:
        env["SIGMOND_ROLE"] = env_role
    quoted = " ".join(f"'{a}'" for a in args)
    # The configure flow's own top-level lines: check, then refuse a
    # radiod-only role given up front.
    r = bash(f"set -u\nMARK_DIR={mark}\nsay(){{ echo \"[wizard] $*\"; }}\n"
             + shell_func("station_role_init") + shell_func("station_role_check")
             + shell_func("radiod_only_appliance_refusal")
             + f"station_role_init {quoted}\n"
             + "( station_role_check || exit 2\n" + TOP_REFUSAL
             + 'echo "rc=0 role=${STATION_ROLE:-} ask=${STATION_ROLE_ASK:-}" )\n'
             + "rc=$?\n"
             + '[ "$rc" = 0 ] || { station_role_check >/dev/null; '
             + 'echo "rc=$rc role=${STATION_ROLE:-} ask=${STATION_ROLE_ASK:-}"; }\n'
             + 'printf "arg=%s\\n" ${WIZ_ARGS[@]+"${WIZ_ARGS[@]}"}\n', env=env)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    state = next(l for l in lines if l.startswith("rc="))
    return state, [l[4:] for l in lines if l.startswith("arg=")], r.stdout


def test_no_choice_anywhere_defaults_to_full_and_asks(tmp_path):
    state, args, _ = role_init(tmp_path, "--reconfigure")
    assert state == "rc=0 role=full ask=1"
    assert args == ["--reconfigure"]


@pytest.mark.parametrize("argv", [("--role", "radiod-only", "--reconfigure"),
                                  ("--reconfigure", "--role=radiod-only")])
def test_the_role_flag_radiod_only_is_refused_and_leaves_the_other_args(tmp_path, argv):
    state, args, out = role_init(tmp_path, *argv)
    assert state == "rc=1 role=radiod-only ask=0"
    assert args == ["--reconfigure"]
    for w in REFUSAL_WORDS:
        assert w in out, (w, out)


def test_the_role_flag_full_answers_the_question(tmp_path):
    state, args, out = role_init(tmp_path, "--role", "full", "--reconfigure")
    assert state == "rc=0 role=full ask=0"
    assert args == ["--reconfigure"]
    assert "refused" not in out


def test_sigmond_role_radiod_only_is_refused_and_the_flag_beats_it(tmp_path):
    state, _, out = role_init(tmp_path, env_role="radiod-only")
    assert state == "rc=1 role=radiod-only ask=0"
    assert "Nothing was written" in out
    state, _, _ = role_init(tmp_path, "--role", "full", env_role="radiod-only")
    assert state == "rc=0 role=full ask=0"


def test_reconfigure_keeps_the_role_chosen_last_time_as_the_default(tmp_path):
    # Only the default: nothing is refused until the question is answered.
    state, _, _ = role_init(tmp_path, "--reconfigure", persisted="radiod-only")
    assert state == "rc=0 role=radiod-only ask=1"
    state, _, _ = role_init(tmp_path, "--reconfigure", persisted="garbage")
    assert state == "rc=0 role=full ask=1"


def test_the_refusal_comes_before_anything_is_written_or_the_vm_is_touched():
    text = WIZARD.read_text()
    top = text.index("radiod_only_appliance_refusal || exit 1")
    asked_ = text.index("ask_role\nradiod_only_appliance_refusal || exit 1\n")
    assert text.index("station_role_check || exit 2") < top
    # The up-front refusal precedes the VM wait (which may `qm start` it).
    assert top < text.index("# >>> agent_wait")
    for write in ("> /etc/sigmond/site-profile.toml.new", "> /etc/sigmond/station-role",
                  '> "$MARK_DIR/station-role"', '> "$CONF_MARK"'):
        assert asked_ < text.index(write), write
    # And it precedes the VM gate, which would otherwise ask the VM.
    assert asked_ < text.index("\nradiod_only_vm_gate || exit 1\n")


def test_the_refusal_passes_a_full_station_silently():
    r = bash("set -u\nsay(){ echo \"[wizard] $*\"; }\n"
             + shell_func("radiod_only_appliance_refusal")
             + "STATION_ROLE=full; radiod_only_appliance_refusal; echo rc=$?")
    assert r.stdout == "rc=0\n"


@pytest.mark.parametrize("argv,env_role", [(("--role", "decoder"), None),
                                           (("--role",), None),
                                           ((), "both")])
def test_an_unknown_role_is_refused(tmp_path, argv, env_role):
    state, _, out = role_init(tmp_path, *argv, env_role=env_role)
    assert state.startswith("rc=2 ")
    assert "full or radiod-only" in out


@pytest.mark.parametrize("answer,default,want", [
    ("2\n", "full", "radiod-only"), ("radiod-only\n", "full", "radiod-only"),
    ("1\n", "radiod-only", "full"), ("\n", "radiod-only", "radiod-only"),
    ("\n", "full", "full"), ("x\n2\n", "full", "radiod-only")])
def test_the_role_question(answer, default, want):
    r = bash("set -u\nrd(){ read \"$@\"; }\n" + shell_func("ask_role")
             + f'STATION_ROLE={default}; STATION_ROLE_ASK=1; ask_role; echo "role=$STATION_ROLE"',
             stdin=answer)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == f"role={want}"


def test_a_role_given_up_front_is_not_asked_again():
    r = bash("set -u\nrd(){ echo READ; read \"$@\"; }\n" + shell_func("ask_role")
             + 'STATION_ROLE=radiod-only; STATION_ROLE_ASK=0; ask_role; echo "role=$STATION_ROLE"')
    assert "READ" not in r.stdout
    assert r.stdout.strip().splitlines()[-1] == "role=radiod-only"


# -- where the role is recorded ------------------------------------------------

def logical_lines() -> list[str]:
    out, cur = [], ""
    for ln in LINES:
        s = ln.strip()
        if not cur and (not s or s.startswith("#")):
            continue
        if s.endswith("\\"):
            cur += s[:-1] + " "
            continue
        out.append(cur + s)
        cur = ""
    return out


def test_the_vm_role_file_is_written_through_gexec_in_ascii():
    hits = [l for l in logical_lines() if "/etc/sigmond/station-role" in l]
    assert hits, "the wizard never writes the VM's station-role"
    for l in hits:
        assert "qm guest exec" not in l, l
    writes = [l for l in hits if l.startswith("gexec ") and "> /etc/sigmond/station-role" in l]
    assert writes, hits
    for l in writes:
        assert l.split('"')[1].isascii(), l


def test_the_role_file_is_written_after_the_site_profile_installs():
    text = WIZARD.read_text()
    assert text.index("smd config profile-install /etc/sigmond/site-profile.toml.new") \
        < text.index("> /etc/sigmond/station-role")


def test_the_host_keeps_the_role_beside_rac_profile_for_reconfigure():
    text = WIZARD.read_text()
    assert 'printf \'%s\\n\' "$STATION_ROLE" > "$MARK_DIR/station-role"' in text
    assert text.index('> "$MARK_DIR/station-role"') > text.index('> "$CONF_MARK"')


def test_the_older_template_fallback_brings_up_the_chosen_profile():
    assert not any("smd bringup dasi2" in l for l in logical_lines())
    assert any("smd bringup $BRINGUP_PROFILE --non-interactive" in l for l in logical_lines())


def test_the_wizard_parses():
    assert subprocess.run(["bash", "-n", str(WIZARD)], timeout=30).returncode == 0


# -- fix round 1 ---------------------------------------------------------------

def gate(role: str, vm_out: str | None):
    """Run radiod_only_vm_gate with guest_out answering `vm_out` (None: the
    agent is never expected to be asked)."""
    calls = "guest_out(){ echo CALLED >&2; printf '%s' \"$VM_OUT\"; }\n"
    env = dict(os.environ, VM_OUT=vm_out or "")
    r = bash("set -u\nsay(){ echo \"[wizard] $*\"; }\n" + calls
             + shell_func("station_is_radiod_only") + shell_func("radiod_only_probe_cmd")
             + shell_func("radiod_only_vm_gate")
             + f"STATION_ROLE={role}\nradiod_only_vm_gate\n", env=env)
    return r


def test_switching_to_radiod_only_is_refused_while_decoders_are_enabled():
    r = gate("radiod-only", "TOPOLOGY-OK\nka9q-radio\nwspr-recorder\nka9q-web\npsk-recorder\n")
    assert r.returncode != 0
    assert "wspr-recorder" in r.stdout and "psk-recorder" in r.stdout
    assert "smd disable wspr-recorder psk-recorder" in r.stdout
    assert "Nothing was written" in r.stdout


@pytest.mark.parametrize("vm", ["TOPOLOGY-OK\n",
                                "TOPOLOGY-OK\nka9q-radio\nigmp-querier\nka9q-web\n"],
                         ids=["fresh-vm", "radiod-only-set"])
def test_radiod_only_is_allowed_when_nothing_else_is_enabled(vm):
    r = gate("radiod-only", vm)
    assert r.returncode == 0, r.stdout


def test_an_unreadable_vm_topology_refuses_rather_than_guesses():
    r = gate("radiod-only", "")
    assert r.returncode != 0
    assert "Nothing was written" in r.stdout


def test_a_full_station_never_asks_the_vm():
    r = gate("full", None)
    assert r.returncode == 0
    assert "CALLED" not in r.stderr


def probe_cmd() -> str:
    r = bash(shell_func("radiod_only_probe_cmd") + "radiod_only_probe_cmd")
    assert r.returncode == 0 and "TOPOLOGY-OK" in r.stdout, r.stderr
    return r.stdout


def test_the_probe_reads_enabled_components_from_the_topology(tmp_path):
    topo = tmp_path / "topology.toml"
    cmd = probe_cmd().replace("/etc/sigmond/topology.toml", str(topo))
    assert probe_cmd().isascii()
    # Absent file: a fresh VM, nothing enabled.
    out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=30).stdout
    assert out.split() == ["TOPOLOGY-OK"]
    topo.write_text('[component.radiod]\nenabled = true\n\n[component.wspr-recorder]\n'
                    'enabled = true\n\n[component.psk-recorder]\nenabled = false\n')
    out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=30).stdout
    assert out.split() == ["TOPOLOGY-OK", "ka9q-radio", "wspr-recorder"]
    topo.write_text("[component.radiod\n")          # broken: no OK line
    out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=30).stdout
    assert "TOPOLOGY-OK" not in out


def test_the_gate_runs_right_after_the_role_and_before_any_vm_write():
    seq = question_sequence()
    assert ("ask_role\nradiod_only_appliance_refusal || exit 1\n"
            "radiod_only_vm_gate || exit 1\n") in seq
    text = WIZARD.read_text()
    assert text.index("radiod_only_vm_gate || exit 1") < text.index("> /etc/sigmond/site-profile.toml.new")
    gate_src = shell_func("radiod_only_vm_gate")
    assert "guest_out" in gate_src and "qm guest exec" not in gate_src


def test_render_is_skipped_on_a_radiod_only_station():
    lines = [l for l in logical_lines()]
    i = next(n for n, l in enumerate(lines) if 'gexec_unit 600 render "smd config render"' in l)
    assert "if ! station_is_radiod_only; then" in lines[i - 2:i], lines[i - 2:i]


def vm_default(tmp_path, vm_out, host=None):
    mark = tmp_path / "m"
    mark.mkdir(exist_ok=True)
    if host is not None:
        (mark / "station-role").write_text(host + "\n")
    env = dict(os.environ, VM_OUT=vm_out)
    env.pop("SIGMOND_ROLE", None)
    r = bash(f"set -u\nMARK_DIR={mark}\nsay(){{ :; }}\n"
             "guest_out(){ echo CALLED >&2; printf '%s\\n' \"$VM_OUT\"; }\n"
             + shell_func("station_role_init") + shell_func("station_role_check")
             + shell_func("station_role_vm_default")
             + "station_role_init --reconfigure; station_role_check; station_role_vm_default\n"
             + 'echo "role=$STATION_ROLE ask=$STATION_ROLE_ASK"', env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()[-1], "CALLED" in r.stderr


def test_without_a_host_record_the_default_comes_from_the_vm(tmp_path):
    assert vm_default(tmp_path, "radiod-only") == ("role=radiod-only ask=1", True)


@pytest.mark.parametrize("vm", ["garbage", "", "full"])
def test_a_vm_role_that_is_not_radiod_only_defaults_to_full(tmp_path, vm):
    assert vm_default(tmp_path, vm)[0] == "role=full ask=1"


def test_a_host_record_wins_and_the_vm_is_not_asked(tmp_path):
    assert vm_default(tmp_path, "radiod-only", host="full") == ("role=full ask=1", False)


def test_the_vm_default_is_read_only_after_the_agent_answers():
    text = WIZARD.read_text()
    assert text.index("# <<< agent_wait") < text.index("\nstation_role_vm_default\n")
    assert text.index("\nstation_role_vm_default\n") < text.index("\nask_role\n")


def test_a_garbage_sigmond_role_does_not_stop_rac_retry(tmp_path):
    # The top-level argument handling runs for every subcommand; only the
    # configure flow validates the role.
    i = next(n for n, l in enumerate(LINES) if l.startswith('station_role_init "$@"'))
    top = "".join(LINES[i:i + 2])
    env = dict(os.environ, SIGMOND_ROLE="bogus")
    r = bash(f"set -u\nMARK_DIR={tmp_path}\nsay(){{ echo \"[wizard] $*\"; }}\n"
             + shell_func("station_role_init") + "set -- --rac-retry\n" + top
             + 'case "${1:-}" in --rac-retry) echo RETRY; exit 0;; esac\nexit 9\n', env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RETRY" in r.stdout and "refused" not in r.stdout
    text = WIZARD.read_text()
    check = text.index("station_role_check || exit 2")
    assert check > text.index("    --rac-retry)")
    assert check > text.index("already configured (")
