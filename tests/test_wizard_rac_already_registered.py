"""Re-registering a station that is already registered, and admin-port collisions.

AI6VN, 2026-10-29, `sigmond-setup --reconfigure` on a station whose HamSCI
tunnels were up and healthy:

    HamSCI direct attempt failed ("err":"proxy [DASI-009.AI6VN-host-ssh] already exists"
    ...); see /var/log/sigmond-wizard.log
    HamSCI secure (TOFU) vpn1 frps answered on :35736 but its registrar did not
    ... trying wsprdaemon (secure) — gw2.wsprdaemon.org:35736

Two defects, and the second hid the first.

1. "already exists" means THIS NAME IS TAKEN, and on a reconfigure the taker
   is the station's OWN live session: frpc from the previous run is still
   connected and still holding every proxy name, so frps refuses the claim.
   The wizard read its own healthy registration as a failure and DEMOTED --
   HamSCI TOFU -> HamSCI TOFU -> wsprdaemon -- which is the exact opposite of
   the "most secure first" ladder it advertises.

2. The gateway it demoted to could never run.  rac_gateway_set hands vpn1 and
   gw2 the SAME frpc admin port, so the second frpc to start dies with
   "listen tcp 127.0.0.1:7500: bind: address already in use" -- and the wizard
   still reported "The gateway accepted 6 of 6 channels".  The rule is written
   in the wizard at the top of rac_gateway_set and broken ten lines below it.
"""
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"
TEXT = WIZARD.read_text()

# The transient claim-probe binds this one; no published gateway may reuse it.
PROBE_ADMIN_PORT = 7502


def shell_func(name: str) -> str:
    out, keep = [], False
    for ln in TEXT.splitlines(True):
        if ln.startswith("%s(){" % name) or ln.startswith("%s() {" % name):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, "%s not found in the wizard" % name
    return "".join(out)


def run(script, env=None):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          timeout=120, env=dict(os.environ, **(env or {})))


# ── 2. every published gateway needs its own frpc admin port ────────────────

GWSET = textwrap.dedent("""
    set -u
    RAC_PORT_SECURE=35736
    RAC_CA_GW2=/tmp/ca-gw2.crt
    RAC_CA_HAMSCI=/tmp/ca-hamsci.crt
    _hs1="vpn1.hamsci.org"; _hs2="vpn2.hamsci.org"; _hs_sni="vpn.hamsci.org"
""") + shell_func("rac_gateway_set")


def gateway_sets():
    """Every gateway a DASI station can publish: both families, together."""
    r = run(GWSET + '\nrac_gateway_set vpn1.hamsci.org\nrac_gateway_set gw2.wsprdaemon.org\n')
    assert r.returncode == 0, r.stderr
    rows = []
    for ln in r.stdout.splitlines():
        if not ln.strip():
            continue
        f = ln.split("|")
        rows.append({"name": f[0], "admin": f[-1]})
    return rows


def test_a_dasi_station_publishes_three_gateways():
    names = [g["name"] for g in gateway_sets()]
    assert names == ["vpn1", "vpn2", "gw2"], names


def test_no_two_gateways_share_an_admin_port():
    """Two frpc sessions on one port means the second never starts, and a
    station that believes it holds two gateways holds one."""
    rows = gateway_sets()
    ports = [g["admin"] for g in rows]
    dupes = {p for p in ports if ports.count(p) > 1}
    assert not dupes, "admin port(s) %s shared by %s" % (
        sorted(dupes), [(g["name"], g["admin"]) for g in rows])


def test_no_gateway_reuses_the_claim_probes_port():
    """The probe is transient, but it runs DURING the ladder -- exactly when a
    live session for the gateway being probed may also be up."""
    for g in gateway_sets():
        assert g["admin"] != str(PROBE_ADMIN_PORT), \
            "gateway %s reuses the probe's admin port" % g["name"]


# ── 1. "already exists" is not a failure when the name is ours ──────────────

FRPS_ALREADY = ('{"proxies":[{"status":"error","err":"proxy '
                '[DASI-009.AI6VN-host-ssh] already exists"},'
                '{"status":"error","err":"proxy [DASI-009.AI6VN-host-ui] '
                'already exists"}]}')
FRPS_PORT_TAKEN = ('{"proxies":[{"status":"error","err":"port already '
                   'used: 51229"}]}')
FRPS_OK = ('{"proxies":[{"status":"running"},{"status":"running"},'
           '{"status":"running"},{"status":"running"}]}')


