"""The pairing: the decoder VM asks, the Proxmox host alone acts
(tasks/plan-remote-access.md section 2).

`sigmond-setup --pair-vm` gives the VM one ssh key, and gives that key one
thing it can run on the host: sigmond-rac-switch, with one of its three words.
These tests run the wizard's own functions, cut out of the shipped script by
name.  The two scripts the host sends into the guest run here for real, in a
scratch directory.  The rig proves the rest against a real sshd and a real
sudo (sigmond-appliance test-nested-v3.sh).
"""
import base64
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
# A mutation gets tried on a copy of the wizard, never in the checkout: name the copy here.
WIZARD = Path(os.environ.get("SIGMOND_TEST_WIZARD") or REPO / "scripts" / "proxmox" / "sigmond-wizard.sh")
TEXT = WIZARD.read_text()

HOSTKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostHostHostHostHostHostHostHostHostHostHost"
VMKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIVmVmVmVmVmVmVmVmVmVmVmVmVmVmVmVmVmVmVmVmVm"
PAIR_FUNCS = ("rac_pair_key_ok", "rac_have_sudo", "rac_pair_sudoers", "rac_pair_authorized_line",
              "rac_pair_guest_script", "rac_pair_verify_script", "rac_pair_record", "rac_pair_locked",
              "rac_pair_vm")
COULD_NOT_INSTALL = "could not install the remote-access files on this host (the switch or its units)"
STAMP = r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ"
ARMED = "systemctl enable --now sigmond-rac-pair.timer sigmond-rac-pair.path"
NOT_STARTED = "the pairing's re-assertion units did not start (systemctl enable --now failed)"
VISUDO = shutil.which("visudo") or ("/usr/sbin/visudo" if os.path.exists("/usr/sbin/visudo") else None)


def shell_func(name: str) -> str:
    """The function `name`, cut out of the wizard: from its first line to the first `}` that
    stands alone at the left margin outside a heredoc.  The guest scripts the wizard sends
    hold such a line of their own."""
    out, keep, delim = [], False, None
    for ln in TEXT.splitlines(True):
        if ln.startswith(f"{name}(){{") or ln.startswith(f"{name}() {{"):
            keep = True
        if keep:
            out.append(ln)
            if delim:
                if ln.rstrip("\n") == delim:
                    delim = None
                continue
            m = re.search(r"<<-?\s*['\"]?([A-Z]+)['\"]?", ln)
            if m:
                delim = m.group(1)
                continue
            if ln.rstrip() == "}" or (len(out) == 1 and ln.rstrip().endswith("}")):
                break
    assert out, f"{name} not found in the wizard"
    return "".join(out)


def heredoc(path: str) -> str:
    m = re.search(r"cat > %s <<'([A-Z]+)'[^\n]*\n(.*?)\n\1\n" % re.escape(path), TEXT, re.S)
    assert m, f"the wizard writes no {path}"
    return m.group(2) + "\n"


PRELUDE = textwrap.dedent("""
    set -u
    RAC_SWITCH=/usr/local/sbin/sigmond-rac-switch
    RAC_PAIR_USER=sigmond-remote
    RAC_PAIR_FAIL=""
    RAC_PAIR_WARN=""
""")


def bash(script, env=None, **kw):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, **(env or {})), **kw)


def funcs(*names):
    return PRELUDE + "".join(shell_func(n) for n in names)


# -- the key line: one key, one script ----------------------------------------------

def test_the_key_line_restricts_the_key_to_the_switch_and_to_the_vms_address():
    r = bash(funcs("rac_pair_key_ok", "rac_pair_authorized_line")
             + f"rac_pair_authorized_line 10.99.0.2 '{VMKEY}'\n")
    assert r.stdout == ('restrict,from="10.99.0.2",command="sudo -n /usr/local/sbin/sigmond-rac-switch" '
                        f"{VMKEY} sigmond-rac-switch\n")


BAD_KEYS = [
    "", "ssh-ed25519", "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC7" + "A" * 60,
    VMKEY + ' x" command="/bin/sh', VMKEY + "\n" + VMKEY, "no-pty " + VMKEY,
    "ssh-ed25519 AAAA\u00c3\u00a2\u00c2\u0080\u00c2\u0094" + "A" * 60,     # mojibake from the agent
    "ssh-ed25519 " + "A" * 300,
]


@pytest.mark.parametrize("key", BAD_KEYS, ids=range(len(BAD_KEYS)))
def test_anything_but_one_ed25519_public_key_writes_no_key_line(key):
    r = bash(funcs("rac_pair_key_ok", "rac_pair_authorized_line")
             + 'rac_pair_authorized_line 10.99.0.2 "$KEY"; echo "rc=$?"\n', env={"KEY": key})
    assert r.stdout == "rc=1\n"


# -- the sudoers lines: one account, one script, no argument ---------------------------

def test_the_sudoers_lines_allow_the_switch_with_no_argument_and_keep_one_variable():
    out = bash(funcs("rac_pair_sudoers") + "rac_pair_sudoers\n").stdout
    rules = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
    assert rules == [
        "Defaults:sigmond-remote env_reset",
        'Defaults:sigmond-remote env_keep = "SSH_ORIGINAL_COMMAND"',
        "Defaults:sigmond-remote !requiretty",
        'sigmond-remote ALL=(root) NOPASSWD: /usr/local/sbin/sigmond-rac-switch ""',
    ]


