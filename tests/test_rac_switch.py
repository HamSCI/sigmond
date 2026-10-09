"""sigmond-rac-switch: the one switch for the remote-access tunnel
(tasks/plan-remote-access.md section 1).

The script ships inside sigmond-wizard.sh as a heredoc, the way sigmond-vm
does, so these tests cut it out of the wizard and run what ships.  A scratch
directory stands in for the Proxmox host: stub `systemctl`, `curl` and
`logger` sit first on PATH and answer from files in it.
"""
import fcntl
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WIZARD = (REPO / "scripts" / "proxmox" / "sigmond-wizard.sh").read_text()

SOCKETS = [f"sigmond-vm-{n}-relay.socket" for n in ("ssh", "web", "station", "gmag")]
UNIT = "sigmond-rac-host.service"
ON, OFF, DOWN = "Remote access: on", "Remote access: off", "Remote access: wanted on, not up"

TOML = ('serverAddr = "gw2.wsprdaemon.org"\nuser = "0123456789abcdef"\n'
        + "".join(f'[[proxies]]\nname = "AC0G_B4-{c}"\nremotePort = {p}\n' for c, p in (
            ("vm-ssh", 36307), ("vm-web", 46307), ("vm-station", 49307),
            ("vm-gmag", 50307), ("host-ssh", 51307), ("host-ui", 56307))))


def switch_text() -> str:
    m = re.search(r"cat > /usr/local/sbin/sigmond-rac-switch <<'RACSWEOF'\n(.*?)\nRACSWEOF\n",
                  WIZARD, re.S)
    assert m, "the wizard carries no sigmond-rac-switch heredoc"
    return m.group(1) + "\n"


SYSTEMCTL = r"""#!/bin/bash
# stub: write down every call; keep `active` and `enabled` as files
echo "$*" >> "$HOSTDIR/systemctl.calls"
verb="$1"; shift
case "$verb" in
    is-active)  [ -e "$HOSTDIR/active" ] ;;
    is-enabled) [ -e "$HOSTDIR/enabled" ] ;;
    enable)  case " $* " in *" sigmond-rac-host.service "*)
                 touch "$HOSTDIR/enabled"
                 [ -e "$HOSTDIR/unit-refuses-to-start" ] || touch "$HOSTDIR/active" ;; esac ;;
    disable) case " $* " in *" sigmond-rac-host.service "*)
                 rm -f "$HOSTDIR/enabled"
                 [ -e "$HOSTDIR/unit-refuses-to-stop" ] || rm -f "$HOSTDIR/active" ;; esac ;;
esac
"""
CURL = r"""#!/bin/bash
# stub: frpc's admin API, with as many running proxies as the file says
n=$(cat "$HOSTDIR/channels" 2>/dev/null || echo 0)
printf '{"tcp":['
for i in $(seq 1 "$n"); do printf '{"name":"p%s","status":"running"},' "$i"; done
printf '{"name":"x","status":"new"}]}\n'
"""
SETUP = r"""#!/bin/bash
# stub: sigmond-setup --rac-register
echo "$*" >> "$HOSTDIR/setup.calls"
[ "$1" = "--rac-register" ] || exit 9
[ -e "$HOSTDIR/gateway-answers" ] || { echo "[wizard] not registered: no gateway tier answered"; exit 1; }
cp "$HOSTDIR/toml.reply" "$HOSTDIR/frpc-host.toml"
echo 507 > "$HOSTDIR/marks/rac-number"
echo "[wizard] registered: RAC #507"
"""


