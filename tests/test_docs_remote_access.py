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
# From v3.72 #23 the IPv4 relay's ports live in sigmond-vm4relay, which the
# importer runs.
VM4RELAY = REPO.parent / "sigmond-appliance" / "sigmond-vm4relay"
VM4RELAY_TEXT = VM4RELAY.read_text() if VM4RELAY.exists() else ""


def relayed_ports() -> dict:
    """{host port: VM port} the host relays over IPv4, from sigmond-vm4relay's
    PORTS line, as long as the importer runs it."""
    if "/usr/local/sbin/sigmond-vm4relay install" not in FIRSTBOOT_TEXT:
        return {}
    m = re.search(r'^PORTS="([^"]*)"', VM4RELAY_TEXT, re.M)
    return dict(p.split(":") for p in m.group(1).split()) if m else {}


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
        assert relayed_ports().get("2222") == "22"


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
                "remote access was off by the operator at its last heartbeat",
                "remote access was off (never registered) at its last heartbeat"):
        assert tag in page, tag
        assert tag in code, tag


def test_each_gateway_base_belongs_to_its_channel():
    # The wizard's RAC_BASE_<NAME>=<base> assignments, read out and set beside
    # the row the page gives for that channel.
    row_of = {"VMSSH": "**VM ssh**", "VMWEB": "**VM web**", "VMSTN": "**station pages**",
              "VMGMAG": "**magnetometer**", "HSSH": "**host ssh**", "HUI": "**host UI**"}
    bases = dict(re.findall(r"\bRAC_BASE_(\w+)=(\d+)", WIZARD))
    assert set(row_of) <= set(bases), sorted(bases)
    rows = {line.split("|")[1].strip(): line for line in PAGE.splitlines() if line.startswith("| **")}
    for name, label in row_of.items():
        assert f"{bases[name]} + your RAC number" in rows[label], (name, bases[name], rows[label])


def test_the_tiers_the_page_names_are_the_wizards_labels():
    page = flat(PAGE)
    for label in ("HamSCI secure (TOFU)", "wsprdaemon (secure)",
                  "HamSCI unsecured (direct)", "wsprdaemon (UNSECURED)"):
        assert label in page, label
        assert f"{label}|" in WIZARD, label


def test_the_panel_figures_the_page_names_are_the_panels():
    if not FIRSTBOOT.exists():
        pytest.skip("no sigmond-appliance checkout beside this one")
    # "five minutes later": the panel's refresh timer.
    m = re.search(r"sigmond-issue\.timer <<'ITEOF'.*?OnUnitActiveSec=(\d+)min", FIRSTBOOT_TEXT, re.S)
    assert m, "the panel's refresh timer is gone"
    word = {1: "one", 2: "two", 3: "three", 5: "five", 10: "ten", 15: "fifteen"}[int(m.group(1))]
    assert f"`STATE NOT READ` {word} minutes later" in flat(PAGE)
    # The ssh command the panel prints for the VM is the one the page gives.
    panel_ports = set(re.findall(r"ssh -p (\d+) sigmond@\$\{HOSTIP", FIRSTBOOT_TEXT))
    assert panel_ports == set(re.findall(r"ssh -p (\d+) sigmond@<host address>", PAGE)) != set()
    # Beside the host login the panel says which password to use.
    assert "Logins   host  root / $PWLINE" in FIRSTBOOT_TEXT
    assert 'PWLINE="the password you set at install"' in FIRSTBOOT_TEXT
    assert "the password you set at install" in flat(PAGE)


def _address_contexts(text: str):
    """(sentence or table row, place) for every http:// or https:// URL or ssh
    command that targets `<VM address>`."""
    pattern = re.compile(r"(?:https?://|\bssh\b[^`\n]*@)<VM address>")
    paragraph: list = []
    found = []

    def close():
        if paragraph:
            for sentence in re.split(r"(?<=[.!?])\s+", " ".join(paragraph)):
                if pattern.search(sentence):
                    found.append(sentence)
            paragraph.clear()

    for line in text.splitlines():
        if line.startswith("|"):
            close()
            if pattern.search(line):
                found.append(line)
        elif line.strip():
            paragraph.append(line.strip())
        else:
            close()
    close()
    return found


def test_no_operator_page_sends_the_reader_to_an_address_that_reaches_nothing():
    # On a station with the private link the VM holds 10.99.0.2, which nothing
    # on the operator's network reaches; the host relays the VM's ports at its
    # own address.  A page may name `<VM address>` as the target of a URL or an
    # ssh command only where the same sentence or table row names the old layout.
    bad = []
    for md in sorted(DOCS.glob("*.md")):
        for context in _address_contexts(md.read_text()):
            if "old layout" not in context:
                bad.append(f"{md.name}: {context[:110]}")
    assert not bad, "\n".join(bad)


