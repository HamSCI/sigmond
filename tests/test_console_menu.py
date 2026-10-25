"""`sigmond-console-menu`, the root menu at the station console (v3.82 row 3).

The menu script and its profile hook are heredocs the wizard writes to
/usr/local/sbin/sigmond-console-menu and /etc/profile.d/sigmond-console-menu.sh,
cut out of the shipped wizard with pm-align's own reader.  Every command the
script runs comes from a PATH the test builds: stubs for whiptail, the station
tools and the network, plus links to the few base tools bash needs.  Nothing
here touches the real console, network or services."""
import importlib.util
import os
import pty
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROXMOX = REPO / "scripts" / "proxmox"
_spec = importlib.util.spec_from_file_location("pm_align_for_menu", PROXMOX / "pm-align.py")
pm_align = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pm_align
_spec.loader.exec_module(pm_align)

_DOCS = pm_align.extract_heredocs((PROXMOX / "sigmond-wizard.sh").read_text())
MENU = _DOCS["/usr/local/sbin/sigmond-console-menu"]
HOOK = _DOCS["/etc/profile.d/sigmond-console-menu.sh"]

# Base tools the script may use, linked from the real system.  whiptail and
# the station tools never come from here, so "not installed" is testable.
BASE = ("bash", "cat", "awk", "head", "sed", "printf", "sleep", "tty", "tr", "timeout")

# Each stub logs its name and arguments, one line per call.
LOGGED = 'printf "%s\\n" "$(basename "$0") $*" >> "$FAKE_LOG"\n'
STUBS = {
    # One answer per call from $FAKE_ANSWERS (space-separated); ESC = exit 255.
    # The tag goes to stderr, as whiptail writes it.
    "whiptail": ('printf "%s\\n" "whiptail $*" >> "$FAKE_LOG"\n'
                 'n=$(cat "$SCENE/calls" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$SCENE/calls"\n'
                 'set -- $FAKE_ANSWERS; a=$(eval "echo \\${$n:-ESC}")\n'
                 '[ "$a" = ESC ] && exit 255\n'
                 'printf %s "$a" >&2; exit 0'),
    "sigmond-check": LOGGED + 'exit "$(cat "$SCENE/check.rc" 2>/dev/null || echo 0)"',
    "sigmond-setup": LOGGED + "exit 0",
    "sigmond-rac-switch": LOGGED + 'cat "$SCENE/rac"; [ -e "$SCENE/rac.hang" ] && exec sleep 30; exit 0',
    "ip": ('case "$1" in -4) cat "$SCENE/route4" 2>/dev/null ;; -6) cat "$SCENE/route6" 2>/dev/null ;; esac'),
    "ping": LOGGED + 'for a; do :; done; grep -qxF -- "$a" "$SCENE/answers" 2>/dev/null',
    "getent": ('f="$SCENE/getent.$1"; [ -e "$f" ] || exit 2; cat "$f"'),
    "hostname": 'echo pm-host',
    "id": 'echo "${FAKE_UID:-0}"',
}


def scene(tmp_path, answers="", rac="Remote access: on\n", conf="AC0G-B4 EN34 2026-10-20\n",
          with_rac=True, with_whiptail=True, check_rc=0, rac_hang=False,
          route4="default via 192.168.1.1 dev vmbr0 proto dhcp src 192.168.1.50 metric 100\n",
          route6="default via fe80::1 dev vmbr0 proto ra metric 1024 expires 1798sec hoplimit 64 pref medium\n",
          answers_ping=("192.168.1.1", "fe80::1%vmbr0"),
          getent=(("ahostsv4", "140.82.112.3    STREAM github.com\n140.82.112.3    DGRAM\n"),)):
    s = tmp_path / "scene"
    s.mkdir()
    (s / "rac").write_text(rac)
    if rac_hang:
        (s / "rac.hang").write_text("")
    (s / "check.rc").write_text(str(check_rc))
    if route4:
        (s / "route4").write_text(route4)
    if route6:
        (s / "route6").write_text(route6)
    (s / "answers").write_text("".join(a + "\n" for a in answers_ping))
    for db, text in getent:
        (s / f"getent.{db}").write_text(text)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in BASE:
        real = shutil.which(name)
        assert real, f"the test needs {name} on this machine"
        (bin_ / name).symlink_to(real)
    for name in ("basename", "grep"):
        (bin_ / name).symlink_to(shutil.which(name))
    for name, body in STUBS.items():
        if (name == "sigmond-rac-switch" and not with_rac) or (name == "whiptail" and not with_whiptail):
            continue
        (bin_ / name).write_text("#!/bin/bash\n" + body + "\n")
        (bin_ / name).chmod(0o755)
    cf = tmp_path / "configured"
    if conf is not None:
        cf.write_text(conf)
    script = tmp_path / "sigmond-console-menu"
    script.write_text(MENU)
    script.chmod(0o755)
    log = tmp_path / "log"
    log.write_text("")
    env = {"PATH": str(bin_), "SCENE": str(s), "FAKE_LOG": str(log), "FAKE_ANSWERS": answers,
           "SIGMOND_MENU_CONF": str(cf), "SIGMOND_MENU_TTY": "/dev/tty1", "HOME": str(tmp_path)}
    return script, env, log