@pytest.mark.skipif(VISUDO is None, reason="needs visudo")
def test_visudo_accepts_the_sudoers_lines(tmp_path):
    f = tmp_path / "sigmond-remote"
    f.write_text(bash(funcs("rac_pair_sudoers") + "rac_pair_sudoers\n").stdout)
    f.chmod(0o440)
    r = subprocess.run([VISUDO, "-c", "-q", "-f", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# -- what the host sends into the guest ----------------------------------------------------

def guest_script(host="10.99.0.1", key=HOSTKEY):
    return bash(funcs("rac_pair_guest_script") + f"rac_pair_guest_script '{host}' '{key}'\n").stdout


def verify_script(host="10.99.0.1"):
    return bash(funcs("rac_pair_verify_script") + f"rac_pair_verify_script {host}\n").stdout


REAL_PATHS = ("/etc/sigmond", "/etc/update-motd.d", "/var/lib/sigmond")


def sandbox(tmp_path, script, redirect):
    """Run a guest script the way the wizard sends it: piped to `bash` on stdin, not
    as a `bash -c` argument.  A command inside the script that reads stdin then reads the
    script itself.  Paths point at scratch directories; chown needs root, so stub it, and
    let the stub read stdin as a careless command would.

    `redirect` names the path lines the script must hold, each exactly once: D, M or C.
    A script that gains a path line, or loses one, fails here instead of reaching the
    real /etc/sigmond."""
    d, m, b = tmp_path / "etc-sigmond", tmp_path / "motd.d", tmp_path / "bin"
    for p in (m, b):
        p.mkdir(exist_ok=True)
    (b / "chown").write_text("#!/bin/sh\ncat >/dev/null\nexit 0\n")
    (b / "chown").chmod(0o755)
    where = {"D": ("D=/etc/sigmond\n", f"D={d}\n"),
             "M": ("M=/etc/update-motd.d\n", f"M={m}\n"),
             "C": ("C=/var/lib/sigmond/remote-access.json\n", f"C={tmp_path}/cache/remote-access.json\n")}
    for key in redirect:
        old, new = where[key]
        assert script.count(old) == 1, f"the script must hold {old!r} exactly once"
        script = script.replace(old, new)
    for real in REAL_PATHS:
        assert real not in script, f"the script still names the real {real}"
    r = subprocess.run(["bash"], input=script, capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, PATH=f"{b}:{os.environ['PATH']}"))
    return r, d, m


@pytest.mark.parametrize("which", ["guest", "verify"])
def test_each_guest_script_is_one_function_called_with_no_stdin(which):
    # The guest's bash reads the script from a pipe.  It reads a whole function
    # before it runs any of it, so no command inside can take the rest of the script.
    lines = (guest_script() if which == "guest" else verify_script()).splitlines()
    assert lines[0] == "main(){" and lines[-2] == "}" and lines[-1] == "main </dev/null"
    assert not any(ln.startswith("main(){") for ln in lines[1:])


def test_every_byte_sent_into_the_guest_is_ascii():
    # Proxmox sends a guest-agent request that holds a non-ASCII character
    # short, and about one time in six the agent then answers nothing more.
    for script in (guest_script(), verify_script()):
        assert script and all(ord(c) < 128 for c in script)
    body = "".join(shell_func(n) for n in PAIR_FUNCS)
    assert all(ord(c) < 128 for c in body), "a pairing function holds a non-ASCII byte"
    assert body.count("| base64 -w0) | base64 -d | bash") == 2      # both scripts travel encoded


def test_the_guest_script_makes_a_root_only_key_and_pins_the_host(tmp_path):
    r, d, m = sandbox(tmp_path, guest_script(), "DM")
    assert r.returncode == 0, r.stdout + r.stderr
    assert stat.S_IMODE((d / "rac-switch.key").stat().st_mode) == 0o600
    assert (d / "rac-switch.key.pub").read_text().startswith("ssh-ed25519 ")
    assert (d / "rac-switch.known_hosts").read_text() == f"10.99.0.1 {HOSTKEY}\n"
    hook = (m / "72-sigmond-remote").read_text()
    assert "smd remote motd" in hook and os.access(m / "72-sigmond-remote", os.X_OK)


def test_a_second_run_keeps_the_key_and_takes_up_a_new_host_key(tmp_path):
    sandbox(tmp_path, guest_script(), "DM")
    first = (tmp_path / "etc-sigmond" / "rac-switch.key.pub").read_text()
    restored = HOSTKEY.replace("Host", "Rest")              # an identity restore changed it
    r, d, _ = sandbox(tmp_path, guest_script(key=restored), "DM")
    assert r.returncode == 0
    assert (d / "rac-switch.key.pub").read_text() == first
    assert (d / "rac-switch.known_hosts").read_text() == f"10.99.0.1 {restored}\n"


def test_half_a_key_pair_gets_replaced_by_a_whole_one(tmp_path):
    sandbox(tmp_path, guest_script(), "DM")
    d = tmp_path / "etc-sigmond"
    (d / "rac-switch.key.pub").unlink()
    r, d, _ = sandbox(tmp_path, guest_script(), "DM")
    assert r.returncode == 0
    pub = subprocess.run(["ssh-keygen", "-y", "-f", str(d / "rac-switch.key")],
                         capture_output=True, text=True).stdout.split()[:2]
    assert (d / "rac-switch.key.pub").read_text().split()[:2] == pub


VERIFY_CASES = [
    ("on", 0, "Remote access: on\n  ...\n", 0, "on"),
    ("off", 0, "Remote access: off\n  ...\n", 0, "off"),
    ("down", 3, "Remote access: wanted on, not up\n  ...\n", 0, "down"),
    ("on-after-a-warning", 0, "Warning: a line ssh printed first\nRemote access: on\n  ...\n", 0, "on"),
    ("down-after-a-warning", 3, "something on stderr\nRemote access: wanted on, not up\n", 0, "down"),
    ("host-key-failed", 255, "Host key verification failed.\n", 1, None),
    ("permission-denied", 255, "sigmond-remote@10.99.0.1: Permission denied (publickey).\n", 1, None),
    ("refused", 2, "sigmond-rac-switch: refused.\n", 1, None),
    ("right-words-wrong-exit", 1, "Remote access: on\n", 1, None),
    ("some-other-exit", 4, "Remote access: on\n", 1, None),
    ("something-else", 0, "something else entirely\n", 1, None),
    ("first-state-line-unknown", 0, "Remote access: banana\nRemote access: on\n", 1, None),
]


@pytest.mark.parametrize("ssh_rc,ssh_out,want_rc,want_state", [c[1:] for c in VERIFY_CASES],
                         ids=[c[0] for c in VERIFY_CASES])
def test_the_verify_script_passes_only_on_an_answer_from_the_switch(tmp_path, ssh_rc, ssh_out,
                                                                    want_rc, want_state):
    (tmp_path / "bin").mkdir()
    ssh = tmp_path / "bin" / "ssh"
    (tmp_path / "ssh.out").write_text(ssh_out)
    # This ssh reads stdin to the end whatever its flags, as a real client does without -n.
    ssh.write_text(f"#!/bin/bash\ncat >/dev/null\necho \"$*\" > {tmp_path}/ssh.args\n"
                   f"cat {tmp_path}/ssh.out\nexit {ssh_rc}\n")
    ssh.chmod(0o755)
    r, _, _ = sandbox(tmp_path, verify_script(), "DC")
    assert r.returncode == want_rc, r.stdout + r.stderr
    cache = tmp_path / "cache" / "remote-access.json"
    if want_state is None:
        assert not cache.exists()
    else:
        doc = json.loads(cache.read_text())
        assert doc["state"] == want_state
        assert re.fullmatch(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ", doc["checked_at"])
    args = (tmp_path / "ssh.args").read_text()
    for must in ("BatchMode=yes", "IdentitiesOnly=yes", "StrictHostKeyChecking=yes",
                 "GlobalKnownHostsFile=/dev/null", "sigmond-remote@10.99.0.1 status"):
        assert must in args, must
    assert re.search(r"(^| )-n( |$)", args), "ssh must take -n"
    assert "-F /dev/null" in args, "ssh must read no config file"


def test_the_proof_travels_the_path_smd_remote_travels():
    # smd remote runs `ssh -T -F /dev/null -i ...` (sigmond.commands.remote.ssh_argv).  A `Host *`
    # block in an ssh config would add an identity file, SendEnv and GSSAPI to one call and
    # not the other, so the pairing's proof ignores every config file too.
    assert "ssh -n -T -F /dev/null -i " in verify_script()


def test_a_refused_request_shows_why_in_the_failure_output(tmp_path):
    (tmp_path / "bin").mkdir()
    ssh = tmp_path / "bin" / "ssh"
    ssh.write_text("#!/bin/bash\necho 'Permission denied (publickey).'\nexit 255\n")
    ssh.chmod(0o755)
    r, _, _ = sandbox(tmp_path, verify_script(), "DC")
    assert r.returncode == 1 and "Permission denied (publickey)." in r.stdout


# -- the whole pairing, with the host's commands stubbed ---------------------------------------

def pair(tmp_path, *, slash30="10.99.0.1/30", other30="", agent=0, sudo="yes", apt_installs=False,
         vmkey=VMKEY, gexec_rcs="0 0", install_rc=0, hostpub=HOSTKEY + " root@pm\n", user_exists=False,
         chown_rc=0, enable_rc=0, lock_held=False, tail=None, real_mgmt_vm_ip=False, marks="dir",
         reset_heals=False):
    """`tail` is the last thing the script runs; by default one pairing, then its result.
    `lock_held` holds the pairing lock from this process, as another pairing would.
    `slash30` is the /30 address vmbr1 carries; `other30` is one on another interface, which
    `ip` lists first.  `real_mgmt_vm_ip` runs the wizard's own mgmt_vm_ip against the same `ip`.
    `marks="file"` puts a regular file where the mark directory belongs, so nothing can be
    written under it (root included).  `reset_heals`: one parser reset brings a silent
    agent back (`agent` then gives only the answer before that reset)."""
    (tmp_path / "home").mkdir(exist_ok=True)
    if marks == "dir":
        (tmp_path / "marks").mkdir(exist_ok=True)
    elif not (tmp_path / "marks").exists():
        (tmp_path / "marks").write_text("not a directory\n")
    for stale in ("calls", "gexec.args", "timeouts", "body.fds", "body.lock", "resets"):
        (tmp_path / stale).unlink(missing_ok=True)
    if hostpub is not None:
        (tmp_path / "host.pub").write_text(hostpub)
    script = funcs(*PAIR_FUNCS) + textwrap.dedent(f"""
        VMID=100
        LOG={tmp_path}/wizard.log
        MARK_DIR={tmp_path}/marks
        RAC_PAIR_HOME={tmp_path}/home
        RAC_PAIR_SUDOERS={tmp_path}/sudoers.d-sigmond-remote
        RAC_PAIR_HOSTPUB={tmp_path}/host.pub
        RAC_PAIR_LOCK={tmp_path}/run/pair.lock
        C={tmp_path}/calls
        say(){{ echo "[wizard] $*"; }}
        sleep(){{ :; }}
        timeout(){{ echo "$1 $2" >> {tmp_path}/timeouts; shift; "$@"; }}
        mgmt_vm_ip(){{ echo 10.99.0.2; }}
        ip(){{
            echo "ip $*" >> {tmp_path}/ip.calls
            case " $* " in
                *" dev vmbr1 "*) ;;
                *) [ -n "{other30}" ] && echo "5: wg0    inet {other30} scope global wg0" ;;
            esac
            [ -n "{slash30}" ] && echo "7: vmbr1    inet {slash30} brd 10.99.0.3 scope global vmbr1"
            return 0
        }}
        AGENT_RC={agent}
        qm(){{ echo "qm $*" >> $C; return $AGENT_RC; }}
        qga_reset(){{ echo "reset $1" >> {tmp_path}/resets; [ {int(reset_heals)} = 1 ] || return 1; AGENT_RC=0; }}
        qga_note_reset(){{ echo "note $1" >> {tmp_path}/resets; }}
        rac_have_sudo(){{ [ -e {tmp_path}/sudo-present ]; }}
        apt-get(){{ echo "apt-get $*" >> $C; case "$*" in *install*) [ {int(apt_installs)} = 1 ] && touch {tmp_path}/sudo-present;; esac; }}
        rac_install_host_files(){{
            echo install-host-files >> $C
            # What a program started inside the locked body inherits, and whether the lock holds.
            ls -l /proc/self/fd > {tmp_path}/body.fds 2>/dev/null
            flock -n "$RAC_PAIR_LOCK" true && echo free > {tmp_path}/body.lock || echo held > {tmp_path}/body.lock
            return {install_rc}
        }}
        id(){{ [ {int(user_exists)} = 1 ]; }}
        useradd(){{ echo "useradd $*" >> $C; }}
        usermod(){{ echo "usermod $*" >> $C; }}
        chown(){{ echo "chown $*" >> $C; return {chown_rc}; }}
        visudo(){{ echo "visudo $*" >> $C; {VISUDO or 'true'} "$@"; }}
        systemctl(){{ echo "systemctl $*" >> $C; case "$*" in *enable*) return {enable_rc};; esac; }}
        N=0
        gexec(){{
            N=$((N+1)); shift
            printf '%s\\n' "$*" >> {tmp_path}/gexec.args
            echo "gexec $N" >> $C
            set -- {gexec_rcs}
            eval "return \\${{$N}}"
        }}
        guest_out(){{ echo "guest_out $2" >> $C; printf '%s\\n' "$VMKEY_LINE"; }}
    """)
    if real_mgmt_vm_ip:
        script += shell_func("mgmt_vm_ip")
    if sudo == "yes":
        (tmp_path / "sudo-present").touch()
    held = None
    if lock_held:
        (tmp_path / "run").mkdir(exist_ok=True)
        held = os.open(tmp_path / "run" / "pair.lock", os.O_CREAT | os.O_RDWR)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        r = bash(script + (tail or 'rac_pair_vm; echo "rc=$? fail=$RAC_PAIR_FAIL warn=$RAC_PAIR_WARN"\n'),
                 env={"VMKEY_LINE": vmkey})
    finally:
        if held is not None:
            os.close(held)
    calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
    return r, calls


def test_a_deaf_agent_gets_one_reset_and_the_pairing_goes_on(tmp_path):
    """tasks/plan-guest-agent-requests.md section 4: one reset before any verdict."""
    r, calls = pair(tmp_path, agent=1, reset_heals=True)
    assert "rc=0 fail=" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "resets").read_text() == "reset 100\nnote pairing\n"
    assert [c for c in calls if c.startswith("qm agent")] == ["qm agent 100 ping"] * 2
    assert (tmp_path / "marks" / "rac-pairing").read_text().startswith("paired ")


def test_an_agent_that_stays_silent_gets_one_reset_and_no_more(tmp_path):
    r, calls = pair(tmp_path, agent=1)
    assert "fail=the guest agent in VM 100 does not answer" in r.stdout
    assert (tmp_path / "resets").read_text() == "reset 100\n"
    assert len([c for c in calls if c.startswith("qm agent")]) == 12


def test_an_agent_that_answers_gets_no_reset_from_the_pairing(tmp_path):
    pair(tmp_path)
    assert not (tmp_path / "resets").exists()


def test_pairing_writes_both_halves_and_then_proves_them(tmp_path):
    r, calls = pair(tmp_path)
    assert "rc=0 fail=" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "home" / ".ssh" / "authorized_keys").read_text() == (
        'restrict,from="10.99.0.2",command="sudo -n /usr/local/sbin/sigmond-rac-switch" '
        f"{VMKEY} sigmond-rac-switch\n")
    assert 'NOPASSWD: /usr/local/sbin/sigmond-rac-switch ""' in (tmp_path / "sudoers.d-sigmond-remote").read_text()
    assert not (tmp_path / "sudoers.d-sigmond-remote.new").exists()
    order = [c.split()[0] + (" " + c.split()[1] if c.startswith(("gexec", "systemctl")) else "")
             for c in calls]
    assert order == ["install-host-files", "systemctl enable", "qm", "useradd", "usermod", "chown",
                     "visudo", "gexec 1", "guest_out", "chown", "gexec 2"]
    assert calls[1] == ARMED                     # armed first: a pairing that fails tries again
    assert "useradd --system --home-dir" in calls[3] and "--shell /bin/sh sigmond-remote" in calls[3]
    assert calls[4] == "usermod -p * -s /bin/sh sigmond-remote"


