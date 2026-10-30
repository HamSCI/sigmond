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
RETRY_TIMER = "sigmond-rac-register.timer"
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
# stub: write down every call; keep `active` and `enabled` as files.  `is-active`
# prints the unit's state and exits 0 for `active` alone, as systemctl does; the
# file `activating` stands for a unit inside its restart delay.
echo "$*" >> "$HOSTDIR/systemctl.calls"
verb="$1"; shift
case "$verb" in
    is-active)  say=echo; case " $* " in *" --quiet "*) say=: ;; esac
                if [ -e "$HOSTDIR/activating" ]; then $say activating; exit 3; fi
                if [ -e "$HOSTDIR/active" ]; then $say active; exit 0; fi
                $say inactive; exit 3 ;;
    is-enabled) [ -e "$HOSTDIR/enabled" ] ;;
    enable)  case " $* " in *" sigmond-rac-host.service "*)
                 touch "$HOSTDIR/enabled"
                 [ -e "$HOSTDIR/unit-refuses-to-start" ] || touch "$HOSTDIR/active" ;; esac ;;
    disable) case " $* " in *" sigmond-rac-host.service "*)
                 [ -e "$HOSTDIR/unit-stays-enabled" ] || rm -f "$HOSTDIR/enabled"
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

    # Read-only verbs: asking systemd a question is not a change, and a
    # sequence assertion that counts one is brittle against any new query.
    # list-unit-files joined the list when switch_on began asking which
    # sigmond-rac-host@ instances are enabled, so it can disable the ones
    # whose config has gone (2026-10-30).
    _READ_ONLY = ("is-active", "is-enabled", "list-unit-files", "show")

    def changes(self):
        """Every systemctl call that changes something."""
        return [c for c in self.calls() if c.split()[0] not in self._READ_ONLY]

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


# The forced command honours no override, so these tests meet this machine's
# real paths.  As an ordinary user the switch can write none of them; as root it
# could, so they do not run as root.
FORCED = pytest.mark.skipif(os.geteuid() == 0, reason="as root the forced command would write this machine's /run/sigmond")


@FORCED
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


NOT_ROOT = pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only directory")


def run_with(h, word, *, closed=(), env=None):
    """Run the switch while the directories named in `closed` take no write."""
    for d in closed:
        (h.d / d).chmod(0o555)
    try:
        return h.run(word, env=env)
    finally:
        for d in closed:
            (h.d / d).chmod(0o755)


@NOT_ROOT
def test_an_on_whose_wish_cannot_be_recorded_changes_nothing(tmp_path):
    h = Host(tmp_path, wish="off")
    r = run_with(h, "on", closed=["marks"])
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "could not record the wish; nothing changed" in r.stderr
    assert h.changes() == []                     # nothing enabled, nothing started
    assert h.wish() == "off"


@NOT_ROOT
def test_an_on_that_cannot_open_its_lock_changes_nothing(tmp_path):
    h = Host(tmp_path, wish="off")
    r = run_with(h, "on", closed=["run"])
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "cannot write under" in r.stderr and "nothing changed" in r.stderr
    assert h.changes() == [] and h.wish() == "off"


# -- off closes the station whatever it cannot write ---------------------------------
#
# A full root disk must not keep an owner from closing the station.  `off` stops
# and disables the relays and the tunnel first, and says afterwards what it could
# not put on file.

CLOSED = ["disable --now " + " ".join(SOCKETS), f"disable --now {UNIT}"]


@NOT_ROOT
def test_off_closes_the_tunnel_when_the_wish_cannot_be_recorded(tmp_path):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    r = run_with(h, "off", closed=["marks"])
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert h.changes()[:2] == CLOSED
    # No `off` stands on file to stop a later run of the retry timer, so the timer goes too.
    assert f"disable --now {RETRY_TIMER}" in h.changes()
    assert not (tmp_path / "active").exists() and not (tmp_path / "enabled").exists()
    assert h.wish() == "on"                                  # the file could not change
    assert r.stdout.splitlines()[0] == DOWN                  # what this host now reads
    said = [ln for ln in r.stdout.splitlines() if ln.startswith("sigmond-rac-switch: ")]
    assert any("stopped and disabled the tunnel, its relays and the retry timer" in ln for ln in said), r.stdout
    assert any("could not record the wish 'off'" in ln and str(tmp_path / "marks") in ln for ln in said), r.stdout
    assert "nothing changed" not in r.stdout + r.stderr