class Host:
    def __init__(self, tmp_path, *, registered=True, enabled=False, active=False,
                 channels=6, wish=None):
        self.d = tmp_path
        (tmp_path / "bin").mkdir()
        (tmp_path / "marks").mkdir()
        (tmp_path / "run").mkdir()
        for name, body in (("systemctl", SYSTEMCTL), ("curl", CURL), ("sigmond-setup", SETUP),
                           ("logger", "#!/bin/sh\nexit 0\n"), ("sleep", "#!/bin/sh\nexit 0\n")):
            p = tmp_path / "bin" / name
            p.write_text(body)
            p.chmod(0o755)
        self.script = tmp_path / "sigmond-rac-switch"
        self.script.write_text(switch_text())
        self.script.chmod(0o755)
        (tmp_path / "toml.reply").write_text(TOML)
        (tmp_path / "channels").write_text(str(channels))
        if registered:
            (tmp_path / "frpc-host.toml").write_text(TOML)
            (tmp_path / "marks" / "rac-number").write_text("507\n")
            (tmp_path / "marks" / "rac-tier").write_text("wsprdaemon (secure)\n")
        if enabled:
            (tmp_path / "enabled").touch()
        if active:
            (tmp_path / "active").touch()
        if wish:
            (tmp_path / "marks" / "rac-desired").write_text(wish + "\n")

    def run(self, *args, env=None, **kw):
        e = {"PATH": f"{self.d}/bin:{os.environ['PATH']}", "HOSTDIR": str(self.d),
             "SIGMOND_RAC_MARK_DIR": f"{self.d}/marks",
             "SIGMOND_RAC_TOML": f"{self.d}/frpc-host.toml",
             "SIGMOND_RAC_RUN_DIR": f"{self.d}/run",
             "SIGMOND_RAC_SETUP": f"{self.d}/bin/sigmond-setup",
             "SIGMOND_RAC_WAIT_S": "10", "SIGMOND_RAC_WAIT_STEP": "5"}
        e.update(env or {})
        return subprocess.run([str(self.script), *args], capture_output=True, text=True,
                              timeout=60, env=e, **kw)

    def calls(self, name="systemctl"):
        p = self.d / f"{name}.calls"
        return p.read_text().splitlines() if p.exists() else []

    def changes(self):
        """Every systemctl call that changes something."""
        return [c for c in self.calls() if c.split()[0] not in ("is-active", "is-enabled")]

    def wish(self):
        p = self.d / "marks" / "rac-desired"
        return p.read_text().strip() if p.exists() else None

    def state(self):
        p = self.d / "run" / "rac-state"
        return dict(ln.split("=", 1) for ln in p.read_text().splitlines())


# -- the word -------------------------------------------------------------------

REFUSED = [
    [], [""], ["ON"], ["Status"], ["start"], ["--help"], ["-h"], ["on", "off"],
    ["status", "extra"], ["status extra"], ["on;reboot"], [" on"], ["on "], ["on\n"],
    ["off", ""], ["$(reboot)"], ["status", "--json"],
]


@pytest.mark.parametrize("args", REFUSED, ids=[repr(a) for a in REFUSED])
def test_anything_but_one_of_the_three_words_is_refused_and_changes_nothing(tmp_path, args):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    r = h.run(*args)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert r.stdout == ""
    assert "exactly one word: on, off or status" in r.stderr
    assert h.calls() == []                       # not even a read
    assert h.wish() == "on"
    assert not (tmp_path / "run" / "rac-state").exists()


REFUSAL = "sigmond-rac-switch: refused. It takes exactly one word: on, off or status.\n"


@pytest.mark.parametrize("asked", ["status extra", "off; reboot", "on\noff", "", " off",
                                   "sigmond-rac-switch off", "\x1b[2Joff"])
def test_a_request_through_the_forced_command_is_read_as_strictly(tmp_path, asked):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    r = h.run(env={"SSH_ORIGINAL_COMMAND": asked})
    assert r.returncode == 2
    assert h.calls() == [] and h.wish() == "on"
    assert r.stderr == REFUSAL                   # never a byte of what was asked


def test_the_forced_command_passes_the_word_in_ssh_original_command(tmp_path):
    # No argument: the word arrives in SSH_ORIGINAL_COMMAND, and in that mode
    # the script honours no override.  It therefore reads this machine's real
    # paths, so only the shape of the answer can be checked here.
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    r = h.run(env={"SSH_ORIGINAL_COMMAND": "status"})
    assert r.returncode in (0, 3), (r.stdout, r.stderr)
    assert r.stdout.splitlines()[0] in (ON, OFF, DOWN)
    assert not (tmp_path / "run" / "rac-state").exists()     # SIGMOND_RAC_RUN_DIR ignored
    assert not (tmp_path / "setup.calls").exists()