def test_the_address_guard_reads_http_https_and_ssh():
    # The guard above must be able to fail on each of the three forms.
    for line in ("Open http://<VM address>:8081 in a browser.",
                 "Open https://<VM address>:8006 in a browser.",
                 "Log in with `ssh sigmond@<VM address>` from another computer.",
                 "| web | https://<VM address>:8006 |"):
        assert len(_address_contexts(line + "\n")) == 1, line
    assert _address_contexts("Open https://<host address>:8006 in a browser.\n") == []


def test_the_relays_the_pages_name_are_relays_the_host_runs():
    # The pages send web visitors to the host's ports 8081, 8000 and 8082 and
    # ssh to port 2222; each is a DNAT to the same port on the VM (ssh to 22).
    if not FIRSTBOOT.exists():
        pytest.skip("no sigmond-appliance checkout beside this one")
    relayed = relayed_ports()
    for port, target in (("8081", "8081"), ("8000", "8000"), ("8082", "8082"), ("2222", "22")):
        assert relayed.get(port) == target, port
    for page in ("README.md", "day-2.md", "troubleshooting.md"):
        text = (DOCS / page).read_text()
        for port in re.findall(r"http://<host address>:(\d+)", text):
            assert port in ("8081", "8000", "8082"), (page, port)


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


# -- what the final fixes changed in what an operator reads -----------------------------

def test_no_page_promises_that_off_stops_the_retries():
    # `off` never touches the retry timer: the host registers when a gateway
    # answers, and then leaves the tunnel down.
    for md in sorted(DOCS.glob("*.md")):
        low = md.read_text().lower()
        for phrase in ("stop it trying", "stops trying", "stop the retr", "stops the retr"):
            assert phrase not in low, (md.name, phrase)
    assert "the host still registers when it can, and stops there" in flat(PAGE)
    assert 'systemctl disable --now "$RETRY_TIMER"' in WIZARD            # only where no off stands on file
    off = WIZARD[WIZARD.index("switch_off(){"):WIZARD.index("switch_on(){")]
    assert off.count("RETRY_TIMER") == 1 and '[ "$recorded" = yes ] || systemctl disable --now "$RETRY_TIMER"' in off


@pytest.mark.skipif(not FIRSTBOOT.exists(), reason="no sigmond-appliance checkout beside this one")
def test_the_panels_sentence_about_the_retry_is_on_the_page():
    quoted = "To keep the tunnel down once the station registers"
    assert quoted in flat(PAGE) and quoted in FIRSTBOOT_TEXT
    assert "stop it trying" not in FIRSTBOOT_TEXT


@pytest.mark.skipif(not FIRSTBOOT.exists(), reason="no sigmond-appliance checkout beside this one")
def test_the_instruction_the_page_gives_for_an_unpaired_station_is_the_panels():
    # The panel names the decoder VM's command only where the wizard's pairing
    # recorded `paired`; everywhere else it prints the host's own switch as a
    # whole command.  The page quotes that line for both words.
    m = re.search(r"""printf "(type on another computer:  ssh root@%s 'sigmond-setup --rac-%s')" """, FIRSTBOOT_TEXT)
    assert m, "the panel no longer prints the host's own switch in this form"
    for word in ("on", "off"):
        line = m.group(1).replace("%s", "<host address>", 1).replace("%s", word, 1)
        assert line in PAGE, line
    # The paired form, which the page already gives.
    assert "log in to the decoder VM (ssh -p 2222 sigmond@" in FIRSTBOOT_TEXT
    assert "log in to the decoder VM (ssh -p 2222 sigmond@<host address>) and type:  smd remote on" in PAGE
    # One file decides, and the page says which outcome earns which line.
    assert '"${RAC_PAIRING:-$RAC_MARKS/rac-pairing}"' in FIRSTBOOT_TEXT
    assert '"$MARK_DIR/rac-pairing"' in WIZARD
    page = flat(PAGE)
    assert "has not paired" in page and "names the host's own switch" in page
    # Section 8 says it too, where the reader of an older station looks.
    eight = PAGE[PAGE.index("## 8. A station installed before v3.71"):PAGE.index("## When to mention it")]
    assert "sigmond-setup --rac-on'" in eight and "console panel" in eight


def test_the_banners_line_for_off_over_a_running_tunnel_is_on_the_page():
    quoted = "OFF, but the tunnel service still runs"
    assert quoted in flat(PAGE) and quoted in REMOTE_PY
    assert "Close it:  smd remote off" in REMOTE_PY
    assert remote.TUNNEL_RUNS_NOTE in WIZARD