def run(script, env, *args, **extra):
    e = dict(env, **extra)
    return subprocess.run([shutil.which("bash"), str(script), *args], env=e, input="",
                          capture_output=True, text=True, timeout=30)


def lib(script, env, call, **extra):
    """Source the script as a library and run one of its functions."""
    e = dict(env, SIGMOND_MENU_LIB="1", **extra)
    return subprocess.run([shutil.which("bash"), "-c", f'. "$1"; {call}', "x", str(script)],
                          env=e, input="", capture_output=True, text=True, timeout=30)


def whiptail_calls(log):
    return [l for l in log.read_text().splitlines() if l.startswith("whiptail ")]


# -- is this login at the console? -------------------------------------------

def kind(tmp_path, tty, ssh):
    script, env, _ = scene(tmp_path)
    r = lib(script, env, "console_kind", SIGMOND_MENU_TTY=tty, SSH_CONNECTION=ssh)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_tty1_is_console(tmp_path):
    assert kind(tmp_path, "/dev/tty1", "") == "console"


def test_split_console_is_console(tmp_path):
    # The PM has no keyboard; the VM's console bridge logs in over the /30.
    assert kind(tmp_path, "/dev/pts/0", "10.99.0.2 50422 10.99.0.1 22") == "console"


def test_lan_ssh_is_not_console(tmp_path):
    assert kind(tmp_path, "/dev/pts/0", "192.168.1.20 50422 192.168.1.50 22") == "other"


def test_rac_ssh_is_not_console(tmp_path):
    # frp delivers the RAC session from 127.0.0.1.
    assert kind(tmp_path, "/dev/pts/0", "127.0.0.1 40000 127.0.0.1 22") == "other"


def test_a_pts_without_ssh_is_not_console(tmp_path):
    assert kind(tmp_path, "/dev/pts/3", "") == "other"


def test_login_mode_off_console_draws_nothing(tmp_path):
    script, env, log = scene(tmp_path, answers="check shell")
    r = run(script, env, "--login", SIGMOND_MENU_TTY="/dev/pts/0",
            SSH_CONNECTION="192.168.1.20 50422 192.168.1.50 22")
    assert r.returncode == 0
    assert log.read_text() == ""


def test_login_mode_at_the_console_draws_the_menu(tmp_path):
    script, env, log = scene(tmp_path, answers="shell")
    r = run(script, env, "--login", SIGMOND_MENU_TTY="/dev/pts/0",
            SSH_CONNECTION="10.99.0.2 50422 10.99.0.1 22")
    assert r.returncode == 0
    assert len(whiptail_calls(log)) == 1


# -- the title ---------------------------------------------------------------

def test_title_names_the_station(tmp_path):
    script, env, log = scene(tmp_path, answers="shell")
    run(script, env)
    (call,) = whiptail_calls(log)
    assert "--title Sigmond station AC0G-B4 " in call


def test_title_falls_back_to_hostname(tmp_path):
    script, env, log = scene(tmp_path, answers="shell", conf=None)
    run(script, env)
    (call,) = whiptail_calls(log)
    assert "--title Sigmond station pm-host " in call


# -- the items ---------------------------------------------------------------