@NOT_ROOT
def test_off_closes_the_tunnel_when_the_run_directory_takes_no_write(tmp_path):
    # No lock file can be opened and no state file written.
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    r = run_with(h, "off", closed=["run"])
    assert h.changes()[:2] == CLOSED, (r.stdout, r.stderr)
    assert not (tmp_path / "active").exists()
    assert h.wish() == "off"                                 # the wish did reach its file
    assert not any(RETRY_TIMER in c for c in h.changes())    # so the timer stays as it stood
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.splitlines()[0] == OFF
    assert f"sigmond-rac-switch: could not write under {tmp_path}/run" in r.stdout
    assert not (tmp_path / "run" / "rac-state").exists()
    assert "nothing changed" not in r.stdout + r.stderr


def test_off_closes_the_tunnel_when_no_run_directory_can_exist(tmp_path):
    # A regular file where the directory belongs: this fails for root too.
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    (tmp_path / "run").rmdir()
    (tmp_path / "run").write_text("not a directory\n")
    r = h.run("off")
    assert h.changes()[:2] == CLOSED, (r.stdout, r.stderr)
    assert h.wish() == "off" and r.returncode == 0
    assert r.stdout.splitlines()[0] == OFF


@NOT_ROOT
def test_off_closes_the_tunnel_when_neither_directory_takes_a_write(tmp_path):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    r = run_with(h, "off", closed=["marks", "run"])
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert h.changes()[:3] == CLOSED + [f"disable --now {RETRY_TIMER}"]
    assert not (tmp_path / "active").exists()


@NOT_ROOT
def test_off_that_cannot_record_and_cannot_stop_the_tunnel_says_both_and_exits_4(tmp_path):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    (tmp_path / "unit-refuses-to-stop").touch()
    r = run_with(h, "off", closed=["marks"])
    assert r.returncode == 4, (r.stdout, r.stderr)
    assert "the tunnel service STILL RUNS" in r.stdout
    assert "could not record the wish 'off'" in r.stdout


@NOT_ROOT
def test_off_that_cannot_record_says_so_when_systemd_could_not_disable_the_unit_either(tmp_path):
    # A root filesystem mounted read-only: systemd stops the unit and cannot
    # remove its link, so the tunnel would start again at the next boot.  The
    # answer must not say "disabled".
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    (tmp_path / "unit-stays-enabled").touch()
    r = run_with(h, "off", closed=["marks"])
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert not (tmp_path / "active").exists()                # stopped all the same
    assert "the tunnel service STAYS ENABLED" in r.stdout and "starts again at the next boot" in r.stdout
    assert "stopped and disabled the tunnel" not in r.stdout
    assert "could not record the wish 'off'" in r.stdout


@NOT_ROOT
def test_off_on_a_station_with_no_wish_on_file_reads_off_and_still_says_what_it_could_not_record(tmp_path):
    # A station from before v3.71: the wish reads from the units, so once they
    # stand disabled the state reads off.
    h = Host(tmp_path, enabled=True, active=True)
    r = run_with(h, "off", closed=["marks"])
    assert r.returncode == 1
    assert r.stdout.splitlines()[0] == OFF
    assert "could not record the wish 'off'" in r.stdout
    assert h.wish() is None


def test_an_off_that_records_its_wish_prints_no_line_of_complaint(tmp_path):
    h = Host(tmp_path, enabled=True, active=True, wish="on")
    r = h.run("off")
    assert r.returncode == 0
    assert "sigmond-rac-switch:" not in r.stdout and r.stderr == ""


