"""The operator pages agree with the code about remote access
(tasks/plan-remote-access.md section 5).

From v3.71 the wizard asks nothing about remote access and the operator holds
the switch in the decoder VM.  A page that still describes the old question,
or quotes a message the code no longer prints, teaches a station that does
not exist.  These checks read the pages and the code side by side.
"""
import re
from datetime import timedelta
from pathlib import Path

import pytest

from sigmond.commands import remote

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs" / "operator"
PAGE = (DOCS / "remote-access.md").read_text()
WIZARD = (REPO / "scripts" / "proxmox" / "sigmond-wizard.sh").read_text()
PM_ALIGN = (REPO / "scripts" / "proxmox" / "pm-align.py").read_text()
FLEETBOARD = (REPO / "server" / "heartbeat" / "fleetboard.py").read_text()
# The source writes an apostrophe inside a single-quoted string as \'.
REMOTE_PY = (REPO / "lib" / "sigmond" / "commands" / "remote.py").read_text().replace("\\'", "'")


def joined(source: str) -> str:
    """Python source with adjacent string literals joined: 'abc ' newline
    'def' reads as 'abc def', the way the message prints."""
    return re.sub(r"(['\"])\s*\n\s*\1", "", source)
# The console panel lives in the appliance repository, beside this one.
FIRSTBOOT = REPO.parent / "sigmond-appliance" / "firstboot-v3.sh"
FIRSTBOOT_TEXT = FIRSTBOOT.read_text() if FIRSTBOOT.exists() else ""


def flat(text: str) -> str:
    """Every run of white space as one space, so that a sentence the page
    wraps over two lines still matches the one-line sentence in the code."""
    return " ".join(text.split())


def anchors(text: str) -> set:
    """GitHub's anchor for every heading: lower case, punctuation dropped,
    each space a hyphen."""
    out = set()
    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.*?)\s*$", line)
        if m:
            kept = re.sub(r"[^\w\- ]", "", m.group(1).lower())
            out.add(kept.replace(" ", "-"))
    return out


def test_the_page_opens_with_the_three_commands():
    head = PAGE[:PAGE.index("## 1. What it is")]
    for cmd in ("smd remote off", "smd remote on", "smd remote status"):
        assert cmd in head, cmd
    assert "The setup wizard asks nothing about it." in head


def test_no_operator_page_describes_the_question_the_wizard_no_longer_asks():
    for md in sorted(DOCS.glob("*.md")):
        text = md.read_text()
        assert "Enable remote access?" not in text, md.name
        assert "If you said No at install time" not in text, md.name
        assert "Remote access says FAILED" not in text, md.name


def test_every_link_into_the_page_lands_on_a_heading():
    have = anchors(PAGE)
    for md in sorted(DOCS.glob("*.md")):
        for m in re.finditer(r"remote-access\.md#([A-Za-z0-9_-]+)", md.read_text()):
            assert m.group(1) in have, f"{md.name} links to #{m.group(1)}, which the page lacks"
    for m in re.finditer(r"\]\(#([A-Za-z0-9_-]+)\)", PAGE):
        assert m.group(1) in have


def test_only_the_remote_access_page_names_the_hosts_own_spelling_of_the_switch():
    # The host has no keyboard after install, so every other page sends the
    # operator to the VM.  The canonical page may say that the host's two
    # spellings run the same switch.
    for md in sorted(DOCS.glob("*.md")):
        if md.name != "remote-access.md":
            assert "sigmond-setup --rac-o" not in md.read_text(), md.name
    assert "sigmond-setup --rac-off" in PAGE and "sigmond-setup --pair-vm" in PAGE


def test_the_states_on_the_page_are_the_switchs_own_lines():
    for line in remote.STATE_LINES:
        assert f"\n{line}\n" in PAGE, line
        assert f'echo "{line}"' in WIZARD, line


