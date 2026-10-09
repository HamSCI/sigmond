"""Every install registers for remote access and brings the tunnel up; the
wizard asks nothing about it (tasks/plan-remote-access.md, "Decided on
review").  A later wizard run leaves the switch where its operator put it,
and a timer retries a registration that found no gateway.

These tests run the wizard's own functions, cut out of the shipped script by
name, with the host's commands stubbed.
"""
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from .test_rac_switch import Host

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"
TEXT = WIZARD.read_text()

ON_LINES = ("Remote access: on\n"
            "  The gateway accepted 6 of 6 channels (RAC 507 on gw2.wsprdaemon.org, wsprdaemon (secure)).\n"
            "  Turn it off:  smd remote off   (in the decoder VM)\n")
DOWN_LINES = ("Remote access: wanted on, not up\n"
              "  The station has not registered yet: no gateway has answered.\n"
              "  This host tries again every 10 minutes and brings the tunnel up when one does.\n")


def shell_func(name: str) -> str:
    out, keep = [], False
    for ln in TEXT.splitlines(True):
        if ln.startswith(f"{name}(){{") or ln.startswith(f"{name}() {{"):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, f"{name} not found in the wizard"
    return "".join(out)


def heredoc(path: str) -> str:
    m = re.search(r"cat > %s <<'([A-Z]+)'[^\n]*\n(.*?)\n\1\n" % re.escape(path), TEXT, re.S)
    assert m, f"the wizard writes no {path}"
    return m.group(2) + "\n"


def bash(script, stdin="", env=None):
    return subprocess.run(["bash", "-c", script], input=stdin, capture_output=True, text=True,
                          timeout=60, env=dict(os.environ, **(env or {})))


# -- the wizard asks nothing about remote access --------------------------------

ASK_PRELUDE = textwrap.dedent("""
    set -u
    RAC_BASE_VMSSH=35800; RAC_BASE_VMWEB=45800; RAC_BASE_HSSH=50800; RAC_BASE_HUI=55800
    RAC_REG_PORT=35737; RAC_PORT_SECURE=35736; RAC_PORT_UNSEC=35735
    _hs=vpn.hamsci.org; _wd=gw2.wsprdaemon.org
    RAC_SERVER=gw2.wsprdaemon.org
    HAVE_RX888=1; HAVE_GPSDO=1; HAVE_TS1=1; HAVE_MAG=1
    rd(){ read "$@" || { echo "STDIN-RAN-DRY"; exit 9; }; }
""")


def ask_rac(stdin, profile="dasi", dasi="", missing=False):
    script = (ASK_PRELUDE + f'RAC_PROFILE={profile}; DASI_NUM="{dasi}"\n'
              + ("HAVE_RX888=0\n" if missing else "")
              + shell_func("build_rac_tiers") + shell_func("ask_rac")
              + 'ask_rac\nIFS= read -r rest; echo "LEFT=$rest"; echo "PROFILE=$RAC_PROFILE DASI=$DASI_NUM"\n')
    return bash(script, stdin=stdin)