def test_what_travels_through_the_guest_agent_is_ascii_and_carries_the_hosts_key(tmp_path):
    pair(tmp_path, hostpub=HOSTKEY + " root@caf\u00e9-PM \u2014 the host\n")
    args = (tmp_path / "gexec.args").read_text().splitlines()
    assert len(args) == 2
    for a in args:
        assert all(ord(c) < 128 for c in a)
        assert re.fullmatch(r"echo [A-Za-z0-9+/=]+ \| base64 -d \| bash", a), a[:60]
    sent = base64.b64decode(args[0].split()[1]).decode()
    assert f"printf '%s %s\\n' '10.99.0.1' '{HOSTKEY}'" in sent


def test_pairing_again_changes_nothing_but_the_keys_it_reads(tmp_path):
    pair(tmp_path)
    newkey = VMKEY.replace("Vm", "Nw")
    r, calls = pair(tmp_path, vmkey=newkey, user_exists=True)
    assert "rc=0" in r.stdout
    assert not any(c.startswith("useradd") for c in calls)
    ak = (tmp_path / "home" / ".ssh" / "authorized_keys").read_text()
    assert ak.count("\n") == 1 and newkey in ak and VMKEY not in ak      # one key, never two


ARMING = ["install-host-files", "systemctl"]
# A host that fails by its structure (no /30 link, no readable sshd key) never pairs, so it
# arms nothing: the units would fail at every boot and every six hours for ever.
STRUCTURAL = ["install-host-files"]