def test_the_messages_the_page_quotes_are_messages_the_code_prints():
    page = flat(PAGE)
    for quoted in ("The station has not registered yet: no gateway has answered.",
                   "The tunnel service does not run",
                   "The tunnel service runs, and the gateway has accepted none of its",
                   "The operator holds it off. The registration stays",
                   "paired: the decoder VM can run smd remote on, smd remote off and smd remote status",
                   "NOT paired:",
                   "another pairing is running; nothing done",
                   "this host has no private link to its decoder VM (no /30 address)"):
        assert quoted in page, quoted
        assert quoted in WIZARD, quoted
    # The four ways `smd remote` fails to reach the host, and what it says
    # about the command that repairs the pairing.
    for quoted in ("holds no pairing with its Proxmox host",
                   "shows an sshd key this VM was not paired with",
                   "did not accept this VM's pairing key",
                   "could not reach the Proxmox host",
                   "gave no answer within",
                   "state not read yet",
                   "pairs this VM again when its sshd key file changes",
                   "That command needs the host at v3.71 or later.",
                   'An older host answers "already configured" and pairs nothing.'):
        assert quoted in page, quoted
        assert quoted in REMOTE_PY, quoted
    # The older wizard's answer, which the page tells the operator not to
    # take for success, comes from the wizard's own early exit.
    assert 'say "already configured (' in WIZARD


def test_the_times_the_page_names_are_the_codes():
    page = flat(PAGE)
    # The retry timer: "every 10 minutes" on the page is the timer's interval
    # and the switch's own sentence.
    m = re.search(r"sigmond-rac-register\.timer <<'RACREGTMREOF'.*?OnUnitInactiveSec=(\d+)min", WIZARD, re.S)
    assert m, "the retry timer is gone from the wizard"
    n = m.group(1)
    assert f"every {n} minutes" in page
    assert set(re.findall(r"every (\d+) minutes", page)) == {n}
    assert f"This host tries again every {n} minutes" in WIZARD
    # The tunnel service's own retry, as the switch prints it.
    assert "keeps trying every 30 seconds" in page
    assert "It keeps trying every 30 seconds." in WIZARD
    # How long `on` waits for the channels.
    assert "waits up to a minute" in page
    assert 'WAIT_S="${SIGMOND_RAC_WAIT_S:-60}"' in WIZARD


def test_the_ssh_port_the_page_names_is_the_relay_the_host_runs():
    assert set(re.findall(r"ssh -p (\d+) sigmond@", PAGE)) == {"2222"}
    assert "MGMT_VM=10.99.0.2" in FIRSTBOOT_TEXT or not FIRSTBOOT.exists()
    if FIRSTBOOT.exists():
        assert "--dport 2222 -j DNAT --to-destination ${MGMT_VM}:22" in FIRSTBOOT_TEXT


def test_the_state_blocks_on_the_page_print_exactly_what_the_switch_prints():
    # Every line of the three blocks in §4 is a line `print_state` or
    # `who_when` can print, with the values filled in.
    region = WIZARD[WIZARD.index("who_when(){"):WIZARD.index("wait_for_channels(){")]
    templates = []
    for s in re.findall(r'echo "((?:[^"\\]|\\.)*)"', region):
        parts = re.split(r'(\$\{[^}]*\}|\$\w+)', s.replace('\\"', '"'))
        templates.append(re.compile("".join(".+" if x.startswith("$") else re.escape(x) for x in parts)))
    start = PAGE.index("It prints one of three states:")
    end = PAGE.index("Only the third describes a fault.")
    blocks = re.findall(r"```text\n(.*?)```", PAGE[start:end], re.S)
    assert len(blocks) == 3
    for block in blocks:
        for line in block.splitlines():
            assert any(tp.fullmatch(line) for tp in templates), f"the switch prints no line like: {line}"
    # The values the examples carry.
    assert "UNIT=sigmond-rac-host.service" in WIZARD
    assert 'who="from the decoder VM"' in WIZARD and 'who="by the installer"' in WIZARD


def test_the_not_paired_answer_on_the_page_is_remote_pys():
    start = PAGE.index("  \u2717  this VM holds no pairing with its Proxmox host")
    block = PAGE[start:PAGE.index("```", start)]
    code = joined(REMOTE_PY)
    for line in block.splitlines():
        text = line.strip().lstrip("\u2717").strip()
        assert text in code, f"remote.py prints no line like: {text}"