def ladder(tmp_path, *, api, own_session_active, listen_port):
    frpc = tmp_path / "frpc"
    frpc.write_text("#!/bin/sh\nexec sleep 30\n")
    frpc.chmod(0o755)
    key = tmp_path / "id_ed25519"
    key.write_text("PRIVATE\n")
    (tmp_path / "id_ed25519.pub").write_text("ssh-ed25519 AAAAFAKE root@test\n")
    (tmp_path / "ca.crt").write_text("CA\n")
    api_file = tmp_path / "api.json"
    api_file.write_text(api)
    return textwrap.dedent(f"""
        set -u
        LOG={tmp_path}/wizard.log
        RAC_FRPC={tmp_path}/frpc
        RAC_CA={tmp_path}/ca.crt
        RAC_CA_GW2={tmp_path}/ca.crt
        RAC_CA_HAMSCI={tmp_path}/ca.crt
        RAC_HOSTKEY={tmp_path}/id_ed25519
        RAC_PORT_SECURE=35736
        RAC_BASE_VMSSH=35800; RAC_BASE_VMWEB=45800
        RAC_BASE_HSSH=50800;  RAC_BASE_HUI=55800
        RAC_BASE_VMSTN=48800; RAC_BASE_VMGMAG=49800
        SITE="DASI-009.AI6VN"
        DASI_NUM=9
        HAMSCI_TOKEN=""
        RAC_SERVER=""; RAC_TLS="on"; RAC_TIER_LABEL=""; RAC_PORT=""
        RAC_TIERS='HamSCI secure (TOFU) vpn1|127.0.0.1|DIRECT|on|{listen_port}'
        say(){{ echo "[wizard] $*"; }}
        curl(){{ cat {api_file}; }}
        systemctl(){{
            case "$*" in
                *is-active*sigmond-rac-host@*) return {0 if own_session_active else 1} ;;
            esac
            return 0
        }}
    """) + shell_func("rac_own_session_active") + shell_func("rac_ladder") + \
        '\nrac_ladder\nprintf "REG=[%s]\\n" "$REG"\n'


@pytest.fixture()
def listener():
    """A plain TCP listener so the ladder's reachability probe passes."""
    import socket
    import threading
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(16)
    stop = threading.Event()

    def serve():
        s.settimeout(0.3)
        while not stop.is_set():
            try:
                s.accept()[0].close()
            except Exception:
                pass
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield s.getsockname()[1]
    stop.set()
    t.join(timeout=2)
    s.close()


def test_our_own_live_session_holding_the_names_is_a_SUCCESS(tmp_path, listener):
    """The whole bug: a healthy registration read as a failure."""
    r = run(ladder(tmp_path, api=FRPS_ALREADY, own_session_active=True,
                   listen_port=listener))
    assert "REG=[]" not in r.stdout, \
        "already-registered station was treated as unregistered:\n%s" % r.stdout
    assert "RACN=229" in r.stdout, r.stdout


def test_and_it_says_so_rather_than_claiming_a_failure(tmp_path, listener):
    r = run(ladder(tmp_path, api=FRPS_ALREADY, own_session_active=True,
                   listen_port=listener))
    assert "already registered" in r.stdout.lower(), r.stdout
    assert "attempt failed" not in r.stdout.lower(), r.stdout


def test_names_held_by_someone_else_is_still_a_failure(tmp_path, listener):
    """No live session of ours -> the names really are taken.  Claiming
    success there would hand the operator a tunnel that is not theirs."""
    r = run(ladder(tmp_path, api=FRPS_ALREADY, own_session_active=False,
                   listen_port=listener))
    assert "REG=[]" in r.stdout, r.stdout


def test_someone_elses_station_is_named_as_the_likely_cause(tmp_path, listener):
    r = run(ladder(tmp_path, api=FRPS_ALREADY, own_session_active=False,
                   listen_port=listener))
    assert "9" in r.stdout and "NOT this" in r.stdout, r.stdout


def test_a_taken_PORT_is_unchanged_and_still_a_failure(tmp_path, listener):
    """The pre-existing branch must not be swallowed by the new one."""
    r = run(ladder(tmp_path, api=FRPS_PORT_TAKEN, own_session_active=True,
                   listen_port=listener))
    assert "REG=[]" in r.stdout, r.stdout
    assert "TAKEN" in r.stdout, r.stdout