@pytest.mark.parametrize("kw,reason,touched", [
    (dict(slash30=""), "no private link to its decoder VM", STRUCTURAL),
    (dict(hostpub=None), "cannot read this host's sshd key", STRUCTURAL),
    (dict(hostpub="ssh-ed25519 AAAA\u00e9\u00e9 x\n"), "cannot read this host's sshd key", STRUCTURAL),
    (dict(agent=1), "the guest agent in VM 100 does not answer", ARMING + ["qm"]),
    (dict(sudo="no"), "this host has no sudo and could not install it", ARMING + ["qm", "apt-get"]),
    (dict(install_rc=1), COULD_NOT_INSTALL, ["install-host-files"]),
], ids=["no-slash-30", "no-host-key", "host-key-not-ascii", "agent-silent", "no-sudo", "no-switch"])
def test_pairing_refuses_early_and_says_why(tmp_path, kw, reason, touched):
    r, calls = pair(tmp_path, **kw)
    assert "rc=1" in r.stdout and reason in r.stdout, r.stdout
    assert sorted(set(c.split()[0] for c in calls)) == sorted(set(touched))
    assert not (tmp_path / "home" / ".ssh" / "authorized_keys").exists()
    assert not (tmp_path / "gexec.args").exists()


@pytest.mark.parametrize("kw,reason", [
    (dict(slash30=""), "this host has no private link to its decoder VM (no /30 address)"),
    (dict(hostpub=None), "cannot read this host's sshd key"),
    (dict(hostpub="ssh-ed25519 AAAA\u00e9\u00e9 x\n"), "cannot read this host's sshd key"),
], ids=["no-slash-30", "no-host-key", "host-key-not-ascii"])
def test_a_host_that_fails_by_structure_arms_no_unit(tmp_path, kw, reason):
    # The re-assertion units are armed before every step that can fail by chance and after the
    # two that fail by structure.  Such a host can never pair, so it gets no unit that would
    # fail at every boot.
    r, calls = pair(tmp_path, **kw)
    assert f"rc=1 fail={reason}" in r.stdout, r.stdout
    assert not [c for c in calls if c.startswith("systemctl")], calls
    assert "install-host-files" in calls              # the files still land first