def test_the_fleet_boards_tags_on_the_page_are_the_boards():
    code = joined(FLEETBOARD)
    page = flat(PAGE)
    for tag in ("remote access off by the operator",
                "remote access off: never registered",
                "remote access was off by the operator at its last heartbeat"):
        assert tag in page, tag
        assert tag in code, tag


def test_the_page_promises_no_speed_the_code_does_not_keep():
    # The host re-pairs on a timer, a boot and a key change; none of them
    # names a time within which a repair lands.
    page = flat(PAGE).lower()
    for phrase in ("within minutes", "by itself within", "in a few minutes"):
        assert not re.search(rf"\b{phrase}\b", page), phrase


def test_the_wizards_words_on_the_page_are_the_wizards():
    i = PAGE.index("Remote access comes up by itself; this wizard asks nothing about it.")
    block = PAGE[i:PAGE.index("```", i)]
    for line in block.strip().splitlines():
        assert f'echo "{line}"' in WIZARD, line


def test_the_page_lists_the_six_channels_the_tunnel_declares():
    declared = re.findall(r'name = "\$SITE-([a-z-]+)"', WIZARD)
    assert sorted(declared) == ["host-ssh", "host-ui", "vm-gmag", "vm-ssh", "vm-station", "vm-web"]
    assert "**six channels**" in PAGE
    for base in ("35800", "45800", "48800", "49800", "50800", "55800"):
        assert f"{base} + your RAC number" in PAGE, base
        # The wizard's header comment names every base too; only an
        # assignment makes it the base the tunnel uses.
        assert re.search(rf"RAC_BASE_\w+={base}\b", WIZARD), base


def test_the_pairing_checks_the_page_promises_are_the_hosts_units():
    # §8 says the host checks the pairing a few minutes after every boot, every
    # 6 hours and when its sshd key file changes.  The three lines below arm
    # exactly that (4 minutes after boot).
    assert ("checks the pairing again a few minutes after every boot, every 6 hours, "
            "and when its sshd key file (the file that holds its ssh host key) changes") in flat(PAGE)
    assert "OnBootSec=4min" in WIZARD
    assert "OnUnitInactiveSec=6h" in WIZARD
    assert "PathChanged=/etc/ssh/ssh_host_ed25519_key.pub" in WIZARD


def test_the_banner_age_the_page_names_is_the_codes():
    assert remote.FRESH_FOR == timedelta(hours=7)
    assert "more than 7 hours old" in flat(PAGE)


def test_the_pm_align_reason_the_page_quotes_is_pm_aligns():
    # pm-align prints "skipped: " and then NO_PRIVATE_LINK, which the source
    # splits over two lines.
    assert 'return f"skipped: {NO_PRIVATE_LINK}"' in PM_ALIGN
    assert "this host has no private link to its decoder VM" in PM_ALIGN
    assert "skipped: this host has no private link to its decoder VM" in flat(PAGE)


@pytest.mark.skipif(not FIRSTBOOT.exists(), reason="no sigmond-appliance checkout beside this one")
def test_the_console_panel_lines_on_the_page_are_the_panels():
    panel = flat(FIRSTBOOT.read_text())
    page = flat(PAGE)
    for quoted in ("Remote access ON — RAC",
                   "Remote access OFF, by the operator's choice",
                   "not a fault",
                   "NOTE: the tunnel service still runs, so this station can still be reached.",
                   "Remote access WANTED ON, NOT REGISTERED YET — no gateway has answered",
                   "Remote access WANTED ON, BUT NOT UP — RAC",
                   "Remote access WANTED ON, BUT NOT UP — the tunnel service does not run",
                   "Remote access STATE NOT READ — the switch gave no fresh answer",
                   "Remote access off — this station has not registered for it",
                   "(private link to this host — not on your LAN)",
                   "ADMINISTRATORS ONLY",
                   "HamSCI users"):
        assert quoted in page, quoted
        assert quoted in panel, quoted
    # How an operator tells the private link from the old layout: the panel's
    # `Decoder VM` line carries the note only on the 10.99.0.* addresses.
    assert "`Decoder VM`" in PAGE and "Decoder VM" in panel
    assert "10.99.0.*)" in FIRSTBOOT_TEXT and "MGMT_VM=10.99.0.2" in FIRSTBOOT_TEXT
    assert "`10.99.0.2`" in PAGE
