"""No greenfield station gets a direct connection to wd30.

The wizard used to ask "Send status heartbeats?", default ON, and write
``[heartbeat] host=wd30.wsprdaemon.org port=38222`` into site-profile.toml —
so every new station held an outbound ssh to one operator's box on a
router-forwarded port, and the PM did the same through pm-heartbeat-setup.sh.

Station status moves onto the UPLOAD path in Phase 3: carried in the client's
own tar, delivered through the reflector, which is outbound-only, already
authenticated by the station's gateway account and already replicated to all
three servers.  The prompt and the configuration are gone (rob 2026-10-29).

These assertions are about EXECUTABLE lines only.  The explanatory comments
naturally name wd30, and a test that banned the word would forbid saying why.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"


def code_lines() -> list[str]:
    """Wizard lines with comments and blanks removed."""
    out = []
    for ln in WIZARD.read_text().splitlines():
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(ln)
    return out


def test_no_heartbeat_prompt_remains():
    src = "\n".join(code_lines())
    assert "ask_heartbeat" not in src, "the wd30 heartbeat prompt is back"
    for var in ("HB_ENABLED", "HB_HOST", "HB_PORT", "HB_TOML",
                "HB_DEFAULT_HOST", "HB_DEFAULT_PORT"):
        assert var not in src, f"{var} is back in the wizard"


def test_wizard_never_names_wd30_in_code():
    for ln in code_lines():
        assert "wd30" not in ln.lower(), f"wd30 named in executable line: {ln!r}"
        assert "38222" not in ln, f"the wd30 heartbeat port is back: {ln!r}"


def test_no_heartbeat_section_is_written():
    src = "\n".join(code_lines())
    assert "[heartbeat]" not in src, "the wizard writes a [heartbeat] section again"


def test_both_timers_are_actively_disarmed():
    """A rerun on a station configured by an older build must take the timers
    DOWN.  Merely not asking would leave the previous answer standing."""
    src = "\n".join(code_lines())
    assert re.search(r"systemctl disable --now sigmond-heartbeat\.timer", src), \
        "the VM heartbeat timer is no longer disarmed"
    assert re.search(r"systemctl disable --now pm-heartbeat\.timer", src), \
        "the host heartbeat timer is no longer disarmed"
    assert "pm-heartbeat-setup.sh" not in src, \
        "the PM heartbeat setup script is being run again"


def test_review_menu_is_seven_entries_with_no_heartbeat():
    src = WIZARD.read_text()
    assert "6) Heartbeat" not in src and "Heartbeat: enabled" not in src
    assert 'rd -r -p "Apply? [Y = apply / 1-7 = re-edit that entry' in src, \
        "the review prompt still offers an eighth entry"
    # Every number the prompt offers must dispatch to something.
    dispatch = re.search(r"case \"\$\{OK:-Y\}\" in(.*?)esac", src, re.S)
    assert dispatch, "review dispatch not found"
    offered = set(re.findall(r"^\s*([1-9])\)", dispatch.group(1), re.M))
    assert offered == {"1", "2", "3", "4", "5", "6", "7"}, \
        f"review dispatch covers {sorted(offered)}, not 1-7"