def test_a_request_from_the_pairing_account_is_recorded_as_from_the_vm(tmp_path):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    h.run("off", env={"SUDO_USER": "sigmond-remote"})
    assert (tmp_path / "marks" / "rac-changed").read_text().split()[2] == "vm"
    assert "from the decoder VM" in h.run("status").stdout


# -- off ---------------------------------------------------------------------------

def test_off_records_the_wish_and_disables_the_relays_then_the_tunnel(tmp_path):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    r = h.run("off")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.splitlines()[0] == OFF
    assert h.wish() == "off"
    assert h.changes() == ["disable --now " + " ".join(SOCKETS),
                           f"disable --now {UNIT}",
                           "start --no-block sigmond-issue.service"]
    assert (tmp_path / "frpc-host.toml").read_text() == TOML        # the registration stays
    assert "The registration stays (RAC 507 on gw2.wsprdaemon.org, wsprdaemon (secure))" in r.stdout
    assert "Turn it on:   smd remote on" in r.stdout
    st = h.state()
    assert (st["state"], st["wish"], st["tunnel"], st["registered"]) == ("off", "off", "stopped", "yes")


def test_off_says_who_and_when(tmp_path):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    h.run("off")
    word, when, who = (tmp_path / "marks" / "rac-changed").read_text().split()
    assert (word, who) == ("off", "host")
    assert re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ", when)
    r = h.run("status")
    assert f"Set to off at {when} (UTC) on the Proxmox host." in r.stdout
    assert h.state()["changed_by"] == "host" and h.state()["changed_at"] == when


def test_off_finishes_when_its_terminal_has_gone_away(tmp_path):
    # An admin who came in through the tunnel types `smd remote off`: the
    # tunnel stops, the session dies, and the script must still finish.
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    rd, wr = os.pipe()
    os.close(rd)                                 # every write now meets a broken pipe
    try:
        e = {"PATH": f"{tmp_path}/bin:{os.environ['PATH']}", "HOSTDIR": str(tmp_path),
             "SIGMOND_RAC_MARK_DIR": f"{tmp_path}/marks",
             "SIGMOND_RAC_TOML": f"{tmp_path}/frpc-host.toml",
             "SIGMOND_RAC_RUN_DIR": f"{tmp_path}/run"}
        p = subprocess.run([str(h.script), "off"], stdout=wr, stderr=wr, env=e, timeout=60)
    finally:
        os.close(wr)
    assert p.returncode == 0
    assert h.wish() == "off"
    assert f"disable --now {UNIT}" in h.changes()
    assert not (tmp_path / "active").exists()


def test_off_that_leaves_the_tunnel_running_says_so_and_exits_4(tmp_path):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    (tmp_path / "unit-refuses-to-stop").touch()
    r = h.run("off")
    assert r.returncode == 4
    assert r.stdout.splitlines()[0] == OFF
    assert "the tunnel service runs all the same" in r.stdout


def test_off_on_a_station_that_never_registered_still_records_the_wish(tmp_path):
    h = Host(tmp_path, registered=False)
    r = h.run("off")
    assert r.returncode == 0 and h.wish() == "off"
    assert "The station has not registered yet." in r.stdout


# -- on ----------------------------------------------------------------------------