def test_check_runs_sigmond_check_then_returns(tmp_path):
    script, env, log = scene(tmp_path, answers="check shell")
    r = run(script, env)
    assert r.returncode == 0
    lines = log.read_text().splitlines()
    assert "sigmond-check " in lines
    assert len(whiptail_calls(log)) == 2
    assert "Press Enter to return to the menu" in r.stdout


def test_settings_runs_reconfigure(tmp_path):
    script, env, log = scene(tmp_path, answers="settings shell")
    run(script, env)
    assert "sigmond-setup --reconfigure" in log.read_text().splitlines()


def test_remote_offers_off_when_on(tmp_path):
    script, env, log = scene(tmp_path, answers="remote shell", rac="Remote access: on\n")
    run(script, env)
    first = whiptail_calls(log)[0]
    assert "Remote access off" in first and "Remote access on" not in first
    assert "sigmond-setup --rac-off" in log.read_text().splitlines()


def test_remote_offers_off_when_wanted_but_not_up(tmp_path):
    script, env, log = scene(tmp_path, answers="shell", rac="Remote access: wanted on, not up\nreason\n")
    run(script, env)
    (call,) = whiptail_calls(log)
    assert "Remote access off" in call and "Remote access on" not in call


def test_remote_offers_on_when_off(tmp_path):
    script, env, log = scene(tmp_path, answers="remote shell", rac="Remote access: off\n")
    run(script, env)
    first = whiptail_calls(log)[0]
    assert "Remote access on" in first and "Remote access off" not in first
    assert "sigmond-setup --rac-on" in log.read_text().splitlines()


def test_remote_offers_both_when_state_unknown(tmp_path):
    script, env, log = scene(tmp_path, answers="remote-off shell", with_rac=False)
    run(script, env)
    first = whiptail_calls(log)[0]
    assert " remote-on Remote access on (state unknown) " in first
    assert " remote-off Remote access off (state unknown) " in first
    assert "sigmond-setup --rac-off" in log.read_text().splitlines()


def test_an_unknown_status_line_offers_both(tmp_path):
    script, env, _ = scene(tmp_path, rac="sigmond-rac-switch: something odd\n")
    assert lib(script, env, "rac_actions").stdout.strip() == "on off"


def test_a_hung_rac_status_is_bounded(tmp_path):
    script, env, log = scene(tmp_path, answers="shell", rac_hang=True, rac="")
    t0 = time.monotonic()
    r = run(script, env, SIGMOND_MENU_RAC_TIMEOUT="1")
    assert time.monotonic() - t0 < 5
    assert r.returncode == 0
    (call,) = whiptail_calls(log)
    assert "remote-on" in call and "remote-off" in call


# -- the network test --------------------------------------------------------

def test_net_test_reports_all_four_lines(tmp_path):
    script, env, log = scene(tmp_path)
    out = lib(script, env, "net_test").stdout.splitlines()
    assert out[0] == "IPv4 gateway 192.168.1.1: answers"
    assert out[1] == "IPv6 gateway fe80::1%vmbr0: answers"
    assert out[2].startswith("DNS (IPv4 answer) github.com: 140.82.")
    assert out[3] == "DNS (IPv6 answer) github.com: FAILED"
    assert len(out) == 4
    pings = [l for l in log.read_text().splitlines() if l.startswith("ping ")]
    assert pings == ["ping -4 -c 2 -W 2 192.168.1.1", "ping -6 -c 2 -W 2 fe80::1%vmbr0"]


def test_net_test_names_a_gateway_that_does_not_answer(tmp_path):
    script, env, _ = scene(tmp_path, answers_ping=())
    out = lib(script, env, "net_test").stdout.splitlines()
    assert out[0] == "IPv4 gateway 192.168.1.1: does NOT answer"
    assert out[1] == "IPv6 gateway fe80::1%vmbr0: does NOT answer"


def test_net_test_names_a_missing_default_route(tmp_path):
    script, env, _ = scene(tmp_path, route4="", route6="")
    out = lib(script, env, "net_test").stdout.splitlines()
    assert "none (no IPv4 default route)" in out[0]
    assert "none (no IPv6 default route)" in out[1]