def test_a_host_without_sudo_installs_it_once_and_pairs(tmp_path):
    r, calls = pair(tmp_path, sudo="no", apt_installs=True)
    assert "rc=0" in r.stdout, r.stdout
    assert [c for c in calls if c.startswith("apt-get")] == ["apt-get -qq update", "apt-get -y -qq install sudo"]
    assert "this host has no sudo; installing it" in r.stdout


def test_the_apt_calls_of_a_host_without_sudo_run_under_a_timeout(tmp_path):
    # The re-assertion reaches these lines on every run of such a host.
    pair(tmp_path, sudo="no", apt_installs=True)
    assert (tmp_path / "timeouts").read_text().splitlines() == ["10 qm", "120 apt-get", "300 apt-get"]


def test_every_ping_of_the_guest_agent_runs_under_a_timeout_of_ten_seconds(tmp_path):
    # `qm agent ping` can hang on a channel that has gone stale, and the pairing's
    # service would then sit on its lock until systemd killed it.
    _, calls = pair(tmp_path)
    assert (tmp_path / "timeouts").read_text().splitlines() == ["10 qm"]
    assert "qm agent 100 ping" in calls
    r, _ = pair(tmp_path, agent=1)
    assert "does not answer" in r.stdout
    assert (tmp_path / "timeouts").read_text().splitlines() == ["10 qm"] * 12


def test_only_the_keys_two_fields_reach_the_key_line(tmp_path):
    # Whatever follows the key in the VM's answer never reaches the file.
    r, _ = pair(tmp_path, vmkey=VMKEY + ' x",command="/bin/sh')
    assert "rc=0" in r.stdout
    ak = (tmp_path / "home" / ".ssh" / "authorized_keys").read_text()
    assert ak.endswith(f"{VMKEY} sigmond-rac-switch\n") and "/bin/sh" not in ak


@pytest.mark.parametrize("vmkey", ["", "ssh-rsa AAAA" + "B" * 80, "ssh-ed25519 short"],
                         ids=["nothing", "rsa", "too-short"])
def test_a_bad_answer_from_the_vm_leaves_the_earlier_key_line_in_place(tmp_path, vmkey):
    pair(tmp_path)
    before = (tmp_path / "home" / ".ssh" / "authorized_keys").read_text()
    r, calls = pair(tmp_path, vmkey=vmkey, user_exists=True)
    assert "rc=1 fail=VM 100 returned no usable public key" in r.stdout
    assert (tmp_path / "home" / ".ssh" / "authorized_keys").read_text() == before
    assert "gexec 2" not in calls


def test_a_pairing_that_does_not_answer_is_reported_as_not_paired(tmp_path):
    r, calls = pair(tmp_path, gexec_rcs="0 1")
    assert "rc=1" in r.stdout
    assert "a request from the VM did not reach the switch" in r.stdout


# -- a pairing that fails tries again by itself ---------------------------------------------------

def test_a_pairing_whose_guest_agent_never_answers_still_ends_with_the_units_enabled(tmp_path):
    r, calls = pair(tmp_path, agent=1)
    assert "rc=1" in r.stdout and "does not answer" in r.stdout
    assert ARMED in calls


def test_a_pairing_whose_proof_fails_still_ends_with_the_units_enabled(tmp_path):
    r, calls = pair(tmp_path, gexec_rcs="0 1")
    assert "rc=1" in r.stdout
    assert ARMED in calls


def test_the_enable_call_names_exactly_the_two_pairing_units(tmp_path):
    # Never the tunnel unit, never a relay socket, never the registration timer:
    # only the switch script touches those.
    for kw in (dict(), dict(gexec_rcs="0 1"), dict(agent=1)):
        _, calls = pair(tmp_path, **kw)
        assert [c for c in calls if c.startswith("systemctl")] == [ARMED]
    commands = [ln for ln in shell_func("rac_pair_locked").splitlines() if re.match(r"\s*(if !\s+)?systemctl\b", ln)]
    assert len(commands) == 1 and "sigmond-rac-pair.timer sigmond-rac-pair.path" in commands[0]


def test_units_that_will_not_start_leave_the_result_to_the_proof_and_say_so(tmp_path):
    r, _ = pair(tmp_path, enable_rc=1)
    assert f"rc=0 fail= warn={NOT_STARTED}" in r.stdout, r.stdout
    r, _ = pair(tmp_path, enable_rc=1, gexec_rcs="0 1")
    assert "rc=1" in r.stdout and f"warn={NOT_STARTED}" in r.stdout


def test_the_warning_starts_empty_at_every_call(tmp_path):
    r, _ = pair(tmp_path, tail='RAC_PAIR_WARN=stale; rac_pair_vm; echo "warn=[$RAC_PAIR_WARN]"\n')
    assert "warn=[]" in r.stdout


def test_the_lock_is_free_again_when_a_pairing_returns(tmp_path):
    for kw in (dict(), dict(agent=1), dict(install_rc=1)):
        r, _ = pair(tmp_path, tail=f'rac_pair_vm; flock -n {tmp_path}/run/pair.lock true && echo free || echo held\n',
                    **kw)
        assert r.stdout.endswith("free\n"), (kw, r.stdout)