def rac_branch(word):
    """The `--rac-off|--rac-on` branch of the wizard's dispatch, run for `word`."""
    i = WIZARD.index("    --rac-off|--rac-on)")
    branch = WIZARD[i:WIZARD.index("    --rac-upgrade)", i)]
    return f'set -- --rac-{word}\ncase "${{1:-}}" in\n' + branch + "esac\necho NOT-REACHED\n"


@pytest.mark.parametrize("word", ["off", "on"])
def test_the_hosts_own_spelling_reaches_a_standing_switch_when_its_files_cannot_be_refreshed(tmp_path, word):
    # A full disk fails the reinstall of the host files.  v3.70's --rac-off closed
    # the station all the same, and so must this one.
    switch = tmp_path / "switch"
    switch.write_text(f'#!/bin/bash\necho "switch $*" >> {tmp_path}/calls\necho "Remote access: off"\nexit 0\n')
    switch.chmod(0o755)
    script = (f'set -u\nRAC_SWITCH={switch}\nsay(){{ echo "[wizard] $*"; }}\n'
              'rac_install_host_files(){ return 1; }\n' + rac_branch(word))
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    assert (tmp_path / "calls").read_text() == f"switch {word}\n", (r.stdout, r.stderr)
    assert r.returncode == 0 and "NOT-REACHED" not in r.stdout
    assert "could not refresh the remote-access files on this host" in r.stdout
    assert "Remote access: off" in r.stdout


def test_the_hosts_own_spelling_stops_when_no_switch_stands_at_all(tmp_path):
    script = (f'set -u\nRAC_SWITCH={tmp_path}/no-switch\nsay(){{ echo "[wizard] $*"; }}\n'
              'rac_install_host_files(){ return 1; }\n' + rac_branch("off"))
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "NOT-REACHED" not in r.stdout
    assert "could not install the remote-access files on this host (the switch or its units)" in r.stdout


# -- a timer run or a wizard run never reverses an operator's off ---------------------
#
# Each reads the wish, then asks for `on`: the timer milliseconds later, a wizard
# rerun minutes later, after a fresh registration.  An `off` typed in between
# stands on file by the time the switch holds its lock, and it wins.

@pytest.mark.parametrize("by", ["timer", "install"])
def test_an_on_from_the_timer_or_the_installer_leaves_an_off_on_file_alone(tmp_path, by):
    h = Host(tmp_path, wish="off")
    before = "off 2026-10-09T18:22:05Z vm\n"
    (tmp_path / "marks" / "rac-changed").write_text(before)
    r = h.run("on", env={"SIGMOND_RAC_BY": by})
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.splitlines()[0] == OFF
    assert h.wish() == "off"
    assert h.changes() == []                                 # no enable, no timer, no repaint
    assert h.calls("setup") == []
    assert (tmp_path / "marks" / "rac-changed").read_text() == before
    assert h.state()["state"] == "off"                       # it writes the state, as status does


def test_an_operators_own_on_turns_an_off_on_as_before(tmp_path):
    def host(name):
        (tmp_path / name).mkdir()
        return Host(tmp_path / name, wish="off")

    for name, by in (("host", "host"), ("other", "anything else")):
        h = host(name)
        r = h.run("on", env={"SIGMOND_RAC_BY": by})
        assert r.returncode == 0 and h.wish() == "on", (by, r.stdout, r.stderr)
        assert f"enable --now {UNIT}" in h.changes()
    # A request through the pairing account is the operator's, whatever name it carries.
    h = host("vm")
    r = h.run("on", env={"SUDO_USER": "sigmond-remote", "SIGMOND_RAC_BY": "timer"})
    assert h.wish() == "on" and f"enable --now {UNIT}" in h.changes()


@pytest.mark.parametrize("by", ["timer", "install"])
def test_an_on_from_the_timer_or_the_installer_acts_when_no_off_stands_on_file(tmp_path, by):
    for name, wish in (("none", None), ("on", "on")):
        (tmp_path / name).mkdir()
        h = Host(tmp_path / name, wish=wish)
        r = h.run("on", env={"SIGMOND_RAC_BY": by})
        assert r.returncode == 0 and h.wish() == "on", (name, r.stdout, r.stderr)
        assert f"enable --now {UNIT}" in h.changes()