def test_an_ordinary_station_answers_one_question_and_none_about_remote_access():
    r = ask_rac("n\nNEXT-ANSWER\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "LEFT=NEXT-ANSWER" in r.stdout          # exactly one line consumed
    assert "PROFILE=standard" in r.stdout
    assert "Enable remote access" not in r.stdout


def test_a_dasi_station_answers_its_class_and_number_and_nothing_more():
    r = ask_rac("y\n3\nNEXT-ANSWER\n")
    assert "LEFT=NEXT-ANSWER" in r.stdout, r.stdout
    assert "PROFILE=dasi DASI=3" in r.stdout
    assert "DASI-003 maps deterministically to RAC #223" in r.stdout


def test_the_wizard_says_what_registration_sends_and_how_to_switch_it():
    out = ask_rac("n\n\n").stdout
    assert "Remote access comes up by itself; this wizard asks nothing about it." in out
    assert "this station's name" in out and "this host's public key" in out
    for cmd in ("smd remote off", "smd remote on", "smd remote status"):
        assert cmd in out, cmd
    assert "sigmond-setup --rac-off" not in out    # the host has no keyboard after install


def test_missing_equipment_still_gets_its_note_without_a_decision():
    out = ask_rac("n\n\n", missing=True).stdout
    assert "Some equipment was not detected" in out
    assert "strongly recommended" not in out       # nothing left to recommend: it comes up


def test_no_trace_of_the_question_or_its_answer_remains():
    for gone in ("Enable remote access?", "RAC_NUM", "RAC_EN", "4) Remote:    disabled"):
        assert gone not in TEXT, gone
    review = TEXT[TEXT.index("── Review — nothing is applied yet"):TEXT.index('rd -r -p "Apply?')]
    assert "smd remote off / on" in review
    assert "4) ask_rac;;" in TEXT                  # entry 4 still re-asks the station class
    assert "and (optionally)" not in TEXT          # the header no longer calls the tunnel optional


# -- what a wizard run does about the tunnel ---------------------------------------

def activate(tmp_path, *, configured=False, wish=None, registered=False, switch_wish="on",
             on_rc=0, on_out=ON_LINES, install_rc=0, register_rc=0, tier=None, env=None):
    """Run rac_activate against stubs.  `switch_wish=None` makes the switch's
    `status` leave no state file.  Called twice on one tmp_path, the second run
    meets what the first left in the mark directory."""
    mark = tmp_path / "marks"
    mark.mkdir(exist_ok=True)
    if (tmp_path / "calls").exists():
        (tmp_path / "calls").unlink()
    if configured:
        (mark / ".configured").write_text("N0CALL/T1 EM00aa 2026-10-01\n")
    if wish:
        (mark / "rac-desired").write_text(wish + "\n")
    if registered:
        (tmp_path / "frpc-host.toml").write_text('user = "x"\n')
    if tier:
        (mark / "rac-tier").write_text(tier + "\n")
    (tmp_path / "on.out").write_text(on_out)
    switch = tmp_path / "switch"
    status = ("" if switch_wish is None
              else f'echo "wish={switch_wish}" > {tmp_path}/rac-state; ')
    switch.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        echo "switch[${{SIGMOND_RAC_BY:-}}] $* site=$(cat {mark}/rac-site 2>/dev/null)" >> {tmp_path}/calls
        case "$1" in
            status) {status}exit 0 ;;
            on) cat {tmp_path}/on.out; exit {on_rc} ;;
        esac
        """))
    switch.chmod(0o755)
    script = textwrap.dedent(f"""
        set -u
        MARK_DIR={mark}
        CONF_MARK={mark}/.configured
        LOG={tmp_path}/wizard.log
        RAC_TOML={tmp_path}/frpc-host.toml
        RAC_SWITCH={switch}
        RAC_STATE_FILE={tmp_path}/rac-state
        RAC_PIN_FILE={mark}/rac-endpoint.env
        RAC_PROFILE=standard; DASI_NUM=""; REPORTER=N0CALL/T1; RAC_FAIL=""; RAC_STATE=""
        say(){{ echo "[wizard] $*"; }}
        install(){{ :; }}
        rac_install_host_files(){{ echo install-host-files >> {tmp_path}/calls; return {install_rc}; }}
        rac_install_relays(){{ echo install-relays >> {tmp_path}/calls; }}
        rac_register(){{ echo "register $1" >> {tmp_path}/calls; RAC_FAIL="no gateway tier answered"; return {register_rc}; }}
    """) + "".join(shell_func(n) for n in ("rac_site_name", "rac_save_pin", "rac_record_inputs",
                                           "rac_activate"))
    script += 'rac_activate; echo "rc=$?"; printf "STATE<<%s>>\\n" "$RAC_STATE"\n'
    r = bash(script, env=env)
    calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
    state = r.stdout.split("STATE<<", 1)[1].rsplit(">>", 1)[0] if "STATE<<" in r.stdout else None
    return r, calls, state


WISH_LINE = re.compile(r"^on \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ install$")


def test_a_first_install_turns_remote_access_on_and_asks_the_switch_once(tmp_path):
    r, calls, state = activate(tmp_path)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert calls == ["install-relays", "install-host-files",
                     "switch[install] on site=N0CALL_T1"]
    assert state.splitlines()[0] == "on"
    assert "The gateway accepted 6 of 6 channels" in state
    assert "smd remote off" in state
    # The wizard records the wish itself, before anything that can fail.
    marks = tmp_path / "marks"
    assert (marks / "rac-desired").read_text() == "on\n"
    assert WISH_LINE.match((marks / "rac-changed").read_text().rstrip("\n"))


def test_the_inputs_a_lone_registration_needs_are_on_disk_before_the_switch_runs(tmp_path):
    r, calls, _ = activate(tmp_path, env={"SIGMOND_RAC_SERVER": "127.0.0.1"})
    assert calls[-1].endswith("site=N0CALL_T1")    # the switch stub read it at call time
    marks = tmp_path / "marks"
    assert (marks / "rac-profile").read_text() == "standard\n"
    assert (marks / "rac-endpoint.env").read_text() == "SIGMOND_RAC_SERVER=127.0.0.1\n"


def test_a_first_install_with_no_gateway_still_finishes(tmp_path):
    r, calls, state = activate(tmp_path, on_rc=3, on_out=DOWN_LINES)
    assert "rc=0" in r.stdout                      # the wizard carries on
    assert state.splitlines()[0] == "wanted on, not up"
    assert "tries again every 10 minutes" in state
    assert "register fresh" not in calls           # the switch registers; one attempt, not two


def test_a_later_run_keeps_a_switch_its_operator_turned_off(tmp_path):
    # The wish on file decides.  The switch hears nothing: no `on`, no
    # registration, no question.
    r, calls, state = activate(tmp_path, configured=True, wish="off", registered=True,
                               switch_wish="on")           # the stub would say on, were it asked
    assert "rc=0" in r.stdout
    assert [c for c in calls if c.startswith("switch")] == []
    assert not any(c.startswith("register") for c in calls)
    assert state == "off, as its operator left it. Turn it on from the decoder VM: smd remote on"
    assert (tmp_path / "marks" / "rac-desired").read_text() == "off\n"
    assert not (tmp_path / "marks" / "rac-changed").exists()   # the wizard records no change


def test_a_station_from_before_v371_whose_owner_declined_stays_as_it_stands(tmp_path):
    # Configured, no wish on file, never registered: the switch reads "off".
    r, calls, state = activate(tmp_path, configured=True, switch_wish="off")
    assert not any(" on " in c for c in calls)
    assert state.startswith("off, as its operator left it")
    assert not (tmp_path / "marks" / "rac-desired").exists()   # no wish recorded for it
    assert not any(c.startswith("register") for c in calls)


def test_a_later_run_registers_afresh_before_it_asks_the_switch(tmp_path):
    r, calls, state = activate(tmp_path, configured=True, wish="on", registered=True)
    assert calls == ["install-relays", "install-host-files", "register fresh",
                     "switch[install] on site=N0CALL_T1"]
    assert state.splitlines()[0] == "on"


def test_a_failed_fresh_registration_warns_and_keeps_the_tunnel(tmp_path):
    r, calls, state = activate(tmp_path, configured=True, wish="on", registered=True,
                               register_rc=1)
    assert "could not register afresh (no gateway tier answered); the earlier registration stays" in r.stdout
    assert calls[-1] == "switch[install] on site=N0CALL_T1"
    assert state.splitlines()[0] == "on"


def test_a_host_that_cannot_take_the_switch_script_says_so_and_carries_on(tmp_path):
    # A first install whose host files fail still leaves the wish reading on,
    # the relays and the recorded inputs in place, and a way to try again.
    r, calls, state = activate(tmp_path, install_rc=1)
    assert "rc=0" in r.stdout
    assert calls == ["install-relays", "install-host-files"]
    assert state.startswith("FAILED: could not install")
    assert "sigmond-setup --reconfigure" in state
    marks = tmp_path / "marks"
    assert (marks / "rac-desired").read_text() == "on\n"
    assert WISH_LINE.match((marks / "rac-changed").read_text().rstrip("\n"))
    assert (marks / "rac-profile").read_text() == "standard\n"
    assert (marks / "rac-site").read_text().strip() == "N0CALL_T1"


def test_the_run_after_a_failed_first_install_brings_remote_access_up(tmp_path):
    activate(tmp_path, install_rc=1)                        # the first install fails
    # The wizard marked the host configured.  Were the wish not on file, the
    # real switch would read this unregistered station as off.
    r, calls, state = activate(tmp_path, configured=True, switch_wish="off")
    assert "switch[install] on site=N0CALL_T1" in calls
    assert state.splitlines()[0] == "on"
    assert "as its operator left it" not in state


def test_a_first_install_whose_switch_fails_outright_still_leaves_the_wish_on(tmp_path):
    r, calls, state = activate(tmp_path, on_rc=1, on_out="")
    assert "rc=0" in r.stdout
    assert state.startswith("FAILED: ")
    assert (tmp_path / "marks" / "rac-desired").read_text() == "on\n"


def test_a_switch_that_leaves_no_answer_is_not_a_decision(tmp_path):
    # Configured, no wish on file, and `status` writes no state file.
    r, calls, state = activate(tmp_path, configured=True, switch_wish=None)
    assert "rc=0" in r.stdout
    assert state.startswith("FAILED: could not read the remote-access switch's state")
    assert "as its operator left it" not in state
    assert not any(" on " in c for c in calls)
    assert not (tmp_path / "marks" / "rac-desired").exists()


def test_the_switch_reads_the_wish_lines_the_wizard_writes_as_its_own(tmp_path):
    # Put the files the wizard wrote in front of the real switch.
    (tmp_path / "wiz").mkdir()
    (tmp_path / "host").mkdir()
    activate(tmp_path / "wiz", install_rc=1)
    h = Host(tmp_path / "host")
    for name in ("rac-desired", "rac-changed"):
        shutil.copy(tmp_path / "wiz" / "marks" / name, h.d / "marks" / name)
    before = (h.d / "marks" / "rac-changed").read_text()
    h.run("status")
    st = h.state()
    assert (st["wish"], st["wish_source"], st["changed_by"]) == ("on", "file", "install")
    r = h.run("on")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert (h.d / "marks" / "rac-changed").read_text() == before   # `on` rewrites nothing


def test_an_unsecured_tier_keeps_its_warning(tmp_path):
    _, _, state = activate(tmp_path, tier="wsprdaemon (UNSECURED)")
    assert "UNSECURED (no TLS)" in state and "sigmond-setup --rac-upgrade" in state
    _, _, state = activate(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path,
                           tier="wsprdaemon (secure)")
    assert "UNSECURED" not in state


def test_the_wizard_calls_the_activation_once_and_before_it_marks_the_host_configured():
    assert TEXT.count("\nrac_activate\n") == 1
    assert TEXT.index("\nrac_activate\n") < TEXT.index('> "$CONF_MARK"')
    assert TEXT.index("\nrac_activate\n") > TEXT.index("rac_install_relays(){")


# -- the retry timer ------------------------------------------------------------------

def retry(tmp_path, *, register_rc, fail="no gateway tier answered (tried: wsprdaemon (secure))",
          wish=None, note=None, switch_rc=0):
    mark = tmp_path / "marks"
    mark.mkdir(exist_ok=True)
    (mark / "rac-number").write_text("507\n")
    if wish:
        (mark / "rac-desired").write_text(wish + "\n")
    if note is not None:
        (tmp_path / "reason").write_text(note + "\n")
    calls = tmp_path / "calls"
    if calls.exists():
        calls.unlink()
    switch = tmp_path / "switch"
    switch.write_text(f'#!/bin/bash\necho "switch[${{SIGMOND_RAC_BY:-}}] $*" >> {calls}\n'
                      f'echo "Remote access: on"\nexit {switch_rc}\n')
    switch.chmod(0o755)
    script = textwrap.dedent(f"""
        set -u
        MARK_DIR={mark}
        LOG={tmp_path}/wizard.log
        RAC_SWITCH={switch}
        RAC_RETRY_NOTE={tmp_path}/reason
        RAC_FAIL=""
        say(){{ echo "[wizard] $*"; }}
        systemctl(){{ echo "systemctl $*" >> {calls}; }}
        rac_register(){{ say "narration that must stay out of the journal"; RAC_FAIL='{fail}'; return {register_rc}; }}
    """) + shell_func("rac_retry") + 'rac_retry; echo "rc=$?"\n'
    r = bash(script)
    return r, (calls.read_text().splitlines() if calls.exists() else [])


def test_a_try_that_finds_no_gateway_says_so_once_and_leaves_the_timer_armed(tmp_path):
    r, calls = retry(tmp_path, register_rc=1)
    assert r.stdout.count("not registered yet: no gateway tier answered") == 1
    assert "rc=0" in r.stdout                      # a failed try is not a failed unit
    assert calls == []
    assert "narration" not in r.stdout
    r2, _ = retry(tmp_path, register_rc=1)
    assert "not registered yet" not in r2.stdout   # the same reason: silence
    r3, _ = retry(tmp_path, register_rc=1, fail="sigmond-rac payload missing (frpc/CA)")
    assert "not registered yet: sigmond-rac payload missing" in r3.stdout


def test_a_try_that_registers_brings_a_wanted_tunnel_up_and_leaves_the_disarming_to_the_switch(tmp_path):
    r, calls = retry(tmp_path, register_rc=0, wish="on", note="no gateway tier answered")
    assert calls == ["switch[timer] on"]           # the switch disarms the timer on its `on` path
    assert "registered as RAC #507" in r.stdout
    assert not (tmp_path / "reason").exists()


def test_a_try_whose_switch_fails_leaves_the_timer_armed(tmp_path):
    # The switch exits 1 (its lock is busy).  The station sits registered and
    # wanted on, and only the timer is left to try again.
    r, calls = retry(tmp_path, register_rc=0, wish="on", switch_rc=1)
    assert calls == ["switch[timer] on"]
    assert not any("systemctl" in c for c in calls)
    assert "rc=0" in r.stdout                      # a failed try is not a failed unit


@pytest.mark.parametrize("wish", ["off", None], ids=["turned-off-meanwhile", "no-wish-on-file"])
def test_a_try_that_registers_stops_there_when_the_operator_holds_it_off(tmp_path, wish):
    r, calls = retry(tmp_path, register_rc=0, wish=wish)
    assert calls == ["systemctl disable --now sigmond-rac-register.timer"]
    assert "the operator holds it off, so the tunnel stays down" in r.stdout


def test_the_timer_runs_the_retry_and_nothing_fixed_to_a_clock():
    service = heredoc("/etc/systemd/system/sigmond-rac-register.service")
    timer = heredoc("/etc/systemd/system/sigmond-rac-register.timer")
    assert "ExecStart=/usr/local/sbin/sigmond-setup --rac-retry\n" in service
    assert "Type=oneshot\n" in service
    assert "OnUnitInactiveSec=10min\n" in timer and "OnCalendar" not in timer
    assert "WantedBy=timers.target\n" in timer
    i = TEXT.index("    --rac-retry)")
    assert "rac_retry" in TEXT[i:TEXT.index("    --rac-off|--rac-on)", i)]
    assert i < TEXT.index("waiting for decoder VM")


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_the_retry_units_parse(tmp_path):
    for name in ("sigmond-rac-register.service", "sigmond-rac-register.timer"):
        (tmp_path / name).write_text(heredoc(f"/etc/systemd/system/{name}"))
    r = subprocess.run(["systemd-analyze", "verify", str(tmp_path / "sigmond-rac-register.timer")],
                       capture_output=True, text=True, timeout=60)
    noise = [ln for ln in (r.stdout + r.stderr).splitlines()
             if "sigmond-rac-register" in ln and "sigmond-setup" not in ln]
    assert noise == [], noise