def test_the_net_item_runs_from_the_menu(tmp_path):
    script, env, _ = scene(tmp_path, answers="net shell")
    r = run(script, env)
    assert "IPv4 gateway 192.168.1.1: answers" in r.stdout


# -- leaving the menu --------------------------------------------------------

def test_shell_returns_zero_without_running_anything(tmp_path):
    script, env, log = scene(tmp_path, answers="shell")
    r = run(script, env)
    assert r.returncode == 0
    ran = [l for l in log.read_text().splitlines()
           if not l.startswith(("whiptail ", "sigmond-rac-switch "))]
    assert ran == []


def test_esc_returns_zero(tmp_path):
    script, env, log = scene(tmp_path, answers="ESC")
    r = run(script, env)
    assert r.returncode == 0
    assert len(whiptail_calls(log)) == 1


def test_a_failing_item_returns_to_the_menu(tmp_path):
    script, env, log = scene(tmp_path, answers="check shell", check_rc=3)
    r = run(script, env)
    assert r.returncode == 0
    assert len(whiptail_calls(log)) == 2


def test_no_whiptail_falls_through_to_the_shell(tmp_path):
    script, env, log = scene(tmp_path, with_whiptail=False)
    r = run(script, env)
    assert r.returncode == 0
    assert r.stdout.strip().splitlines() == ["sigmond-console-menu: whiptail not installed; plain shell"]


def test_menu_text_is_ascii():
    # The bridge paints raw bytes onto a TERM=linux VT.
    assert all(b < 0x80 for b in MENU.encode())
    assert all(b < 0x80 for b in HOOK.encode())


# -- the profile hook --------------------------------------------------------

def hook_scene(tmp_path):
    script, env, log = scene(tmp_path)
    fake_menu = tmp_path / "fake-menu"
    fake_menu.write_text('#!/bin/bash\nprintf "%s\\n" "menu $*" >> "$FAKE_LOG"\n')
    fake_menu.chmod(0o755)
    hook = tmp_path / "hook.sh"
    hook.write_text(HOOK)
    env = dict(env, SIGMOND_MENU_BIN=str(fake_menu))
    return hook, env, log


def run_hook_on_a_tty(hook, env, *bash_args):
    """Run bash with stdin on a pseudo-terminal, as a real login has."""
    master, slave = pty.openpty()
    try:
        r = subprocess.run([shutil.which("bash"), *bash_args, f'. "{hook}"'], env=env,
                           stdin=slave, capture_output=True, text=True, timeout=30)
    finally:
        os.close(slave)
        os.close(master)
    return r


def test_profile_hook_calls_the_menu_for_a_root_login_shell(tmp_path):
    hook, env, log = hook_scene(tmp_path)
    r = run_hook_on_a_tty(hook, env, "--noprofile", "-l", "-c")
    assert r.returncode == 0, r.stderr
    assert log.read_text().splitlines() == ["menu --login"]


def test_profile_hook_runs_only_for_root_login_shells(tmp_path):
    hook, env, log = hook_scene(tmp_path)
    runs = [
        run_hook_on_a_tty(hook, env, "--noprofile", "-c"),                                   # not a login shell
        run_hook_on_a_tty(hook, dict(env, SIGMOND_MENU_SHOWN="1"), "--noprofile", "-l", "-c"),  # already shown
        run_hook_on_a_tty(hook, dict(env, FAKE_UID="1000"), "--noprofile", "-l", "-c"),        # not root
        subprocess.run([shutil.which("bash"), "--noprofile", "-l", "-c", f'. "{hook}"'], env=env,  # no tty
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30),
    ]
    for r in runs:
        assert r.returncode == 0 and r.stderr == "", r.stderr
    assert log.read_text() == ""


def test_profile_hook_marks_the_session_shown(tmp_path):
    hook, env, _ = hook_scene(tmp_path)
    master, slave = pty.openpty()
    try:
        r = subprocess.run([shutil.which("bash"), "--noprofile", "-l", "-c", f'. "{hook}"; echo "shown=$SIGMOND_MENU_SHOWN"'],
                           env=env, stdin=slave, capture_output=True, text=True, timeout=30)
    finally:
        os.close(slave)
        os.close(master)
    assert "shown=1" in r.stdout
