"""The wizard chooses a small, deliberate set of time sources.

Measured on AI6VN during the v3.83 bring-up (2026-10-29), on the odroid NAT64
gateway: the station reached ~30 distinct public NTP servers, from three pools
at once -- pool.ntp.org, 2.debian.pool.ntp.org (the distro default), Google,
Cloudflare and five NIST hosts.  Nothing of ours had ever written an NTP line;
that is simply what Debian ships.

Thirty destinations is thirty firewall exceptions at a site that grants them
one at a time, and at South Pole public NTP may be unreachable entirely.  So
the wizard asks for a local server, offers whatever DHCP advertised as the
default, and otherwise writes a pool capped with chrony's own `maxsources`.
"""
import re
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"


def shell_func(name: str) -> str:
    out, depth, keep = [], 0, False
    for ln in WIZARD.read_text().splitlines(True):
        if not keep and ln.startswith(f"{name}() {{"):
            keep = True
        if keep:
            out.append(ln)
            depth += ln.count("{") - ln.count("}")
            if depth <= 0:
                break
    assert out, f"{name} not found in the wizard"
    return "".join(out)


def run(script: str, cwd=None) -> str:
    p = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, timeout=30, cwd=cwd)
    return p.stdout + p.stderr


DEFAULTS = 'NTP_LOCAL=""; NTP_PUBLIC_POOL="pool.ntp.org"; NTP_MAXSOURCES=4\n'


def dropin(local: str = "") -> str:
    return run(textwrap.dedent(f"""
        NTP_LOCAL="{local}"; NTP_PUBLIC_POOL="pool.ntp.org"; NTP_MAXSOURCES=4
        {shell_func("ntp_dropin_body")}
        ntp_dropin_body
    """))


def test_public_default_is_a_capped_pool_not_thirty_servers():
    out = dropin()
    assert "pool pool.ntp.org iburst maxsources 4" in out
    assert "debian.pool.ntp.org" not in out
    # One source line, not a list of named servers.
    assert len([l for l in out.splitlines()
                if l.startswith(("pool ", "server "))]) == 1


def test_a_local_server_displaces_every_public_one():
    out = dropin("ntp.southpole.usap.gov")
    assert "server ntp.southpole.usap.gov iburst" in out
    assert "pool" not in out, "a local server must not leave a public pool behind"


def test_dhcp_option_42_is_detected(tmp_path):
    lease = tmp_path / "var" / "lib" / "dhcp"
    lease.mkdir(parents=True)
    (lease / "dhclient.eth0.leases").write_text(
        "lease {\n  option ntp-servers 10.1.2.3;\n}\n")
    script = textwrap.dedent(f"""
        {shell_func("ntp_from_dhcp").replace("/var/lib/dhcp", str(lease))}
        ntp_from_dhcp
    """)
    assert run(script).strip() == "10.1.2.3"


def test_no_dhcp_offer_detects_nothing(tmp_path):
    empty = tmp_path / "none"
    empty.mkdir()
    script = textwrap.dedent(f"""
        {shell_func("ntp_from_dhcp").replace("/var/lib/dhcp", str(empty))}
        ntp_from_dhcp
    """)
    assert run(script).strip() == ""


def apply_against(tmp_path, conf_text: str, runs: int = 1) -> str:
    """Run the generated apply script against a throwaway /etc/chrony."""
    root = tmp_path / f"r{runs}"
    etc = root / "etc" / "chrony"
    etc.mkdir(parents=True)
    (etc / "chrony.conf").write_text(conf_text)
    body = shell_func("ntp_apply_script")
    script = textwrap.dedent(f"""
        {body}
        ntp_apply_script
    """)
    generated = run(script)
    generated = (generated
                 .replace("/etc/chrony", str(etc))
                 .replace("/root/chrony-backups", str(root / "backups")))
    for _ in range(runs):
        run(generated)
    return (etc / "chrony.conf").read_text()


DISTRO = textwrap.dedent("""\
    # Welcome to the chrony configuration file.
    pool 2.debian.pool.ntp.org iburst
    server time.cloudflare.com iburst
    driftfile /var/lib/chrony/chrony.drift
    makestep 1 3
""")


def test_distro_sources_are_commented_out(tmp_path):
    after = apply_against(tmp_path, DISTRO)
    for line in ("pool 2.debian.pool.ntp.org iburst",
                 "server time.cloudflare.com iburst"):
        assert f"#{line}" in after, f"{line!r} is still live"
    assert "driftfile /var/lib/chrony/chrony.drift" in after, \
        "a non-source line was commented out"
    assert "makestep 1 3" in after


def test_applying_twice_does_not_double_comment(tmp_path):
    # The two runs use separate roots, so the absolute path in the appended
    # confdir line differs; normalise it away rather than compare noise.
    norm = lambda t: re.sub(r"^confdir .*$", "confdir <dir>", t, flags=re.M)
    once = norm(apply_against(tmp_path, DISTRO, runs=1))
    twice = norm(apply_against(tmp_path, DISTRO, runs=2))
    assert once == twice, "the apply step is not idempotent"
    assert "##" not in twice
    assert twice.count("confdir") == 1, "confdir appended more than once"