def test_a_clean_claim_still_registers(tmp_path, listener):
    r = run(ladder(tmp_path, api=FRPS_OK, own_session_active=False,
                   listen_port=listener))
    assert "RACN=229" in r.stdout, r.stdout
    assert "claiming it" in r.stdout, r.stdout


# ── 3. a channel count belongs to the gateway it is printed beside ─────────
#
# The summary AI6VN was given read:
#
#   The gateway accepted 6 of 6 channels (RAC 151 on gw2.wsprdaemon.org,
#   wsprdaemon (secure)).
#
# gw2's frpc had never once started.  print_state names the gateway from
# $TOML's serverAddr -- gw2, where the symlink pointed -- while UP came from
# channels_up(), which takes the HIGHEST count across every session and so
# returned vpn1's six.  Both helpers are right for the question they were
# built for ("can anyone get in?"); neither answers "did THIS gateway accept?"
# The number and the name came from different places and were printed as one
# fact.

RACSW = __import__("re").search(
    r"cat > /usr/local/sbin/sigmond-rac-switch <<'RACSWEOF'\n(.*?)\nRACSWEOF\n",
    TEXT, __import__("re").S)
assert RACSW, "the rac-switch body is not in the wizard"
RACSW_BODY = RACSW.group(1)


def switch_fn(name: str) -> str:
    out, keep = [], False
    for ln in RACSW_BODY.splitlines(True):
        if ln.startswith("%s(){" % name) or ln.startswith("%s() {" % name):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, "%s not in the rac-switch body" % name
    return "".join(out)


def counter(tmp_path, *, active, api_by_port):
    """A station with vpn1 healthy (6 channels) and gw2 configured but dead."""
    etc = tmp_path / "sigmond"
    etc.mkdir(exist_ok=True)
    for short, port in (("vpn1", "7500"), ("vpn2", "7501"), ("gw2", "7503")):
        (etc / ("frpc-host-%s.toml" % short)).write_text(
            'serverAddr = "%s"\n[webServer]\nport = %s\n' % (short, port))
    (etc / "frpc-host.toml").write_text('serverAddr = "gw2.wsprdaemon.org"\n')
    cases = "\n".join(
        '            *127.0.0.1:%s*) printf %s ;;' % (p, "'%s'" % v)
        for p, v in api_by_port.items())
    actives = " ".join(active)
    return textwrap.dedent(f"""
        set -u
        TOML={etc}/frpc-host.toml
        API=http://127.0.0.1:7500/api/status
        ACTIVE="{actives}"
        systemctl(){{
            case "$*" in
                is-active*sigmond-rac-host@*)
                    # Pick the unit out of the ARGUMENTS one at a time.
                    # ${{*##*@}} applies the pattern to each positional
                    # parameter and rejoins, so it yielded "is-active vpn1"
                    # and never matched.
                    _u=""
                    for _a in "$@"; do
                        case "$_a" in sigmond-rac-host@*)
                            _u="${{_a#*@}}"; _u="${{_u%.service}}" ;;
                        esac
                    done
                    case " $ACTIVE " in *" $_u "*) echo active; return 0 ;; esac
                    echo inactive; return 3 ;;
            esac
            return 0
        }}
        curl(){{
            case "$*" in
{cases}
                *) printf '' ;;
            esac
        }}
    """) + switch_fn("rac_admin_ports") + switch_fn("channels_up")


SIX = '{"proxies":[' + ",".join(['{"status":"running"}'] * 6) + ']}'


def test_a_dead_gateways_count_is_zero_even_while_another_is_healthy(tmp_path):
    """The AI6VN report, as a test: gw2 dead, vpn1 holding six."""
    script = counter(tmp_path, active=["vpn1", "vpn2"],
                     api_by_port={"7500": SIX, "7501": SIX, "7503": ""})
    r = run(script + '\nchannels_up gw2\n')
    assert r.stdout.strip() == "0", \
        "a dead gateway was credited with another's channels: %r" % r.stdout


def test_a_live_gateway_reports_its_own_count(tmp_path):
    script = counter(tmp_path, active=["vpn1", "vpn2"],
                     api_by_port={"7500": SIX, "7501": SIX, "7503": ""})
    r = run(script + '\nchannels_up vpn1\n')
    assert r.stdout.strip() == "6", r.stdout


def test_without_a_gateway_it_still_answers_can_anyone_get_in(tmp_path):
    """The no-argument form is what the panel and `smd remote status` use;
    its 'highest across sessions' meaning must not change."""
    script = counter(tmp_path, active=["vpn1"],
                     api_by_port={"7500": SIX, "7501": "", "7503": ""})
    r = run(script + '\nchannels_up\n')
    assert r.stdout.strip() == "6", r.stdout