def test_a_pairing_that_finds_another_one_running_does_nothing(tmp_path):
    r, calls = pair(tmp_path, lock_held=True)
    assert "rc=2 fail=another pairing is running" in r.stdout, r.stdout
    assert calls == []                                       # no guest agent, no useradd, no systemctl
    assert not (tmp_path / "gexec.args").exists()
    assert not (tmp_path / "home" / ".ssh" / "authorized_keys").exists()


# -- what `sigmond-setup --pair-vm` says ----------------------------------------------------------

def branch_tail():
    i = TEXT.index("    --pair-vm)")
    branch = TEXT[i:TEXT.index("    --rac-off|--rac-on)", i)]
    return 'set -- --pair-vm\ncase "${1:-}" in\n' + branch + "esac\necho NOT-REACHED\n"


# Every pm-align in the field from v3.71 on reads these three lines of `--pair-vm` (the
# `paired:` line, the `another pairing is running` line and the `NOT paired:` line), so their
# text is a contract.  Change a word here and a fielded pm-align reads the answer wrong.

def test_pair_vm_says_paired_and_exits_0(tmp_path):
    r, _ = pair(tmp_path, tail=branch_tail())
    assert r.returncode == 0 and "paired: the decoder VM can run smd remote on" in r.stdout
    assert "NOT" not in r.stdout and "WARN" not in r.stdout


def test_pair_vm_says_why_it_did_not_pair_and_exits_1(tmp_path):
    r, _ = pair(tmp_path, gexec_rcs="0 1", tail=branch_tail())
    assert r.returncode == 1 and "NOT paired: both halves stand on disk" in r.stdout


def test_pair_vm_exits_0_when_another_pairing_runs(tmp_path):
    r, calls = pair(tmp_path, lock_held=True, tail=branch_tail())
    assert r.returncode == 0 and "another pairing is running; nothing done" in r.stdout
    assert "paired:" not in r.stdout and calls == []


@pytest.mark.parametrize("kw,rc", [(dict(), 0), (dict(gexec_rcs="0 1"), 1)], ids=["paired", "not-paired"])
def test_pair_vm_prints_the_warning_whatever_the_outcome(tmp_path, kw, rc):
    r, _ = pair(tmp_path, enable_rc=1, tail=branch_tail(), **kw)
    assert r.returncode == rc
    assert f"[wizard] WARN: {NOT_STARTED}" in r.stdout, r.stdout


def wizard_run(rc, fail="", warn=""):
    """The lines the wizard run holds between rac_activate and PSWS_STATE, with the pairing stubbed."""
    fail = fail or ("another pairing is running" if rc == 2 else "")
    start = TEXT.index("\nrac_activate\n") + len("\nrac_activate\n")
    block = TEXT[start:TEXT.index("\nPSWS_STATE=", start)]
    script = ('set -u\nsay(){ echo "[wizard] $*"; }\n'
              f'rac_pair_vm(){{ RAC_PAIR_FAIL="{fail}"; RAC_PAIR_WARN="{warn}"; return {rc}; }}\n'
              + block + '\necho "STATE=<<$PAIR_STATE>>"\n')
    return bash(script).stdout


def test_the_wizard_run_carries_the_warning_in_both_outcomes():
    paired = wizard_run(0, warn=NOT_STARTED)
    assert "STATE=<<paired: in the decoder VM" in paired
    assert f"\n            WARN: {NOT_STARTED}>>" in paired
    assert f"[wizard] WARN: remote-access switch: {NOT_STARTED}" in paired
    failed = wizard_run(1, fail="the guest agent in VM 100 does not answer", warn=NOT_STARTED)
    assert "NOT PAIRED (the guest agent in VM 100 does not answer)" in failed
    assert f"\n            WARN: {NOT_STARTED}>>" in failed
    assert f"[wizard] WARN: remote-access switch: {NOT_STARTED}" in failed


def test_the_wizard_run_stays_quiet_when_nothing_is_wrong():
    out = wizard_run(0)
    assert "WARN" not in out and out.endswith("<<paired: in the decoder VM, smd remote on / off / status>>\n")


def test_the_wizard_run_says_that_a_pairing_runs_already_and_how_to_see_it_finish():
    # The pairing's timer starts a run of its own the moment the wizard arms it.
    # That run holds the lock and does the work, so "NOT PAIRED" would mislead.
    out = wizard_run(2)
    assert "NOT PAIRED" not in out and "STATE=<<paired:" not in out
    state = out.split("STATE=<<", 1)[1]
    assert "a pairing runs already on this host" in state
    assert "smd remote status" in state and "decoder VM" in state
    assert "sigmond-setup --pair-vm" not in state            # nothing for the reader to run
    assert "[wizard] remote-access switch: a pairing runs already on this host" in out
    assert "WARN" not in out


def test_an_account_directory_root_cannot_own_stops_the_pairing(tmp_path):
    # The account must not change its own key, so root must own its home.
    r, calls = pair(tmp_path, chown_rc=1)
    expect = "rc=1 fail=could not prepare " + str(tmp_path / "home") + " for the account (root must own it)"
    assert expect in r.stdout, r.stdout
    assert not (tmp_path / "home" / ".ssh" / "authorized_keys").exists()
    assert not (tmp_path / "gexec.args").exists()


@pytest.mark.parametrize("vmkey,ok", [
    ("welcome to the decoder\n" + VMKEY, True),                 # a profile script printed a line first
    ("two words\nssh-rsa AAAA\n" + VMKEY, True),
    ("just a line\nand another one", False),
    ("welcome to the decoder", False),
], ids=["a-line-first", "two-lines-first", "only-junk", "only-one-junk-line"])
def test_a_stray_line_from_the_guest_does_not_hide_the_key(tmp_path, vmkey, ok):
    r, _ = pair(tmp_path, vmkey=vmkey)
    if ok:
        assert "rc=0" in r.stdout
        assert (tmp_path / "home" / ".ssh" / "authorized_keys").read_text().endswith(f"{VMKEY} sigmond-rac-switch\n")
    else:
        assert "rc=1 fail=VM 100 returned no usable public key" in r.stdout


def test_a_vm_that_will_not_take_its_half_stops_the_pairing(tmp_path):
    r, calls = pair(tmp_path, gexec_rcs="1 0")
    assert "rc=1 fail=could not write the pairing into VM 100" in r.stdout
    assert "guest_out" not in " ".join(calls)


# -- where it sits, and what re-asserts it ------------------------------------------------------

