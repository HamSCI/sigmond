"""A fresh install starts with the site sink switch at `off`; a configured
host never gets the block (tasks/plan-sink-control.md §10.3 item 2)."""
import os
import subprocess
import textwrap
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"


def shell_func(name: str) -> str:
    out, keep = [], False
    for ln in WIZARD.read_text().splitlines(True):
        if ln.startswith(f"{name}(){{"):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, f"{name} not found in the wizard"
    return "".join(out)


def run_uploads_toml(conf_mark: Path) -> str:
    script = textwrap.dedent(f"""
        set -u
        CONF_MARK={conf_mark}
        {shell_func("uploads_toml")}
        uploads_toml
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=30).stdout


def test_fresh_host_writes_the_off_block(tmp_path):
    out = run_uploads_toml(tmp_path / ".configured")          # mark absent
    up = tomllib.loads(out)["uploads"]
    assert up["enabled"] is False
    assert up["mode"] == "discard"
    assert "smd sink upload" in up["reason"]


def test_configured_host_writes_no_block(tmp_path):
    mark = tmp_path / ".configured"
    mark.write_text("AC0G/B4 EM38ww 2026-10-01\n")
    assert run_uploads_toml(mark).strip() == ""


def test_the_profile_carries_the_block_and_installs_through_profile_install():
    text = WIZARD.read_text()
    assert 'UPLOADS_TOML=$(uploads_toml)' in text
    heredoc = text[text.index("PROFILE=$(cat <<PEOF"):text.index("PEOF\n)")]
    assert "$UPLOADS_TOML" in heredoc
    assert "smd config profile-install /etc/sigmond/site-profile.toml.new" in text
    # The wizard writes the new profile BESIDE the old one; a write straight
    # over site-profile.toml would reset the switch before profile-install ran.
    assert 'base64 -d > /etc/sigmond/site-profile.toml.new"' in text
    assert 'base64 -d > /etc/sigmond/site-profile.toml"' not in text


def test_the_summary_tells_a_fresh_install_how_to_set_the_switch_to_upload():
    text = WIZARD.read_text()
    summary = text[text.index("SUMMARY=$(cat <<SEOF"):text.index("SEOF\n)")]
    assert "$SINK_NOTE" in summary
    note = text[text.index('SINK_NOTE=""'):text.index("SUMMARY=$(cat <<SEOF")]
    assert "smd sink upload" in note
    # Only a fresh install (a non-empty UPLOADS_TOML) gets the note.
    for uploads, shown in (("", False), ("x", True)):
        out = subprocess.run(["bash", "-c", f"set -u\nUPLOADS_TOML={uploads!r}\n{note}"
                              'printf %s "$SINK_NOTE"'],
                             capture_output=True, text=True, timeout=30).stdout
        assert ("smd sink upload" in out) is shown, (uploads, out)


def test_the_mark_is_defined_and_the_block_is_built_before_the_wizard_writes_it():
    # uploads_toml reads $CONF_MARK, and the wizard writes that mark near its
    # end.  A reorder that wrote the mark first would make a fresh install look
    # configured; a lost definition would make every host look fresh.
    text = WIZARD.read_text()
    assert 'CONF_MARK="$MARK_DIR/.configured"' in text
    assert text.index('UPLOADS_TOML=$(uploads_toml)') < text.index('> "$CONF_MARK"')


# -- the profile-install block ------------------------------------------------

K3LR_HOLD = '[station]\ncallsign = "K3LR"\n\n[uploads]\nenabled = true\nmode = "hold"\n'


def install_block() -> str:
    """The wizard text from the `.new` write through the closing `fi`."""
    out, keep = [], False
    for ln in WIZARD.read_text().splitlines(True):
        if ln.startswith('gexec 30 "echo $B64'):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "fi":
                break
    assert out, "the profile install block was not found in the wizard"
    return "".join(out)


# A stub gexec: it records each guest command, answers the probe and the
# profile-install call from the environment, and runs the grep gate for real
# against a temporary stand-in for the live profile.  The string it receives is
# the one the wizard's own double quotes produced, so the gate's escaping gets
# tested too.
STUB = r"""
set -u
B64=QUJD
LOG=/dev/null
say(){ echo "[wizard] $*"; }
gexec(){
    shift
    printf '%s\n' "$*" >> "$CALLS"
    case "$*" in
        *"profile-install --help"*) return "$PROBE" ;;
        *"profile-install /etc/sigmond/site-profile.toml.new"*) return "$INSTALL_RC" ;;
        *"grep -qsE"*)
            [ "$AGENT" = dead ] && return 1
            local cmd="$*"
            bash -c "${cmd//\/etc\/sigmond\/site-profile.toml/$LIVE}"
            return $? ;;
        *) return 0 ;;
    esac
}
"""


def run_install_block(tmp_path, *, probe=0, live=None, agent="alive", install_rc=0):
    live_path = tmp_path / "site-profile.toml"
    if live is not None:
        live_path.write_text(live)
    calls = tmp_path / "calls"
    env = dict(os.environ, CALLS=str(calls), PROBE=str(probe), LIVE=str(live_path),
               AGENT=agent, INSTALL_RC=str(install_rc))
    r = subprocess.run(["bash", "-c", STUB + install_block()], env=env,
                       capture_output=True, text=True, timeout=30)
    ran = calls.read_text().splitlines() if calls.exists() else []
    return r, ran


def moved_whole(ran):
    return any(c.startswith("mv -f /etc/sigmond/site-profile.toml.new") for c in ran)


def test_a_vm_that_knows_profile_install_installs_through_it(tmp_path):
    r, ran = run_install_block(tmp_path, probe=0, live=K3LR_HOLD)
    assert r.returncode == 0, r.stdout
    assert "smd config profile-install /etc/sigmond/site-profile.toml.new" in ran
    assert not moved_whole(ran)
    assert not any("grep -qsE" in c for c in ran)


def test_a_refused_new_profile_stops_the_wizard_and_names_the_likely_cause(tmp_path):
    r, ran = run_install_block(tmp_path, probe=0, live=K3LR_HOLD, install_rc=1)
    assert r.returncode == 1
    assert not moved_whole(ran)
    assert "most often a double quote or backslash in an answer" in r.stdout


@pytest.mark.parametrize("live", [
    None,                                                   # no live file yet
    '[station]\ncallsign = "X"\n',                          # no uploads block
    '[station]\ncallsign = "X"\n# [uploads]\n# enabled = false\n',   # only a comment
], ids=["no-live-file", "no-block", "commented-block"])
def test_an_old_smd_installs_whole_only_when_the_live_profile_has_no_block(tmp_path, live):
    r, ran = run_install_block(tmp_path, probe=1, live=live)
    assert r.returncode == 0, r.stdout
    assert moved_whole(ran)
    assert "WARN" in r.stdout
    for verb in ("smd upload status", "smd upload on", "smd upload hold", "smd upload discard"):
        assert verb in r.stdout, verb


@pytest.mark.parametrize("live", [
    K3LR_HOLD,
    '[station]\ncallsign = "X"\n\n[uploads]   # outbound policy\nenabled = false\n',
    '[station]\ncallsign = "X"\n  [uploads]\nenabled = false\n',
], ids=["legacy-hold", "trailing-comment", "indented"])
def test_an_old_smd_never_overwrites_a_profile_that_sets_the_switch(tmp_path, live):
    r, ran = run_install_block(tmp_path, probe=1, live=live)
    assert r.returncode == 1
    assert not moved_whole(ran)
    assert "rm -f /etc/sigmond/site-profile.toml.new" in ran
    assert "The live site profile is unchanged." in r.stdout
    assert "Ask your fleet admin to update this VM's sigmond" in r.stdout
    assert "smd component update" not in r.stdout      # it restarts radiod


def test_an_old_smd_and_a_silent_guest_agent_never_overwrite_the_profile(tmp_path):
    # The gate answers 1 whether the profile carries a block or the agent died;
    # the wizard cannot tell them apart and must not guess.
    r, ran = run_install_block(tmp_path, probe=1, live=K3LR_HOLD, agent="dead")
    assert r.returncode == 1
    assert not moved_whole(ran)
    assert "The live site profile is unchanged." in r.stdout
