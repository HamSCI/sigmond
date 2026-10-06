"""A fresh install starts with the site sink switch at `off`; a configured
host never gets the block (tasks/plan-sink-control.md §10.3 item 2)."""
import subprocess
import textwrap
import tomllib
from pathlib import Path

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