def test_pair_vm_runs_before_the_wizard_waits_or_asks():
    i = TEXT.index("    --pair-vm)")
    branch = TEXT[i:TEXT.index("    --rac-off|--rac-on)", i)]
    assert "rac_pair_vm" in branch and "exit 0" in branch and "exit 1;;" in branch
    assert i < TEXT.index("waiting for decoder VM") and i < TEXT.index("ask_reporter() {")
    for name in PAIR_FUNCS + ("gexec", "guest_out", "mgmt_vm_ip", "rac_install_host_files"):
        j = TEXT.find(f"\n{name}(){{")
        j = j if j >= 0 else TEXT.index(f"\n{name}() {{")
        assert j < i, f"{name} must be defined above the dispatch"


def test_the_wizard_run_pairs_after_it_has_brought_remote_access_up():
    i = TEXT.index("\nrac_activate\n")
    j = TEXT.index("rac_pair_vm; _prc=$?\n", i)
    assert j < TEXT.index('> "$CONF_MARK"')
    assert " Switch:    $PAIR_STATE\n" in TEXT
    assert "sigmond-setup --pair-vm" in TEXT[j:j + 900]


def test_the_pairing_is_re_asserted_at_boot_and_when_the_hosts_sshd_key_changes():
    service = heredoc("/etc/systemd/system/sigmond-rac-pair.service")
    timer = heredoc("/etc/systemd/system/sigmond-rac-pair.timer")
    path = heredoc("/etc/systemd/system/sigmond-rac-pair.path")
    assert "ExecStart=/usr/local/sbin/sigmond-setup --pair-vm\n" in service
    assert "OnBootSec=4min\n" in timer
    assert "PathChanged=/etc/ssh/ssh_host_ed25519_key.pub\n" in path
    assert "Unit=sigmond-rac-pair.service\n" in path


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_the_pairing_units_parse(tmp_path):
    for name in ("sigmond-rac-pair.service", "sigmond-rac-pair.timer", "sigmond-rac-pair.path"):
        (tmp_path / name).write_text(heredoc(f"/etc/systemd/system/{name}"))
    for name in ("sigmond-rac-pair.timer", "sigmond-rac-pair.path"):
        r = subprocess.run(["systemd-analyze", "verify", str(tmp_path / name)],
                           capture_output=True, text=True, timeout=60)
        noise = [ln for ln in (r.stdout + r.stderr).splitlines()
                 if "sigmond-rac-pair" in ln and "sigmond-setup" not in ln]
        assert noise == [], noise


def test_the_pairing_key_stays_out_of_the_station_identity():
    # A reflash mints a new pairing key and the wizard pairs again.  The key
    # records whom the VM may ask, not who the station is.
    identity = (REPO / "lib" / "sigmond" / "identity.py").read_text()
    assert "rac-switch" not in identity and "sigmond-remote" not in identity


# -- which link, when the host carries more than one /30 ---------------------------------------

def test_the_pairing_takes_the_hosts_address_from_vmbr1_when_another_slash_30_comes_first(tmp_path):
    # A WireGuard or point-to-point link can carry a /30 too, and `ip` may list it first.
    # The private link to the decoder VM is vmbr1's.  The VM's address follows from it:
    # mgmt_vm_ip reads "the first /30 on this host" and would name the wrong machine.
    r, _ = pair(tmp_path, slash30="10.99.0.1/30", other30="172.16.5.1/30", real_mgmt_vm_ip=True)
    assert "rc=0 fail=" in r.stdout, r.stdout + r.stderr
    sent = base64.b64decode((tmp_path / "gexec.args").read_text().splitlines()[0].split()[1]).decode()
    assert f"printf '%s %s\\n' '10.99.0.1' '{HOSTKEY}'" in sent
    ak = (tmp_path / "home" / ".ssh" / "authorized_keys").read_text()
    assert ak.startswith('restrict,from="10.99.0.2",'), ak
    assert "ip -4 -o addr show dev vmbr1" in (tmp_path / "ip.calls").read_text().splitlines()[0]


def test_the_pairing_falls_back_to_the_first_slash_30_when_vmbr1_carries_none(tmp_path):
    r, _ = pair(tmp_path, slash30="", other30="10.99.0.1/30", real_mgmt_vm_ip=True)
    assert "rc=0 fail=" in r.stdout, r.stdout + r.stderr
    sent = base64.b64decode((tmp_path / "gexec.args").read_text().splitlines()[0].split()[1]).decode()
    assert f"printf '%s %s\\n' '10.99.0.1' '{HOSTKEY}'" in sent
    assert (tmp_path / "home" / ".ssh" / "authorized_keys").read_text().startswith('restrict,from="10.99.0.2",')


def test_an_address_set_by_hand_for_the_vm_still_wins(tmp_path):
    r, _ = pair(tmp_path, slash30="10.99.0.1/30", other30="172.16.5.1/30", real_mgmt_vm_ip=True,
                tail='SIGMOND_MGMT_VM=10.99.0.6 rac_pair_vm; echo "rc=$?"\n')
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "home" / ".ssh" / "authorized_keys").read_text().startswith('restrict,from="10.99.0.6",')


# -- the lock stays in the shell that took it ---------------------------------------------------

def test_a_program_started_inside_the_pairing_holds_no_copy_of_its_lock(tmp_path):
    # An orphaned child (a guest-agent call that hangs, an apt that never returns) that
    # inherited descriptor 7 kept the lock after its shell had died, and every later
    # pairing then answered "another pairing is running".
    r, _ = pair(tmp_path)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "body.lock").read_text() == "held\n"          # the pairing itself holds it
    fds = (tmp_path / "body.fds").read_text()
    assert "pair.lock" not in fds, fds                                # and no child carries it


# -- the outcome on file: what the console panel and pm-align read ------------------------------

def record(tmp_path):
    p = tmp_path / "marks" / "rac-pairing"
    return p.read_text() if p.exists() else None


def test_a_pairing_that_proves_itself_records_paired_and_when(tmp_path):
    r, _ = pair(tmp_path)
    assert "rc=0" in r.stdout
    assert re.fullmatch(rf"paired {STAMP}\n", record(tmp_path)), record(tmp_path)
    assert stat.S_IMODE((tmp_path / "marks" / "rac-pairing").stat().st_mode) == 0o644
    assert sorted(f.name for f in (tmp_path / "marks").iterdir()) == ["rac-pairing"]     # no .new left