def test_confdir_is_ensured(tmp_path):
    after = apply_against(tmp_path, DISTRO)
    assert re.search(r"^confdir .*chrony/conf\.d$", after, re.M), \
        "chrony would never read the drop-in"


def test_backup_is_written_outside_the_config_directory(tmp_path):
    apply_against(tmp_path, DISTRO)
    etc = tmp_path / "r1" / "etc" / "chrony"
    # ⛔ chrony reads every *.conf in conf.d; a backup left in the config tree
    # would be LOADED.  This is the dnsmasq mistake of 2026-10-28.
    strays = [p for p in etc.rglob("*") if p.is_file()
              and p.name != "chrony.conf"
              and not p.name.endswith("10-sigmond-ntp.conf")]
    assert not strays, f"backup or stray file inside the config tree: {strays}"
    assert list((tmp_path / "r1" / "backups").glob("chrony.conf.*")), \
        "no backup was taken at all"


def test_both_planes_get_the_same_text():
    """The PM is where most of the public NTP traffic came from.  A VM-only
    change would leave every one of those destinations in place."""
    src = shell_func("ntp_apply_both_planes")
    m = re.search(r'sh "\$f".*?gexec \d+ "\$\(cat "\$f"\)"', src, re.S)
    assert m, "the host and the VM no longer apply the same generated script"
    # The wizard body and `--ntp` both go through it.
    assert WIZARD.read_text().count("ntp_apply_both_planes") >= 3


# ── `sigmond-setup --ntp` (2026-10-31) ─────────────────────────────────────
# A station installed before v3.84 never saw the question, and a full wizard
# rerun on a station in service is out of the question.  `--ntp <host|none>`
# sets the same drop-in on both planes and reruns sigmond-site-timing in the
# VM, which declares a vetted LAN stratum-1 server to hf-timestd as T4.

def _first_line(pattern: str) -> int:
    for i, ln in enumerate(WIZARD.read_text().splitlines(), 1):
        if re.match(pattern, ln):
            return i
    raise AssertionError(f"{pattern!r} not in the wizard")


def test_ntp_helpers_are_defined_before_the_option_block():
    # The option block runs and exits before the wizard's body; a helper
    # defined below it does not exist yet when `--ntp` runs.
    block = _first_line(r'^case "\$\{1:-\}" in')
    for fn in ("ntp_from_dhcp", "ntp_dropin_body", "ntp_apply_script",
               "ntp_normalize", "ntp_apply_both_planes"):
        assert _first_line(rf"^{fn}\(\) \{{") < block, fn


def normalize(arg: str) -> str:
    return run(textwrap.dedent(f"""
        {shell_func("ntp_normalize")}
        ntp_normalize '{arg}'; echo "[$?]"
    """)).strip()


def test_normalize_names_none_and_strips_junk():
    assert normalize("192.168.8.144") == "192.168.8.144[0]"
    assert normalize("ntp.southpole.usap.gov") == "ntp.southpole.usap.gov[0]"
    assert normalize("none") == "[0]"
    assert normalize("NONE") == "[0]"
    # Refused, not stripped: "10.0.0.1rm-rf" is no server anyone named.
    assert normalize("10.0.0.1; rm -rf /") == "[1]"
    assert normalize("") == "[1]", "an empty answer must not mean 'none'"


def test_apply_both_planes_writes_host_then_vm(tmp_path):
    log = tmp_path / "calls"
    script = textwrap.dedent(f"""
        NTP_LOCAL="192.168.8.144"; NTP_PUBLIC_POOL="pool.ntp.org"; NTP_MAXSOURCES=4
        LOG={tmp_path}/wiz.log
        say() {{ echo "say: $*" >> {log}; }}
        sh() {{ echo "host-sh: $(grep -c 'server 192.168.8.144 iburst' "$1")" >> {log}; }}
        gexec() {{ shift; echo "gexec: $(printf '%s' "$*" | grep -c 'server 192.168.8.144 iburst')" >> {log}; }}
        {shell_func("ntp_dropin_body")}
        {shell_func("ntp_apply_script")}
        {shell_func("ntp_apply_both_planes")}
        TMPDIR={tmp_path} ntp_apply_both_planes; echo "rc=$?" >> {log}
    """)
    run(script)
    calls = log.read_text()
    assert "host-sh: 1" in calls, calls
    assert "gexec: 1" in calls, calls
    assert "rc=0" in calls, calls


def test_ntp_option_reruns_site_timing_in_the_vm():
    text = WIZARD.read_text()
    case = text[text.index("    --ntp)"):]
    case = case[:case.index(";;")]
    assert "ntp_normalize" in case and "ntp_apply_both_planes" in case
    assert "/usr/local/sbin/sigmond-site-timing" in case