def test_the_changed_key_message_on_the_page_names_the_sshd_restart():
    code = joined(REMOTE_PY)
    page = flat(PAGE)
    assert "systemctl restart ssh" in code and "`systemctl restart ssh`" in page
    seven = PAGE[PAGE.index("## 7. If remote access does not come up"):PAGE.index("## 8. A station installed")]
    i = seven.index("shows an sshd key this VM was not paired with")
    bullet = flat(seven[i:seven.index("did not accept this VM's pairing key", i)])
    assert bullet.index("sigmond-setup --pair-vm") < bullet.index("systemctl restart ssh")
    assert bullet.count("sigmond-setup --pair-vm") == 2          # pair, restart sshd, pair again
    assert "a running sshd serves the key it loaded at its start" in code
    assert "serves the key it loaded at its start" in bullet


def test_the_page_says_what_off_does_when_the_host_can_write_nothing():
    page = flat(PAGE)
    for quoted in ("sigmond-rac-switch:", "could not record the wish 'off'"):
        assert quoted in page, quoted
        assert quoted in WIZARD, quoted
    assert "stops and disables the tunnel" in page


@pytest.mark.skipif(not FIRSTBOOT.exists(), reason="no sigmond-appliance checkout beside this one")
def test_what_the_page_says_about_the_keyboard_is_what_the_station_does():
    # The host has no keyboard of its own.  A keyboard plugged into the station
    # types into the decoder VM, and the split console carries that to a root
    # login on the host (firstboot-v3.sh: sigmond-console-bridge, -paint, -relay).
    first_line = "KEYBOARD WORKS HERE \u2014 press Enter for a login to THIS host"
    assert first_line in PAGE and first_line in FIRSTBOOT_TEXT
    assert "TYPE THE PROXMOX HOST ROOT PASSWORD" in FIRSTBOOT_TEXT       # what the screen then asks
    assert "Nothing appears while you type" in FIRSTBOOT_TEXT
    assert "TCP4-LISTEN:7790,bind=10.99.0.1" in FIRSTBOOT_TEXT           # it rides the private link
    page = flat(PAGE)
    assert "The host has no keyboard of its own" in page
    assert "types into the decoder VM" in page
    assert "nothing shows while you type" in page.lower()
    i = page.index("KEYBOARD WORKS HERE")
    after = page[i:i + 900]
    for cmd in ("sigmond-setup --rac-off", "sigmond-setup --rac-on", "sigmond-setup --pair-vm"):
        assert cmd in after, cmd
    assert "The host has no keyboard once the install ends" not in PAGE
    # No neighbouring page still calls the keyboard dead or the console read-only.
    for md in sorted(DOCS.glob("*.md")):
        low = md.read_text().lower()
        # (troubleshooting.md keeps "The host console keyboard is dead" as a
        # heading: it names what the operator sees, and its body says what holds.)
        for stale in ("keyboard stops working", "dead keyboard", "they do nothing",
                      "console is read-only"):
            assert stale not in low, (md.name, stale)


@pytest.mark.skipif(not FIRSTBOOT.exists(), reason="no sigmond-appliance checkout beside this one")
def test_the_two_password_notes_the_page_quotes_are_the_panels():
    for quoted in ("the password you set at install", "hamsci-sigmond <-- image default, CHANGE IT"):
        assert quoted in flat(PAGE), quoted
        assert quoted in flat(FIRSTBOOT_TEXT), quoted
    assert "with a warning to change it" not in PAGE             # a paraphrase the panel never prints


def test_the_page_claims_no_more_about_an_older_host_than_the_code_shows():
    page = flat(PAGE)
    assert ("On an older host, `sigmond-setup --rac-on` does nothing for a station that never "
            "registered.") in page
    assert "needs a host at v3.71 to register" not in page
    assert "means the host already runs a pairing" in page
    assert "pairs right now" not in page
    assert "If the same answer returns after ten minutes, tell your fleet admin." in page
    # Wherever section 8 says the host's own two commands work the switch, it
    # says for what: an existing registration.
    eight = flat(PAGE[PAGE.index("## 8. A station installed before v3.71"):PAGE.index("## When to mention it")])
    for m in re.finditer(r"`sigmond-setup --rac-off` and `sigmond-setup --rac-on`", eight):
        assert "existing registration" in eight[m.start():m.start() + 260], eight[m.start():m.start() + 200]


def test_the_contents_page_and_the_install_page_ask_for_the_host_address_alone():
    readme = (DOCS / "README.md").read_text()
    install = (DOCS / "install.md").read_text()
    for name, text in (("README.md", readme), ("install.md", install)):
        low = flat(text).lower()
        assert "both addresses" not in low, name
        assert "write down the host address when the install shows it" in low, name
        assert "works only from the host" in low, name
    assert "The VM address alone is not enough" not in install
    assert "ports confirmed live" not in readme
    assert "a small private network that only the two machines share" in flat(readme)