@pytest.mark.parametrize("kw,reason", [
    (dict(agent=1), "the guest agent in VM 100 does not answer"),
    (dict(gexec_rcs="0 1"), "both halves stand on disk, but a request from the VM did not reach the switch"),
    (dict(slash30=""), "this host has no private link to its decoder VM (no /30 address)"),
    (dict(install_rc=1), COULD_NOT_INSTALL),
], ids=["agent-silent", "proof-failed", "no-private-link", "no-host-files"])
def test_a_pairing_that_fails_records_not_paired_and_why(tmp_path, kw, reason):
    r, _ = pair(tmp_path, **kw)
    assert "rc=1" in r.stdout
    m = re.fullmatch(rf"not-paired {STAMP} (.*)\n", record(tmp_path))
    assert m and m.group(1).startswith(reason), record(tmp_path)


def test_a_failure_replaces_an_earlier_paired_and_a_success_replaces_it_again(tmp_path):
    pair(tmp_path)
    pair(tmp_path, agent=1, user_exists=True)
    assert record(tmp_path).startswith("not-paired ")
    pair(tmp_path, user_exists=True)
    assert record(tmp_path).startswith("paired ")


def test_a_pairing_that_finds_another_one_running_records_nothing(tmp_path):
    r, _ = pair(tmp_path, lock_held=True)
    assert "rc=2" in r.stdout
    assert record(tmp_path) is None
    pair(tmp_path)                                                    # and it leaves a record alone
    before = record(tmp_path)
    pair(tmp_path, lock_held=True)
    assert record(tmp_path) == before


def test_a_lock_that_cannot_be_opened_records_not_paired(tmp_path):
    (tmp_path / "run").write_text("a file where the lock's directory belongs\n")
    r, calls = pair(tmp_path)
    assert "rc=1 fail=cannot open the pairing lock" in r.stdout, r.stdout
    assert re.fullmatch(rf"not-paired {STAMP} cannot open the pairing lock \(.*\)\n", record(tmp_path))
    assert calls == []


def test_the_reason_on_file_is_one_line_of_printable_text_no_longer_than_200(tmp_path):
    script = funcs("rac_pair_record") + (
        f'MARK_DIR={tmp_path}\n'
        "rac_pair_record not-paired \"$(printf 'first\\nsecond\\ttab\\033[2Jesc\\001%0300d' 7)\"; echo rc=$?\n")
    r = bash(script)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    raw = (tmp_path / "rac-pairing").read_bytes()
    assert raw.count(b"\n") == 1 and raw.endswith(b"\n")
    word, when, why = raw.decode().rstrip("\n").split(" ", 2)
    assert word == "not-paired" and re.fullmatch(STAMP, when)
    assert why.startswith("first second tab[2Jesc0000") and len(why) == 200
    assert all(32 <= b < 127 for b in raw[:-1])


def test_an_outcome_that_cannot_be_recorded_warns_and_keeps_the_pairings_result(tmp_path):
    r, _ = pair(tmp_path, marks="file")
    assert "rc=0 fail= warn=" in r.stdout, r.stdout
    assert "could not record the pairing's outcome" in r.stdout
    assert "the console panel" in r.stdout


def test_pair_vm_the_command_records_its_outcome_too(tmp_path):
    # `sigmond-setup --pair-vm` and the wizard's own pairing run one function.
    r, _ = pair(tmp_path, tail=branch_tail())
    assert r.returncode == 0 and record(tmp_path).startswith("paired ")
    r, _ = pair(tmp_path, gexec_rcs="0 1", user_exists=True, tail=branch_tail())
    assert r.returncode == 1 and record(tmp_path).startswith("not-paired ")
    before = record(tmp_path)
    r, _ = pair(tmp_path, lock_held=True, tail=branch_tail())
    assert r.returncode == 0 and record(tmp_path) == before
    assert "$MARK_DIR/rac-pairing" in shell_func("rac_pair_record")
    assert "rac_pair_record" in shell_func("rac_pair_vm")


# -- the banner's cache carries a tunnel that still runs while the wish reads off ----------------

SWITCH_NOTE = "  NOTE: the tunnel service runs all the same. Someone started it outside this switch; 'off' again stops it."


def verify_cache(tmp_path, ssh_out, ssh_rc=0):
    (tmp_path / "bin").mkdir(exist_ok=True)
    ssh = tmp_path / "bin" / "ssh"
    (tmp_path / "ssh.out").write_text(ssh_out)
    ssh.write_text(f"#!/bin/bash\ncat >/dev/null\ncat {tmp_path}/ssh.out\nexit {ssh_rc}\n")
    ssh.chmod(0o755)
    r, _, _ = sandbox(tmp_path, verify_script(), "DC")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads((tmp_path / "cache" / "remote-access.json").read_text())


def test_the_note_the_verify_script_looks_for_is_the_switchs_own_line():
    assert f'echo "{SWITCH_NOTE}"' in TEXT
    assert "NOTE: the tunnel service runs all the same\\." in verify_script()


def test_the_proof_records_a_tunnel_that_runs_while_the_wish_reads_off(tmp_path):
    doc = verify_cache(tmp_path, "Remote access: off\n  The operator holds it off.\n" + SWITCH_NOTE + "\n")
    assert doc["state"] == "off" and doc["tunnel_runs"] is True
    assert sorted(doc) == ["checked_at", "state", "tunnel_runs"]


@pytest.mark.parametrize("answer", [
    "Remote access: off\n  The operator holds it off.\n",
    "Remote access: on\n  The gateway accepted 6 of 6 channels.\n",
    "Remote access: on\n" + SWITCH_NOTE + "\n",                      # the note belongs to off alone
    "Remote access: off\n  a line that says NOTE: the tunnel service runs all the same. inside it\n",
], ids=["off", "on", "on-with-the-words", "words-inside-another-line"])
def test_the_proof_leaves_the_key_out_of_every_other_answer(tmp_path, answer):
    doc = verify_cache(tmp_path, answer)
    assert sorted(doc) == ["checked_at", "state"]
