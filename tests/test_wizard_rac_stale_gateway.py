"""A gateway this station no longer uses must not survive on disk.

AI6VN, 2026-10-30.  The night before, a mis-read registration error had made
the ladder fall back to gw2 and write /etc/sigmond/frpc-host-gw2.toml.  That
fallback was repaired -- unit disabled, symlink back to vpn1 -- and the next
`sigmond-setup --reconfigure`, which correctly settled on vpn1, RESURRECTED
it:

    sigmond-rac-host@gw2.service   enabled, activating
    listen tcp 127.0.0.1:7500: bind: address already in use

rac_units() enumerates units from the config files present, so a leftover
config is a unit switch_on enables on every run, for ever.  The stale file
also keeps its ORIGINAL admin port, so fixing the port table cannot reach it.

Two halves, because registration and systemd are deliberately separate
(test_wizard_rac_register: "registration never starts, stops or enables the
tunnel").  rac_write_config removes the FILES; the switch disables a unit
whose config has gone.
"""
import os
import re
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"
TEXT = WIZARD.read_text()

RACSW_BODY = re.search(
    r"cat > /usr/local/sbin/sigmond-rac-switch <<'RACSWEOF'\n(.*?)\nRACSWEOF\n",
    TEXT, re.S).group(1)


def _fn(source: str, name: str) -> str:
    out, keep = [], False
    for ln in source.splitlines(True):
        if ln.startswith("%s(){" % name) or ln.startswith("%s() {" % name):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, "%s not found" % name
    return "".join(out)


def shell_func(name):
    return _fn(TEXT, name)


def switch_fn(name):
    return _fn(RACSW_BODY, name)


def run(script, env=None):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          timeout=60, env=dict(os.environ, **(env or {})))


# ── half one: the configs ──────────────────────────────────────────────────

def station(tmp_path, existing):
    """A /etc/sigmond holding per-gateway configs for `existing`."""
    etc = tmp_path / "sigmond"
    etc.mkdir(exist_ok=True)
    for gw in existing:
        (etc / ("frpc-host-%s.toml" % gw)).write_text(
            'serverAddr = "%s"\n[webServer]\nport = 7500\n' % gw)
    return etc


def write_config(tmp_path, existing):
    etc = station(tmp_path, existing)
    (tmp_path / "ca.crt").write_text("CA\n")
    return textwrap.dedent(f"""
        set -u
        RAC_TOML={etc}/frpc-host.toml
        RAC_CA={tmp_path}/ca.crt
        RAC_CA_GW2={tmp_path}/ca.crt
        RAC_CA_HAMSCI={tmp_path}/ca.crt
        RAC_PORT_SECURE=35736
        RAC_BASE_VMSTN=48800; RAC_BASE_VMGMAG=49800
        MARK_DIR={tmp_path}
        RAC_TIER_LABEL="HamSCI secure (TOFU) vpn1"
        RAC_REGISTRAR=DIRECT
        RAC_TLS=on
        SITE="DASI-009.AI6VN"
        _hs1="vpn1.hamsci.org"; _hs2="vpn2.hamsci.org"; _hs_sni="vpn.hamsci.org"
        REG='RACN=229 RUSER=u RTOKEN=t P_VMSSH=36029 P_VMWEB=46029 P_HSSH=51029 P_HUI=56029 SRV=vpn1.hamsci.org SPORT=35736'
        say(){{ :; }}
        systemctl(){{ echo "SYSTEMCTL $*" >> {tmp_path}/systemctl.calls; }}
    """) + shell_func("rac_gateway_set") + shell_func("rac_write_config") + \
        "\nrac_write_config\nls -1 %s | tr '\\n' ' '\necho\n" % etc


def test_a_gateway_outside_this_tier_is_removed(tmp_path):
    """gw2 left over from a fallback, while the ladder settles on HamSCI."""
    r = run(write_config(tmp_path, ["gw2"]))
    assert r.returncode == 0, r.stderr
    assert "frpc-host-gw2.toml" not in r.stdout, \
        "the stale gateway survived: %s" % r.stdout


def test_the_gateways_it_just_wrote_are_kept(tmp_path):
    r = run(write_config(tmp_path, ["gw2"]))
    for gw in ("vpn1", "vpn2"):
        assert "frpc-host-%s.toml" % gw in r.stdout, (gw, r.stdout)


def test_the_symlink_still_points_at_a_file_that_exists(tmp_path):
    etc = tmp_path / "sigmond"
    run(write_config(tmp_path, ["gw2"]))
    link = etc / "frpc-host.toml"
    assert link.is_symlink() and link.resolve().exists(), \
        "the symlink target was pruned"


def test_registration_still_touches_no_units(tmp_path):
    """The existing contract: registration writes configuration, the switch
    owns systemd.  Pruning must not smuggle a disable in here."""
    run(write_config(tmp_path, ["gw2"]))
    calls = tmp_path / "systemctl.calls"
    assert not calls.exists() or not calls.read_text().strip(), \
        "rac_write_config called systemctl: %s" % calls.read_text()


# ── half two: the unit left enabled behind it ──────────────────────────────

def switch(tmp_path, configs, enabled):
    etc = station(tmp_path, configs)
    (etc / "frpc-host.toml").write_text('serverAddr = "vpn1.hamsci.org"\n')
    return textwrap.dedent(f"""
        set -u
        TOML={etc}/frpc-host.toml
        UNIT=sigmond-rac-host.service
        ENABLED="{' '.join(enabled)}"
        systemctl(){{
            case "$1" in
                list-unit-files)
                    for _e in $ENABLED; do
                        echo "sigmond-rac-host@$_e.service enabled"
                    done
                    return 0 ;;
            esac
            echo "$*" >> {tmp_path}/calls
            return 0
        }}
    """) + switch_fn("rac_units") + switch_fn("rac_prune_orphan_units") + \
        "\nrac_prune_orphan_units\n"


def test_a_unit_whose_config_is_gone_gets_disabled(tmp_path):
    run(switch(tmp_path, configs=["vpn1", "vpn2"], enabled=["vpn1", "vpn2", "gw2"]))
    calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
    assert "disable --now sigmond-rac-host@gw2.service" in calls, calls


def test_units_that_still_have_a_config_are_left_alone(tmp_path):
    run(switch(tmp_path, configs=["vpn1", "vpn2"], enabled=["vpn1", "vpn2", "gw2"]))
    calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
    for gw in ("vpn1", "vpn2"):
        assert "sigmond-rac-host@%s.service" % gw not in calls, (gw, calls)


def test_nothing_to_prune_touches_nothing(tmp_path):
    run(switch(tmp_path, configs=["vpn1", "vpn2"], enabled=["vpn1", "vpn2"]))
    p = tmp_path / "calls"
    assert not p.exists() or not p.read_text().strip(), p.read_text()


def test_the_switch_body_stays_ascii():
    """It is a generated script and must carry no byte above 0x7F -- the
    lesson from 2026-10-29, when a comment marker broke 14 tests."""
    RACSW_BODY.encode("ascii")


def test_the_switch_actually_calls_the_prune():
    """A helper nobody calls is a helper that does not exist.  The switch is
    where an orphaned unit gets noticed, so switch_on must run it BEFORE it
    enables what rac_units lists."""
    body = switch_fn("switch_on")
    assert "rac_prune_orphan_units" in body, \
        "switch_on never prunes; the orphan stays enabled for ever"
    assert body.index("rac_prune_orphan_units") < body.index("for _u in $(rac_units)"), \
        "the prune runs after the enable pass, so the orphan is re-enabled first"