def test_on_enables_relays_and_tunnel_and_waits_for_every_declared_channel(tmp_path):
    h = Host(tmp_path, wish="off", channels=6)
    r = h.run("on")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stdout.splitlines()[-4] == ON or ON in r.stdout.splitlines()
    assert h.wish() == "on"
    assert h.changes() == [f"disable --now {RETRY_TIMER}",       # nothing left to retry
                           "enable --now " + " ".join(SOCKETS),
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
    assert "has not registered yet: no gateway has answered" in r.stdout
    assert "tries again every 10 minutes and brings the tunnel up when one does" in r.stdout
    # The wish stands and the timer keeps trying; nothing else gets enabled.
    assert h.changes() == [f"enable --now {RETRY_TIMER}",
                           "start --no-block sigmond-issue.service"]
    assert h.state()["state"] == "down" and h.state()["registered"] == "no"


def test_on_that_registers_disarms_the_retry_timer(tmp_path):
    h = Host(tmp_path, registered=False, wish="on")
    (tmp_path / "gateway-answers").touch()
    r = h.run("on")
    assert r.returncode == 0
    assert h.changes()[0] == f"disable --now {RETRY_TIMER}"
    assert not any(c.startswith("enable") and RETRY_TIMER in c for c in h.changes())


def test_off_leaves_the_retry_timer_alone(tmp_path):
    # A station that turned the switch off before it could register still
    # registers when a gateway answers, and stops there.  So `off` never
    # disarms the timer.
    h = Host(tmp_path, registered=False, wish="on")
    h.run("off")
    assert not any(RETRY_TIMER in c for c in h.changes())


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
    assert [c for c in h.calls() if c.startswith("is-active") and c.endswith(UNIT)]
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
    # Registration may only TRY-restart, and it must do so for EVERY session:
    # a station that re-registers and reloads one gateway's tunnel while the
    # other keeps the old configuration is half-registered, which is worse
    # than either state alone because both look healthy.
    assert 'for _u in ${RAC_UNITS:-sigmond-rac-host.service}; do' in outside
    assert 'systemctl try-restart "$_u"' in outside                        # registration only


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
INSTALLER_SYSTEMCTL = "#!/bin/sh\n# stub: the installer reloads systemd; the test touches none\nexit ${SYSTEMCTL_RC:-0}\n"
WORKING = "#!/bin/bash\necho the switch that works\nexit 0\n"
# every unit the installer writes: the retry pair and the pairing's three
HOST_UNITS = ["sigmond-rac-pair.path", "sigmond-rac-pair.service", "sigmond-rac-pair.timer",
              "sigmond-rac-register.service", "sigmond-rac-register.timer"]


def installer_text(target: Path) -> str:
    """rac_install_host_files and the helper after it, with every path moved to `target`."""
    i = WIZARD.index("RAC_SWITCH=/usr/local/sbin/sigmond-rac-switch\n")
    j = WIZARD.index("# ── RAC on/off switches", i)
    return (WIZARD[i:j].replace("/usr/local/sbin/sigmond-rac-switch", str(target))
            .replace("/etc/systemd/system", str(target.parent / "units")))


def run_installer(tmp_path, *, old=True, mode=None, nbytes=None, chmod_fails=False,
                  units="dir", blocked=None, systemctl_rc=0):
    """`units`: "dir" is a units directory that takes writes, "file" puts a regular file where
    the directory belongs.  `blocked` names a unit file to occupy with a directory.  Both fail
    a write for root as well.  `systemctl_rc` is the status the stub systemctl exits with."""
    stub = tmp_path / "stub"
    stub.mkdir()
    for name, body in (("cat", CAT), ("chmod", CHMOD), ("systemctl", INSTALLER_SYSTEMCTL)):
        (stub / name).write_text(body)
        (stub / name).chmod(0o755)
    if units == "file":
        (tmp_path / "units").write_text("not a directory\n")
    else:
        (tmp_path / "units").mkdir()
    if blocked:
        (tmp_path / "units" / blocked).mkdir()
    target = tmp_path / "sigmond-rac-switch"
    if old:
        target.write_text(WORKING)
        target.chmod(0o755)
    (tmp_path / "installer.sh").write_text(installer_text(target))
    env = {"PATH": f"{stub}:{os.environ['PATH']}", "REAL_CAT": shutil.which("cat"),
           "REAL_CHMOD": shutil.which("chmod")}
    if chmod_fails:
        env["CHMOD_FAILS"] = "1"
    env["SYSTEMCTL_RC"] = str(systemctl_rc)
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
    assert sorted(f.name for f in (tmp_path / "units").iterdir()) == HOST_UNITS


def test_the_retry_service_keeps_systemds_own_lines_out_of_the_journal(tmp_path):
    # systemd logs "Starting", "Started" and "Deactivated" for every run of a
    # oneshot, which at one try in ten minutes floods the journal.  The two
    # settings leave the script's own line, the only one worth keeping.
    run_installer(tmp_path)
    text = (tmp_path / "units" / "sigmond-rac-register.service").read_text()
    service = text.split("[Service]", 1)[1].split("\n[", 1)[0].splitlines()
    assert "LogLevelMax=notice" in service
    assert "SyslogLevel=notice" in service


@pytest.mark.parametrize("fault", [
    {"units": "file"}, {"blocked": "sigmond-rac-register.service"},
    {"blocked": "sigmond-rac-register.timer"}, {"blocked": "sigmond-rac-pair.service"},
    {"blocked": "sigmond-rac-pair.timer"}, {"blocked": "sigmond-rac-pair.path"}],
    ids=["no-units-directory", "service-unit-unwritable", "timer-unit-unwritable",
         "pair-service-unwritable", "pair-timer-unwritable", "pair-path-unwritable"])
def test_a_unit_that_cannot_be_written_fails_the_installer(tmp_path, fault):
    r, target, aside = run_installer(tmp_path, **fault)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert target.read_text() == switch_text()       # the switch itself stands


def test_a_systemd_that_will_not_reload_does_not_fail_the_installer(tmp_path):
    # `systemctl enable` reloads by itself, so a failed daemon-reload costs nothing.
    r, target, aside = run_installer(tmp_path, systemctl_rc=1)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "daemon-reload" in r.stderr and r.stderr.count("\n") == 1
    assert target.read_text() == switch_text()
    assert oct(target.stat().st_mode & 0o777) == "0o755"
    assert sorted(f.name for f in (tmp_path / "units").iterdir()) == HOST_UNITS


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


# -- a unit inside its restart delay still counts as running --------------------------

def test_a_tunnel_unit_that_systemd_calls_activating_counts_as_running(tmp_path):
    # Between two tries systemd reports the unit as `activating`.  "The tunnel
    # service does not run" would then flip in and out of the answer every 30 seconds.
    h = Host(tmp_path, wish="on", enabled=True, channels=0)
    (tmp_path / "activating").touch()
    r = h.run("status")
    assert r.returncode == 3
    assert "The tunnel service runs, and the gateway has accepted none of its 6 channels" in r.stdout
    assert "does not run" not in r.stdout
    assert h.state()["tunnel"] == "running"


def test_a_unit_that_is_neither_active_nor_activating_does_not_run(tmp_path):
    h = Host(tmp_path, wish="on", enabled=True, channels=6)
    r = h.run("status")
    assert "The tunnel service does not run" in r.stdout
    assert h.state()["tunnel"] == "stopped"


# -- the state file never shows two writers at once -----------------------------------

STATE_KEYS = ["state", "wish", "wish_source", "registered", "tunnel", "channels_up",
              "channels_declared", "rac", "gateway", "tier", "changed_at", "changed_by", "checked_at"]
DATE_GATE = r"""#!/bin/bash
# stub: a date that waits.  It says it was reached, then holds until the test lets it go.
touch "$HOSTDIR/at-date.$WRITER"
while [ ! -e "$HOSTDIR/go.$WRITER" ]; do "$REAL_SLEEP" 0.02; done
exec "$REAL_DATE" "$@"
"""


def test_the_state_file_is_written_through_a_name_of_the_writers_own():
    body = switch_text()
    assert '"$RUN_DIR/rac-state.new.$$"' in body
    assert '"$RUN_DIR/rac-state.new"' not in body


def test_two_writers_at_once_leave_a_state_file_that_parses(tmp_path):
    # `status` runs from the panel, the heartbeat and an operator with no lock
    # between them.  Writer A has written twelve lines and stands at its last
    # one when writer B starts and writes twelve shorter ones; then A finishes,
    # then B.  Through one shared temporary name that leaves B's lines, a run of
    # NUL bytes and two last lines in the file.
    h = Host(tmp_path, wish="on", enabled=True, active=True, channels=6)
    (tmp_path / "marks" / "rac-tier").write_text("a tier whose name runs long enough to move the last line\n")
    short = tmp_path / "marks-b"
    short.mkdir()
    (short / "rac-desired").write_text("on\n")
    (tmp_path / "bin" / "date").write_text(DATE_GATE)
    (tmp_path / "bin" / "date").chmod(0o755)
    env = {"PATH": f"{tmp_path}/bin:{os.environ['PATH']}", "HOSTDIR": str(tmp_path),
           "SIGMOND_RAC_MARK_DIR": f"{tmp_path}/marks", "SIGMOND_RAC_TOML": f"{tmp_path}/frpc-host.toml",
           "SIGMOND_RAC_RUN_DIR": f"{tmp_path}/run",
           "REAL_DATE": shutil.which("date"), "REAL_SLEEP": shutil.which("sleep")}

    def start(writer, **more):
        p = subprocess.Popen([str(h.script), "status"], env=dict(env, WRITER=writer, **more),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(500):
            if (tmp_path / f"at-date.{writer}").exists():
                return p
            subprocess.run([env["REAL_SLEEP"], "0.02"])
        p.kill()
        raise AssertionError(f"writer {writer} never reached its last line")

    a = start("A")
    b = start("B", SIGMOND_RAC_MARK_DIR=str(short))
    (tmp_path / "go.A").touch()
    a.wait(timeout=30)
    (tmp_path / "go.B").touch()
    b.wait(timeout=30)
    raw = (tmp_path / "run" / "rac-state").read_bytes()
    assert b"\x00" not in raw
    lines = raw.decode().splitlines()
    assert [ln.split("=", 1)[0] for ln in lines] == STATE_KEYS, lines
    assert sorted(f.name for f in (tmp_path / "run").iterdir()) == ["rac-state"]      # no temp file left


# -- the forced command takes nothing from its caller but the word --------------------

SYSTEMCTL_WITNESS = r"""#!/bin/bash
# stub: what a program started by the switch inherits
{ echo "CALL $*"
  env | grep '^SIGMOND_' | sed 's/^/ENV /'
  echo "STDIN $(head -c 64 | tr -d '\n')"
  echo "FD9 $(readlink /proc/$$/fd/9 2>/dev/null)"
  [ -e "$HOSTDIR/run/rac-switch.lock" ] && { flock -n "$HOSTDIR/run/rac-switch.lock" true && echo "LOCK free" || echo "LOCK held"; }
} >> "$HOSTDIR/witness"
exit 3
"""


def witness(tmp_path, *args, env=None, stdin="left over on stdin\n"):
    h = Host(tmp_path, registered=True, wish="on")
    (tmp_path / "bin" / "systemctl").write_text(SYSTEMCTL_WITNESS)
    r = h.run(*args, env=env, input=stdin)
    w = tmp_path / "witness"
    return r, (w.read_text().splitlines() if w.exists() else [])


@FORCED
def test_in_forced_mode_no_sigmond_variable_reaches_the_switch_or_a_program_it_starts(tmp_path):
    # sigmond-setup, which the switch starts to register, reads a dozen SIGMOND_
    # variables the switch itself never names: a gateway, a VM id, a port.
    sent = {"SSH_ORIGINAL_COMMAND": "status", "SIGMOND_RAC_SERVER": "gateway.example.invalid",
            "SIGMOND_VMID": "999", "SIGMOND_SOMETHING_NEW": "x"}
    r, seen = witness(tmp_path, env=sent)
    assert r.returncode in (0, 3), (r.stdout, r.stderr)
    assert r.stdout.splitlines()[0] in (ON, OFF, DOWN)
    assert [ln for ln in seen if ln.startswith("CALL")], "the switch started no program to witness"
    assert [ln for ln in seen if ln.startswith("ENV")] == []
    assert not (tmp_path / "run" / "rac-state").exists()     # SIGMOND_RAC_RUN_DIR went with the rest


@FORCED
def test_in_forced_mode_stdin_reads_as_empty_inside_a_child(tmp_path):
    r, seen = witness(tmp_path, env={"SSH_ORIGINAL_COMMAND": "status"})
    stdins = [ln for ln in seen if ln.startswith("STDIN")]
    assert stdins and set(stdins) == {"STDIN "}, seen


def test_the_witness_can_see_a_variable_and_stdin_when_the_switch_takes_an_argument(tmp_path):
    # The control: with one argument the same stub sees both, so the two tests
    # above can fail.
    r, seen = witness(tmp_path, "status", env={"SIGMOND_SOMETHING_NEW": "x"})
    assert "ENV SIGMOND_SOMETHING_NEW=x" in seen
    assert "STDIN left over on stdin" in seen


def test_forced_mode_names_no_list_of_variables_to_forget():
    body = switch_text()
    forced = body[body.index('if [ "$#" -eq 0 ]; then'):body.index('elif [ "$#" -eq 1 ]; then')]
    assert 'for _v in "${!SIGMOND_@}"; do unset "$_v"; done' in forced
    assert forced.index("exec </dev/null") < forced.index("${!SIGMOND_@}") < forced.index("SSH_ORIGINAL_COMMAND")


# -- the lock stays in the switch, out of every program it starts ---------------------

@pytest.mark.parametrize("word", ["on", "off"])
def test_a_program_the_switch_starts_holds_no_copy_of_its_lock(tmp_path, word):
    # An orphaned child that held fd 9 kept the lock after the switch had died.
    r, seen = witness(tmp_path, word, stdin="")
    assert [ln for ln in seen if ln.startswith("CALL")], (r.stdout, r.stderr)
    assert {ln for ln in seen if ln.startswith("FD9")} == {"FD9 "}, seen
    assert {ln for ln in seen if ln.startswith("LOCK")} == {"LOCK held"}, seen      # and the switch still holds it


# -- the legacy single-tunnel unit -------------------------------------------------

def _two_gateways(h):
    # The wizard's layout: one config per gateway, frpc-host.toml a symlink to
    # the first.  install-host.sh has enabled the legacy unit.
    for gw in ("vpn1", "vpn2"):
        (h.d / f"frpc-host-{gw}.toml").write_text(TOML)
    (h.d / "frpc-host.toml").unlink()
    (h.d / "frpc-host.toml").symlink_to("frpc-host-vpn1.toml")
    (h.d / "enabled").touch()


@pytest.mark.parametrize("word", ["on", "off"])
def test_the_switch_retires_the_legacy_unit_once_per_gateway_configs_exist(tmp_path, word):
    # v3.83 nested Phase D: off left sigmond-rac-host.service enabled, and the
    # symlink satisfies its condition, so it would start at the next boot.
    h = Host(tmp_path)
    _two_gateways(h)
    h.run(word)
    assert f"disable --now {UNIT}" in h.changes()
    assert f"enable --now {UNIT}" not in h.changes()
    assert not (tmp_path / "enabled").exists()
    verb = "enable" if word == "on" else "disable"
    for gw in ("vpn1", "vpn2"):
        assert f"{verb} --now sigmond-rac-host@{gw}.service" in h.changes()


def test_a_single_gateway_station_keeps_its_legacy_unit(tmp_path):
    # No per-gateway config: the legacy unit IS the tunnel, and on enables it.
    h = Host(tmp_path)
    h.run("on")
    assert f"enable --now {UNIT}" in h.changes()
    assert f"disable --now {UNIT}" not in h.changes()