def test_a_change_that_cannot_get_the_lock_gives_up_and_changes_nothing(tmp_path):
    # The retry timer and an operator can ask in the same moment.  One lock
    # puts them in a row; a change that never gets it says so and touches nothing.
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    with open(tmp_path / "run" / "rac-switch.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        r = h.run("off", env={"SIGMOND_RAC_LOCK_WAIT_S": "1"})
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "another change is still running; nothing changed" in r.stderr
    assert r.stdout == ""
    assert h.changes() == []
    assert h.wish() == "on"
    # The positive control: the lock released, the same request goes through.
    r = h.run("off", env={"SIGMOND_RAC_LOCK_WAIT_S": "1"})
    assert r.returncode == 0 and h.wish() == "off", (r.stdout, r.stderr)


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only directory")
@pytest.mark.parametrize("word,stood", [("off", "on"), ("on", "off")])
def test_a_wish_that_cannot_be_recorded_changes_nothing(tmp_path, word, stood):
    h = Host(tmp_path, enabled=(stood == "on"), active=(stood == "on"), wish=stood)
    marks = tmp_path / "marks"
    marks.chmod(0o555)
    try:
        r = h.run(word)
    finally:
        marks.chmod(0o755)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "could not record the wish; nothing changed" in r.stderr
    assert h.changes() == []                     # nothing enabled, nothing stopped
    assert h.wish() == stood


def test_on_enables_relays_and_tunnel_and_waits_for_every_declared_channel(tmp_path):
    h = Host(tmp_path, wish="off", channels=6)
    r = h.run("on")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.splitlines()[-4] == ON or ON in r.stdout.splitlines()
    assert h.wish() == "on"
    assert h.changes() == ["enable --now " + " ".join(SOCKETS),
                           f"enable --now {UNIT}",
                           "start --no-block sigmond-issue.service"]
    assert h.calls("setup") == []                # registered already: nothing asked of the gateway
    assert "The gateway accepted 6 of 6 channels" in r.stdout
    assert "Turn it off:  smd remote off" in r.stdout
    st = h.state()
    assert (st["state"], st["channels_up"], st["channels_declared"]) == ("on", "6", "6")


def test_on_registers_first_when_the_station_never_did(tmp_path):
    h = Host(tmp_path, registered=False)
    (tmp_path / "gateway-answers").touch()
    r = h.run("on")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert h.calls("setup") == ["--rac-register"]
    assert "registered: RAC #507" in r.stdout    # the registration's own lines reach the caller
    assert f"enable --now {UNIT}" in h.changes()
    assert ON in r.stdout.splitlines()


def test_on_with_no_gateway_keeps_the_wish_and_reports_not_up(tmp_path):
    h = Host(tmp_path, registered=False)
    r = h.run("on")
    assert r.returncode == 3
    assert h.wish() == "on"
    assert DOWN in r.stdout.splitlines()
    assert "has not registered with a gateway yet" in r.stdout
    assert not any(c.startswith("enable") for c in h.changes())
    assert h.state()["state"] == "down" and h.state()["registered"] == "no"


def test_on_whose_channels_never_come_up_exits_3_after_the_wait(tmp_path):
    h = Host(tmp_path, wish="off", channels=0)
    r = h.run("on")
    assert r.returncode == 3
    assert DOWN in r.stdout.splitlines()
    assert "the gateway has accepted none of its 6 channels" in r.stdout
    assert h.wish() == "on"


def test_on_whose_service_will_not_start_names_the_journal(tmp_path):
    h = Host(tmp_path, wish="off")
    (tmp_path / "unit-refuses-to-start").touch()
    r = h.run("on")
    assert r.returncode == 3
    assert "The tunnel service does not run" in r.stdout
    assert f"journalctl -u {UNIT} -n 50" in r.stdout


def test_on_counts_what_the_configuration_declares_not_a_fixed_number(tmp_path):
    # Four of six is still a tunnel, and the answer says four of six.
    h = Host(tmp_path, wish="off", channels=4)
    r = h.run("on")
    assert r.returncode == 0
    assert "The gateway accepted 4 of 6 channels" in r.stdout
    assert h.calls().count("is-active --quiet " + UNIT) >= 1
    body = switch_text()
    assert "-ge 4" not in body and "-ge 6" not in body
    assert "grep -c '^name *=' \"$TOML\"" in body


def test_on_when_the_wish_already_reads_on_keeps_who_and_when(tmp_path):
    h = Host(tmp_path, wish="on")
    earlier = "on 2026-01-01T00:00:00Z install\n"
    (tmp_path / "marks" / "rac-changed").write_text(earlier)
    h.run("on")
    assert (tmp_path / "marks" / "rac-changed").read_text() == earlier


def test_the_installer_and_the_vm_are_named_as_such(tmp_path):
    h = Host(tmp_path, registered=True)
    h.run("on", env={"SIGMOND_RAC_BY": "install"})
    assert (tmp_path / "marks" / "rac-changed").read_text().split()[2] == "install"
    r = h.run("status")
    assert "by the installer" in r.stdout
    h.run("off", env={"SIGMOND_RAC_BY": "anything else"})
    assert (tmp_path / "marks" / "rac-changed").read_text().split()[2] == "host"


# -- status ------------------------------------------------------------------------

@pytest.mark.parametrize("kw,first,rc", [
    (dict(wish="on", enabled=True, active=True, channels=6), ON, 0),
    (dict(wish="off"), OFF, 0),
    (dict(wish="on", enabled=True, active=True, channels=0), DOWN, 3),
    (dict(wish="on", enabled=True, active=False), DOWN, 3),
    (dict(wish="on", registered=False), DOWN, 3),
], ids=["on", "off", "running-no-channel", "not-running", "not-registered"])
def test_status_prints_one_of_three_first_lines_and_changes_nothing(tmp_path, kw, first, rc):
    h = Host(tmp_path, **kw)
    before = h.wish()
    r = h.run("status")
    assert r.returncode == rc, (r.stdout, r.stderr)
    assert r.stdout.splitlines()[0] == first
    assert h.changes() == []
    assert h.wish() == before
    assert not (tmp_path / "marks" / "rac-changed").exists()


def test_status_writes_the_state_file_the_panel_and_the_heartbeat_read(tmp_path):
    h = Host(tmp_path, wish="on", enabled=True, active=True, channels=5)
    h.run("status")
    st = h.state()
    assert list(st) == ["state", "wish", "wish_source", "registered", "tunnel", "channels_up",
                        "channels_declared", "rac", "gateway", "tier", "changed_at",
                        "changed_by", "checked_at"]
    assert st["state"] == "on" and st["wish_source"] == "file"
    assert (st["channels_up"], st["channels_declared"]) == ("5", "6")
    assert (st["rac"], st["gateway"], st["tier"]) == ("507", "gw2.wsprdaemon.org", "wsprdaemon (secure)")
    assert re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ", st["checked_at"])
    assert oct((tmp_path / "run" / "rac-state").stat().st_mode & 0o777) == "0o644"


# -- a station installed before v3.71 holds no wish on file ---------------------------

@pytest.mark.parametrize("kw,first,line", [
    (dict(registered=True, enabled=True, active=True), ON, "The gateway accepted 6 of 6"),
    (dict(registered=True, enabled=False), OFF, "The operator holds it off. The registration stays"),
    (dict(registered=False, enabled=False), OFF, "This station never registered for remote access."),
    (dict(registered=False, enabled=True), OFF, "This station never registered for remote access."),
], ids=["said-yes", "rac-off-earlier", "said-no", "said-no-unit-enabled"])
def test_without_a_wish_on_file_the_wish_reads_from_what_stands(tmp_path, kw, first, line):
    h = Host(tmp_path, **kw)
    r = h.run("status")
    assert r.stdout.splitlines()[0] == first
    assert line in r.stdout
    assert h.wish() is None                      # status adopts nothing
    assert h.state()["wish_source"] == "units"


def test_a_station_whose_owner_declined_stays_as_it_stands_until_on(tmp_path):
    h = Host(tmp_path, registered=False)
    h.run("status")
    assert h.calls("setup") == [] and h.changes() == []
    (tmp_path / "gateway-answers").touch()
    r = h.run("on")
    assert r.returncode == 0 and h.calls("setup") == ["--rac-register"]


# -- one switch ---------------------------------------------------------------------

def test_the_wizards_two_spellings_run_the_switch_and_nothing_else():
    i = WIZARD.index("    --rac-off|--rac-on)")
    branch = WIZARD[i:WIZARD.index("    --rac-upgrade)", i)]
    assert 'exec "$RAC_SWITCH" "${1#--rac-}"' in branch
    assert "\nRAC_SWITCH=/usr/local/sbin/sigmond-rac-switch\n" in WIZARD
    assert "systemctl" not in branch
    assert "    --rac-on)\n" not in WIZARD and "    --rac-off)\n" not in WIZARD


def test_no_wizard_line_outside_the_switch_turns_the_tunnel_on_or_off():
    outside = WIZARD.replace(switch_text(), "")
    for verb in ("enable", "disable", "start", "stop", "restart"):
        assert not re.search(rf"systemctl {verb}( --now)? [^\n]*sigmond-rac-host", outside), verb
        assert not re.search(rf"systemctl {verb}( --now)? [^\n]*sigmond-vm-[a-z]+-relay", outside), verb
    assert "systemctl try-restart sigmond-rac-host.service" in outside     # registration only


def test_the_wizard_run_brings_the_tunnel_up_through_the_switch():
    block = WIZARD[WIZARD.index("rac_install_relays(){"):WIZARD.index('PSWS_STATE="skipped')]
    assert 'SIGMOND_RAC_BY=install "$RAC_SWITCH" on' in block
    assert "127.0.0.1:7500" not in block          # the switch does the proving now
    assert block.index("rac_install_host_files") < block.index('"$RAC_SWITCH" on')


def test_the_switch_installs_from_the_wizard_as_an_ascii_script():
    body = switch_text()
    assert body.startswith("#!/bin/bash\n")
    assert all(ord(c) < 128 for c in body)
    assert subprocess.run(["bash", "-n"], input=body, text=True, capture_output=True).returncode == 0
    i = WIZARD.index("rac_install_host_files(){")
    fn = WIZARD[i:WIZARD.index("\nRACSWEOF\n", i) + 400]
    assert "rm -f /usr/local/sbin/sigmond-rac-switch\n" in fn
    assert "chmod 755 /usr/local/sbin/sigmond-rac-switch" in fn
    assert WIZARD.index("rac_install_host_files(){") < WIZARD.index('case "${1:-}" in')


# -- the installer keeps the working switch until the new one proves whole -----------
#
# A failed write must not cost the station the switch it had.  The tests cut
# rac_install_host_files and its helper out of the wizard, point every path in
# them at a scratch directory, and put a `cat` first on PATH that goes wrong
# in the way CAT_MODE says.

CAT = r"""#!/bin/bash
# stub: a cat whose write goes wrong.  $CAT_MODE says how; no mode, a real cat.
case "$CAT_MODE" in
    fail)    "$REAL_CAT" > /dev/null; exit 1 ;;            # disk full: nothing written
    refused) "$REAL_CAT"; exit 1 ;;                        # the whole body, and a bad status
    short)   head -c "$CAT_BYTES"; exit 0 ;;               # cut short, and a good status
    garbled) "$REAL_CAT" > /dev/null; printf 'if [\nexit 0\n'; exit 0 ;;   # ends right, does not parse
esac
exec "$REAL_CAT" "$@"
"""
CHMOD = '#!/bin/bash\n# stub: a chmod that refuses when CHMOD_FAILS is set\n[ -n "$CHMOD_FAILS" ] && exit 1\nexec "$REAL_CHMOD" "$@"\n'
WORKING = "#!/bin/bash\necho the switch that works\nexit 0\n"


def installer_text(target: Path) -> str:
    """rac_install_host_files and the helper after it, with every path moved to `target`."""
    i = WIZARD.index("RAC_SWITCH=/usr/local/sbin/sigmond-rac-switch\n")
    j = WIZARD.index("# ── RAC on/off switches", i)
    return WIZARD[i:j].replace("/usr/local/sbin/sigmond-rac-switch", str(target))


def run_installer(tmp_path, *, old=True, mode=None, nbytes=None, chmod_fails=False):
    stub = tmp_path / "stub"
    stub.mkdir()
    for name, body in (("cat", CAT), ("chmod", CHMOD)):
        (stub / name).write_text(body)
        (stub / name).chmod(0o755)
    target = tmp_path / "sigmond-rac-switch"
    if old:
        target.write_text(WORKING)
        target.chmod(0o755)
    (tmp_path / "installer.sh").write_text(installer_text(target))
    env = {"PATH": f"{stub}:{os.environ['PATH']}", "REAL_CAT": shutil.which("cat"),
           "REAL_CHMOD": shutil.which("chmod")}
    if chmod_fails:
        env["CHMOD_FAILS"] = "1"
    if mode:
        env["CAT_MODE"] = mode
    if nbytes is not None:
        env["CAT_BYTES"] = str(nbytes)
    r = subprocess.run(["bash", "-c", f"set -u; source {tmp_path}/installer.sh; rac_install_host_files"],
                       capture_output=True, text=True, env=env, timeout=60)
    return r, target, target.with_name(target.name + ".old")


CUTS = {                                         # where a short write stops, from the end
    "half": lambda n: n // 2,
    "a-screen-short": lambda n: n - 1000,
    "before-the-last-line": lambda n: n - len("exit 0\n"),
    "inside-the-last-line": lambda n: n - 3,
}


def test_the_switch_ends_on_the_line_the_installer_looks_for():
    assert switch_text().splitlines()[-1] == "exit 0"


def test_a_good_write_replaces_the_switch_and_leaves_nothing_beside_it(tmp_path):
    r, target, aside = run_installer(tmp_path)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert target.read_text() == switch_text()
    assert oct(target.stat().st_mode & 0o777) == "0o755"
    assert not aside.exists()


@pytest.mark.parametrize("mode,cut", [("fail", None), ("refused", None), ("garbled", None)]
                         + [("short", c) for c in CUTS],
                         ids=["cat-fails", "cat-reports-failure-after-a-whole-write",
                              "does-not-parse"] + [f"short-{c}" for c in CUTS])
def test_a_write_that_goes_wrong_puts_the_working_switch_back_and_fails(tmp_path, mode, cut):
    n = len(switch_text().encode())
    r, target, aside = run_installer(tmp_path, mode=mode, nbytes=CUTS[cut](n) if cut else None)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert target.read_text() == WORKING
    assert os.access(target, os.X_OK)
    assert not aside.exists()


def test_a_switch_that_cannot_be_made_executable_puts_the_working_one_back(tmp_path):
    r, target, aside = run_installer(tmp_path, chmod_fails=True)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert target.read_text() == WORKING
    assert os.access(target, os.X_OK)
    assert not aside.exists()


@pytest.mark.parametrize("mode", ["fail", "refused", "garbled"])
def test_a_first_install_that_goes_wrong_leaves_no_broken_switch_behind(tmp_path, mode):
    r, target, aside = run_installer(tmp_path, old=False, mode=mode)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert not target.exists() and not aside.exists()


# -- on waits for the channels, step by step -----------------------------------------

CURL_SEQ = r"""#!/bin/bash
# stub: frpc's admin API, answering from a sequence.  Every call takes the next
# number in $HOSTDIR/channels.seq as the count of running proxies; the last repeats.
read -r -a all < "$HOSTDIR/channels.seq"
i=$(cat "$HOSTDIR/channels.pos" 2>/dev/null || echo 0)
echo $((i + 1)) > "$HOSTDIR/channels.pos"
n=${all[$i]:-${all[-1]}}
printf '{"tcp":['
for k in $(seq 1 "$n"); do printf '{"name":"p%s","status":"running"},' "$k"; done
printf '{"name":"x","status":"new"}]}\n'
"""
SLEEP_LOG = '#!/bin/sh\necho "$*" >> "$HOSTDIR/sleep.calls"\n'


def host_that_climbs(tmp_path, answers, **kw):
    """A host whose gateway accepts channels as the sequence says, one answer per look."""
    h = Host(tmp_path, wish="off", **kw)
    (tmp_path / "channels.seq").write_text(" ".join(str(a) for a in answers))
    for name, body in (("curl", CURL_SEQ), ("sleep", SLEEP_LOG)):
        (tmp_path / "bin" / name).write_text(body)
        (tmp_path / "bin" / name).chmod(0o755)
    return h


def test_on_waits_until_the_gateway_has_accepted_every_channel(tmp_path):
    h = host_that_climbs(tmp_path, [0, 0, 6])
    r = h.run("on")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "The gateway accepted 6 of 6 channels" in r.stdout
    assert len(h.calls("sleep")) >= 2


def test_on_does_not_settle_for_the_channels_up_at_the_first_look(tmp_path):
    h = host_that_climbs(tmp_path, [4, 4, 6])
    r = h.run("on")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "The gateway accepted 6 of 6 channels" in r.stdout
    assert "4 of 6" not in r.stdout
    assert h.state()["channels_up"] == "6"


def test_on_waits_the_whole_time_it_is_given_and_no_longer(tmp_path):
    h = host_that_climbs(tmp_path, [0])
    r = h.run("on", env={"SIGMOND_RAC_WAIT_S": "20", "SIGMOND_RAC_WAIT_STEP": "5"})
    assert r.returncode == 3
    assert DOWN in r.stdout.splitlines()
    assert h.calls("sleep") == ["5"] * 4           # WAIT_S / WAIT_STEP steps of WAIT_STEP