def test_read_state_names_the_gateway_before_it_counts_for_it():
    """GW is parsed from $TOML; UP must be computed for THAT gateway, so the
    assignment has to come after it.  Ordering is the whole defect."""
    body = switch_fn("read_state")
    assert "GW=" in body and "UP=$(channels_up" in body, body
    assert body.index("GW=") < body.index("UP=$(channels_up"), \
        "UP is computed before GW is known, so it cannot be GW's count"
    assert "channels_up \"${GW%%.*}\"" in body, \
        "read_state still counts channels fleet-wide, not for the named gateway"


# ── the admin-port table exists twice; the copies must agree ───────────────

def test_smd_and_the_wizard_agree_on_every_admin_port():
    """bin/smd renders the frpc configs; the wizard's ladder writes them too.
    Two tables, one contract -- and only the wizard's was fixed first."""
    import re as _re
    smd = (REPO / "bin" / "smd").read_text()
    m = _re.search(r"_RAC_GATEWAYS = \{(.*?)\n\}", smd, _re.S)
    assert m, "bin/smd no longer carries _RAC_GATEWAYS"
    smd_ports = dict(_re.findall(r"'(\w+)':\s*\([^)]*?(\d+)\)", m.group(1)))
    wiz_ports = {g["name"]: g["admin"] for g in gateway_sets()}
    assert smd_ports == wiz_ports, "smd %s vs wizard %s" % (smd_ports, wiz_ports)


def test_no_two_gateways_anywhere_share_an_admin_port():
    """Not per-family.  The pre-existing test checked HamSCI and wsprdaemon
    separately, so vpn1 and gw2 were never compared -- and the ladder crosses
    families on every fallback, which is exactly when they collide."""
    import re as _re
    smd = (REPO / "bin" / "smd").read_text()
    m = _re.search(r"_RAC_GATEWAYS = \{(.*?)\n\}", smd, _re.S)
    ports = [p for _, p in _re.findall(r"'(\w+)':\s*\([^)]*?(\d+)\)", m.group(1))]
    assert len(ports) == len(set(ports)), ports


TWO = '{"proxies":[{"status":"running"},{"status":"running"}]}'


def test_a_stopped_unit_is_refused_even_if_its_api_still_answers(tmp_path):
    """Isolates the unit check.  An frpc that is dying and restarting can
    still answer its admin API in the window between the two; the question is
    whether the UNIT is up, not whether something replied."""
    script = counter(tmp_path, active=["vpn1", "vpn2"],
                     api_by_port={"7500": SIX, "7501": SIX, "7503": SIX})
    r = run(script + '\nchannels_up gw2\n')
    assert r.stdout.strip() == "0", \
        "a stopped gateway was credited because its port answered: %r" % r.stdout


def test_it_reads_that_gateways_own_admin_port_not_the_default(tmp_path):
    """Isolates the port lookup.  $API is the fixed 7500 default; a gateway
    whose own session holds two channels must report two, not the six the
    default port's session is holding."""
    script = counter(tmp_path, active=["vpn1", "vpn2"],
                     api_by_port={"7500": SIX, "7501": TWO, "7503": ""})
    r = run(script + '\nchannels_up vpn2\n')
    assert r.stdout.strip() == "2", \
        "read the default admin port instead of vpn2's own: %r" % r.stdout


def test_a_single_session_station_is_not_reported_as_down(tmp_path):
    """A station that never migrated has frpc-host.toml and no per-gateway
    configs.  Asking about "its gateway" must fall through to the fleet-wide
    count, not report zero -- the first cut of the per-gateway check took
    every un-migrated station's status to down."""
    etc = tmp_path / "sigmond"
    etc.mkdir(exist_ok=True)
    (etc / "frpc-host.toml").write_text(
        'serverAddr = "gw2.wsprdaemon.org"\n[webServer]\nport = 7500\n')
    script = textwrap.dedent(f"""
        set -u
        TOML={etc}/frpc-host.toml
        API=http://127.0.0.1:7500/api/status
        systemctl(){{ echo inactive; return 3; }}
        curl(){{ printf '{SIX}'; }}
    """) + switch_fn("rac_admin_ports") + switch_fn("channels_up")
    r = run(script + '\nchannels_up gw2\n')
    assert r.stdout.strip() == "6", \
        "a single-session station was reported as having no channels: %r" % r.stdout
