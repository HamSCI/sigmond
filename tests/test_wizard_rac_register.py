"""Registration with the gateway, as a step that runs alone
(tasks/plan-remote-access.md section 3).

Before v3.71 the registration lines sat inside the wizard's interactive flow
and read its shell variables, so only a person answering the wizard could
register a station.  `sigmond-setup --rac-register` now runs them with no
question and no decoder VM: the switch script and the retry timer both need
that.  These tests run the wizard's own functions, cut out of the shipped
script by name, against a registrar that listens on this machine.
"""
import http.server
import json
import os
import shutil
import stat
import subprocess
import textwrap
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"
TEXT = WIZARD.read_text()

FUNCS = ("build_rac_tiers", "rac_gateway_set", "rac_load_pin", "rac_save_pin",
         "rac_site_name", "rac_payload_ready", "rac_ladder", "rac_write_config",
         "rac_register_locked", "rac_register", "rac_ka9q_webs", "mgmt_vm_ip",
         "rac_webs_in_config", "rac_web_channels", "rac_refresh_webs",
         "rac_station_runs")

PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFake root@test"

REPLY = {"user": "0123456789abcdef", "token": "tok3nTOKEN42", "rac": 507,
         "server_addr": "127.0.0.1", "server_port": 35735,
         "ports": {"vm_ssh": 36307, "vm_web": 46307, "host_ssh": 51307, "host_ui": 56307}}


def shell_func(name: str) -> str:
    out, keep = [], False
    for ln in TEXT.splitlines(True):
        if ln.startswith(f"{name}(){{") or ln.startswith(f"{name}() {{"):
            keep = True
        if keep:
            out.append(ln)
            if ln.rstrip() == "}":
                break
    assert out, f"{name} not found in the wizard"
    return "".join(out)


class Registrar:
    """A gateway registrar on 127.0.0.1.  It records every request body."""

    def __init__(self, reply=None, status=200):
        self.bodies = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                outer.bodies.append(json.loads(self.rfile.read(n)))
                body = json.dumps(reply if reply is not None else REPLY).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def registrar():
    r = Registrar()
    yield r
    r.close()


def host(tmp_path, *, tiers, relays=True, frpc=True, configured="AC0G/B4 EM38ww 2026-10-09"):
    """A scratch Proxmox host: marks, key, payload and a systemctl that only
    writes down what it was asked."""
    mark = tmp_path / "marks"
    mark.mkdir(exist_ok=True)
    if configured:
        (mark / ".configured").write_text(configured + "\n")
    (tmp_path / "id_ed25519").write_text("PRIVATE\n")
    (tmp_path / "id_ed25519.pub").write_text(PUBKEY + "\n")
    (tmp_path / "ca.crt").write_text("CA\n")
    if frpc:
        (tmp_path / "ca-hamsci.crt").write_text("CA\n")   # an installed payload holds both
        f = tmp_path / "frpc"
        f.write_text("#!/bin/sh\nexit 0\n")
        f.chmod(0o755)
    if relays:
        (tmp_path / "relay.socket").write_text("[Socket]\n")
    return textwrap.dedent(f"""
        set -u
        MARK_DIR={mark}
        CONF_MARK={mark}/.configured
        LOG={tmp_path}/wizard.log
        RAC_FRPC={tmp_path}/frpc
        RAC_CA={tmp_path}/ca.crt
        RAC_TOML={tmp_path}/frpc-host.toml
        RAC_HOSTKEY={tmp_path}/id_ed25519
        RAC_LOCK={tmp_path}/register.lock
        RAC_RELAY_UNIT={tmp_path}/relay.socket
        RAC_PIN_FILE={mark}/rac-endpoint.env
        RAC_FAIL=""
        RAC_BASE_VMSSH=35800; RAC_BASE_VMWEB=45800
        RAC_BASE_HSSH=50800;  RAC_BASE_HUI=55800
        RAC_BASE_VMSTN=48800; RAC_BASE_VMGMAG=49800
        RAC_BASE_VMWEB2=46800; RAC_BASE_VMWEB3=47800
        DASI_NUM=""; HAMSCI_TOKEN=""
        RAC_SERVER=""; RAC_TLS="on"; RAC_TIER_LABEL=""
        RAC_CA_GW2={tmp_path}/ca.crt
        RAC_CA_HAMSCI={tmp_path}/ca-hamsci.crt
        RAC_PORT_SECURE=35736
        _hs="vpn.hamsci.org"; _hs1="vpn1.hamsci.org"; _hs2="vpn2.hamsci.org"
        _hs_sni="$_hs"; _wd="gw2.wsprdaemon.org"
        RAC_TIERS='{tiers}'
        say(){{ echo "[wizard] $*"; }}
        systemctl(){{ echo "$*" >> {tmp_path}/systemctl.calls; }}
    """) + "".join(shell_func(n) for n in FUNCS)


def run(script, tmp_path, env=None):
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          timeout=120, env=dict(os.environ, **(env or {})))


def calls(tmp_path):
    p = tmp_path / "systemctl.calls"
    return p.read_text().splitlines() if p.exists() else []


def tier(port, label="test gateway", tls="on"):
    return f"{label}|127.0.0.1|http://127.0.0.1:{port}/register|{tls}|{port}"


# -- the name the gateway files the station under -----------------------------

def test_site_name_comes_from_the_running_wizards_reporter(tmp_path):
    pre = host(tmp_path, tiers="")
    r = run(pre + 'REPORTER="n0call/t1"\nrac_site_name\n', tmp_path)
    assert r.stdout == "N0CALL_T1"


def test_site_name_without_a_wizard_reads_the_recorded_name_then_the_configured_mark(tmp_path):
    pre = host(tmp_path, tiers="")
    assert run(pre + "rac_site_name\n", tmp_path).stdout == "AC0G_B4"
    (tmp_path / "marks" / "rac-site").write_text("W3USR_9\n")
    assert run(pre + "rac_site_name\n", tmp_path).stdout == "W3USR_9"


def test_site_name_drops_what_the_gateway_would_not_accept(tmp_path):
    pre = host(tmp_path, tiers="")
    r = run(pre + 'REPORTER=\'ac0g/b4";rm -rf $HOME\'\nrac_site_name\n', tmp_path)
    assert r.stdout == "AC0G_B4RM-RFHOME"


# -- registration -------------------------------------------------------------

def test_register_sends_only_the_name_and_the_public_key(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + 'rac_register ensure; echo "rc=$? fail=$RAC_FAIL"\n', tmp_path)
    assert "rc=0 fail=" in r.stdout, r.stdout + r.stderr
    assert registrar.bodies == [{"site": "AC0G_B4", "pubkey": PUBKEY}]
    assert "this sends the station's name (AC0G_B4) and this host's public key" in r.stdout


def test_register_writes_the_tunnel_configuration_and_its_marks(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port, label="wsprdaemon (secure)"))
    r = run(pre + "rac_register ensure\n", tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    toml = (tmp_path / "frpc-host.toml").read_text()
    assert 'serverAddr = "127.0.0.1"' in toml
    assert f"serverPort = {registrar.port}" in toml          # the tier's port, not the reply's
    assert 'user = "0123456789abcdef"' in toml and 'token = "tok3nTOKEN42"' in toml
    assert f'trustedCaFile = "{tmp_path}/ca.crt"' in toml
    names = [ln.split('"')[1] for ln in toml.splitlines() if ln.startswith("name = ")]
    assert names == [f"AC0G_B4-{c}" for c in
                     ("vm-ssh", "vm-web", "vm-station", "vm-gmag", "host-ssh", "host-ui")]
    ports = [int(ln.split("=")[1]) for ln in toml.splitlines() if ln.startswith("remotePort")]
    assert ports == [36307, 46307, 48800 + 507, 49800 + 507, 51307, 56307]
    assert stat.S_IMODE((tmp_path / "frpc-host.toml").stat().st_mode) == 0o600
    assert not (tmp_path / "frpc-host.toml.new").exists()
    marks = tmp_path / "marks"
    assert (marks / "rac-number").read_text() == "507\n"
    assert (marks / "rac-tier").read_text() == "wsprdaemon (secure)\n"
    assert (marks / "rac-registrar").read_text() == f"http://127.0.0.1:{registrar.port}/register\n"
    assert (marks / "rac-site").read_text() == "AC0G_B4\n"


def test_registration_never_starts_stops_or_enables_the_tunnel(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    run(pre + "rac_register ensure\n", tmp_path)
    # A running tunnel takes up the new configuration; a stopped one stays
    # stopped.  try-restart does exactly that and nothing else may appear.
    assert calls(tmp_path) == ["try-restart sigmond-rac-host@gw2.service"]
    body = "".join(shell_func(n) for n in FUNCS)
    for verb in ("systemctl enable", "systemctl start", "systemctl restart",
                 "systemctl stop", "systemctl disable"):
        assert verb not in body, verb


def test_ensure_leaves_a_registered_host_alone(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    (tmp_path / "frpc-host.toml").write_text('user = "old"\n')
    r = run(pre + 'rac_register ensure; echo "rc=$?"\n', tmp_path)
    assert "rc=0" in r.stdout
    assert registrar.bodies == []
    assert (tmp_path / "frpc-host.toml").read_text() == 'user = "old"\n'
    assert calls(tmp_path) == []


def test_fresh_registers_again_over_an_existing_configuration(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    (tmp_path / "frpc-host.toml").write_text('user = "old"\n')
    r = run(pre + 'REPORTER=W3USR/9\nrac_register fresh; echo "rc=$?"\n', tmp_path)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert registrar.bodies[0]["site"] == "W3USR_9"
    assert 'name = "W3USR_9-vm-ssh"' in (tmp_path / "frpc-host.toml").read_text()


def test_a_failed_fresh_run_keeps_the_old_configuration(tmp_path):
    dead = Registrar()
    port = dead.port
    dead.close()                                   # nothing listens there now
    pre = host(tmp_path, tiers=tier(port, label="gone"))
    (tmp_path / "frpc-host.toml").write_text('user = "old"\n')
    r = run(pre + 'rac_register fresh; echo "rc=$? fail=$RAC_FAIL"\n', tmp_path)
    assert "rc=1 fail=no gateway tier answered (tried: gone)" in r.stdout
    assert (tmp_path / "frpc-host.toml").read_text() == 'user = "old"\n'
    assert not (tmp_path / "marks" / "rac-number").exists()
    assert calls(tmp_path) == []


def test_the_ladder_walks_past_a_dead_tier_to_one_that_answers(tmp_path, registrar):
    dead = Registrar()
    port = dead.port
    dead.close()
    tiers = tier(port, label="first") + "\n" + tier(registrar.port, label="second", tls="off")
    pre = host(tmp_path, tiers=tiers)
    r = run(pre + 'rac_register ensure; echo "rc=$? tried=$RAC_TRIED"\n', tmp_path)
    assert "rc=0 tried=first, second" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "marks" / "rac-tier").read_text() == "second\n"
    toml = (tmp_path / "frpc-host.toml").read_text()
    assert "[transport.tls]\nenable = false" in toml and "trustedCaFile" not in toml


def test_a_malformed_reply_registers_nothing(tmp_path):
    bad = Registrar(reply=dict(REPLY, user="$(reboot)"))
    try:
        pre = host(tmp_path, tiers=tier(bad.port))
        r = run(pre + 'rac_register ensure; echo "rc=$?"\n', tmp_path)
    finally:
        bad.close()
    assert "rc=1" in r.stdout
    assert not (tmp_path / "frpc-host.toml").exists()
    assert "malformed registration" in (tmp_path / "wizard.log").read_text()


@pytest.mark.parametrize("kw,reason", [
    (dict(configured=""), "records no station name yet"),
    (dict(frpc=False), "sigmond-rac payload missing"),
    (dict(relays=False), "the VM relays are not installed on this host"),
], ids=["no-name", "no-payload", "no-relays"])
def test_register_refuses_a_host_that_is_not_ready_and_says_why(tmp_path, registrar, kw, reason):
    pre = host(tmp_path, tiers=tier(registrar.port), **kw)
    r = run(pre + 'rac_register ensure; echo "rc=$? fail=$RAC_FAIL"\n', tmp_path)
    assert "rc=1" in r.stdout and reason in r.stdout, r.stdout
    assert registrar.bodies == []
    assert not (tmp_path / "frpc-host.toml").exists()


def test_the_lock_is_free_again_after_a_run(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + f'rac_register ensure\nflock -n {tmp_path}/register.lock true && echo FREE\n',
            tmp_path)
    assert "FREE" in r.stdout, r.stdout + r.stderr


def test_a_program_started_inside_a_registration_holds_no_copy_of_its_lock(tmp_path, registrar):
    # The trial frpc of a DASI rung runs for up to 24 s, and python3 waits on a
    # registrar for 30.  A child that inherited descriptor 8 kept the lock after
    # its shell had died, and every later registration waited five minutes on it.
    pre = host(tmp_path, tiers=tier(registrar.port))
    witness = textwrap.dedent(f"""
        rac_payload_ready(){{
            ls -l /proc/self/fd > {tmp_path}/body.fds 2>/dev/null
            flock -n {tmp_path}/register.lock true && echo free > {tmp_path}/body.lock || echo held > {tmp_path}/body.lock
            [ -x "$RAC_FRPC" ] && [ -f "$RAC_CA" ]
        }}
    """)
    r = run(pre + witness + 'rac_register ensure; echo "rc=$?"\n', tmp_path)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "body.lock").read_text() == "held\n"      # the registration itself holds it
    assert "register.lock" not in (tmp_path / "body.fds").read_text()


def test_a_second_registration_waits_for_the_first(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    script = pre + textwrap.dedent(f"""
        flock {tmp_path}/register.lock sleep 2 &
        sleep 0.3
        t0=$(date +%s%N)
        rac_register ensure
        echo "waited_ms=$(( ($(date +%s%N) - t0) / 1000000 ))"
        wait
    """)
    r = run(script, tmp_path)
    waited = int(r.stdout.split("waited_ms=")[1].split()[0])
    assert waited >= 1200, r.stdout


# -- https: the reply carries the station's token ------------------------------

def _tls_registrar(tmp_path, name):
    """A registrar behind TLS with a self-signed certificate; (port, cert, close)."""
    import ssl
    key, crt = tmp_path / f"{name}.key", tmp_path / f"{name}.crt"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-keyout", str(key), "-out", str(crt), "-subj", "/CN=127.0.0.1",
                    "-addext", "subjectAltName=IP:127.0.0.1"],
                   check=True, capture_output=True, timeout=60)
    reg = Registrar()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    reg.httpd.socket = ctx.wrap_socket(reg.httpd.socket, server_side=True)
    return reg, crt


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl CLI")
def test_an_https_registrar_must_prove_itself_against_the_pinned_ca(tmp_path):
    reg, crt = _tls_registrar(tmp_path, "gateway")
    other, other_crt = _tls_registrar(tmp_path, "stranger")
    try:
        tiers = f"tls|127.0.0.1|https://127.0.0.1:{reg.port}/register|on|{reg.port}"
        pre = host(tmp_path, tiers=tiers)
        # Pinned to a CA that did not sign the registrar: no registration.
        shutil.copy(other_crt, tmp_path / "ca.crt")
        r = run(pre + 'rac_register ensure; echo "rc=$?"\n', tmp_path)
        assert "rc=1" in r.stdout
        assert reg.bodies == []
        assert "CERTIFICATE_VERIFY_FAILED" in (tmp_path / "wizard.log").read_text()
        # Pinned to the registrar's own certificate: it registers.
        shutil.copy(crt, tmp_path / "ca.crt")
        r = run(pre + 'rac_register ensure; echo "rc=$?"\n', tmp_path)
        assert "rc=0" in r.stdout, r.stdout + (tmp_path / "wizard.log").read_text()
        assert reg.bodies == [{"site": "AC0G_B4", "pubkey": PUBKEY}]
    finally:
        reg.close()
        other.close()


# -- a pinned gateway persists -------------------------------------------------

def _tiers_with(tmp_path, env, pin=None):
    pre = host(tmp_path, tiers="")
    if pin is not None:
        (tmp_path / "marks" / "rac-endpoint.env").write_text(pin)
    script = pre + textwrap.dedent("""
        RAC_PROFILE=standard; DASI_NUM=""
        RAC_REG_PORT=35737; RAC_PORT_SECURE=35736; RAC_PORT_UNSEC=35735
        _hs=vpn.hamsci.org; _wd=gw2.wsprdaemon.org
        rac_load_pin
        build_rac_tiers
        printf '%s\\n' "$RAC_TIERS"
    """)
    return run(script, tmp_path, env=env).stdout.strip()


def test_without_a_pin_the_ladder_stands(tmp_path):
    assert _tiers_with(tmp_path, {}).startswith("wsprdaemon (secure)|gw2.wsprdaemon.org|")


def test_a_pin_file_replaces_the_ladder_for_a_run_with_no_environment(tmp_path):
    pin = ("SIGMOND_RAC_SERVER=127.0.0.1\nSIGMOND_RAC_REGISTRAR=http://127.0.0.1:35737/register\n"
           "SIGMOND_RAC_FRPS_PORT=35736\nPATH=/evil\nSIGMOND_RAC_TLS=off\n")
    assert _tiers_with(tmp_path, {}, pin) == \
        "pinned|127.0.0.1|http://127.0.0.1:35737/register|off|35736"


def test_this_runs_environment_wins_over_the_pin_file(tmp_path):
    pin = "SIGMOND_RAC_SERVER=127.0.0.1\n"
    out = _tiers_with(tmp_path, {"SIGMOND_RAC_SERVER": "gw.example.org"}, pin)
    assert out.startswith("pinned|gw.example.org|")


def test_the_pin_file_never_runs_as_code(tmp_path):
    marker = tmp_path / "ran"
    pin = f"SIGMOND_RAC_SERVER=$(touch {marker})\ntouch {marker}\n"
    out = _tiers_with(tmp_path, {}, pin)
    assert not marker.exists()
    assert out.startswith("pinned|$(touch ")           # kept as text, never expanded


def test_a_wizard_run_with_a_pinned_environment_records_the_pin(tmp_path):
    pre = host(tmp_path, tiers="")
    env = {"SIGMOND_RAC_SERVER": "gw.example.org", "SIGMOND_RAC_FRPS_PORT": "35736"}
    run(pre + "rac_save_pin\n", tmp_path, env=env)
    assert (tmp_path / "marks" / "rac-endpoint.env").read_text() == \
        "SIGMOND_RAC_SERVER=gw.example.org\nSIGMOND_RAC_FRPS_PORT=35736\n"


def test_a_pin_of_the_server_alone_is_recorded_too(tmp_path):
    # The loop that writes the file must not end on the status of an unset
    # variable's test: that once skipped the rename and recorded nothing.
    pre = host(tmp_path, tiers="")
    run(pre + "rac_save_pin\n", tmp_path, env={"SIGMOND_RAC_SERVER": "127.0.0.1"})
    assert (tmp_path / "marks" / "rac-endpoint.env").read_text() == "SIGMOND_RAC_SERVER=127.0.0.1\n"


def test_a_wizard_run_without_a_pin_writes_no_pin_file(tmp_path):
    pre = host(tmp_path, tiers="")
    run(pre + "rac_save_pin\n", tmp_path, env={})
    assert not (tmp_path / "marks" / "rac-endpoint.env").exists()


# -- where the step sits in the wizard ------------------------------------------

def test_rac_register_runs_before_the_wizard_waits_for_a_vm_or_asks_anything():
    case_at = TEXT.index('case "${1:-}" in\n    --rac-register)')
    branch = TEXT[case_at:TEXT.index("    --rac-off", case_at)]
    # refresh: register a host that is not registered, and re-render the
    # ka9q-web channels of one that is (see the multi-radiod tests below)
    assert "rac_register refresh" in branch and "exit 0" in branch and "exit 1;;" in branch
    assert case_at < TEXT.index("waiting for decoder VM")
    assert case_at < TEXT.index("ask_reporter() {")
    for name in FUNCS:
        assert TEXT.index(f"{name}()") < case_at, f"{name} must be defined above the dispatch"


def test_the_pin_loads_before_the_ladder_is_built():
    assert TEXT.index("\nrac_load_pin\n") < TEXT.index("\nbuild_rac_tiers\nRAC_SERVER=")


def test_the_wizard_run_registers_through_the_same_function():
    block = TEXT[TEXT.index("rac_install_relays(){"):TEXT.index('PSWS_STATE="skipped')]
    assert "rac_register fresh" in block
    assert "urllib.request" not in block           # one copy of the request, in rac_ladder
    assert TEXT.count("urllib.request.urlopen(req") == 1
    assert TEXT.count("<<TOMLEOF") == 1


def test_the_relay_templates_keep_the_shape_pm_align_reads():
    # pm-align renders a station's relay units from these very lines of the
    # release's wizard (pm-align.py relay_units, WIZARD_HEREDOCS).
    for start in ("cat > /usr/local/lib/sigmond/vm-port-relay.py <<'RLEOF'",
                  'cat > "/etc/systemd/system/sigmond-vm-$RNAME-relay.socket" <<SOCKEOF',
                  'cat > "/etc/systemd/system/sigmond-vm-$RNAME-relay@.service" <<SVCEOF'):
        assert TEXT.count(start) == 1, start


def test_the_ssh_proxies_advertise_their_login_to_the_gateway(tmp_path, registrar):
    # The RAC dashboard on the gateway builds its ssh command from each
    # proxy's `user` metadata (Rob, 2026-11-02): the decoder VM's account is
    # hamsci, the host's is root.  No other proxy carries a login.
    import tomllib
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + "rac_register ensure\n", tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    doc = tomllib.loads((tmp_path / "frpc-host.toml").read_text())
    users = {p["name"].split("-", 1)[1]: p.get("metadatas", {}).get("user") for p in doc["proxies"]}
    assert users == {"vm-ssh": "hamsci", "vm-web": None, "vm-station": None,
                     "vm-gmag": None, "host-ssh": "root", "host-ui": None}
    assert doc["metadatas"]["site"] == "AC0G_B4"      # the login-level table is unchanged


# -- a decoder VM that runs more than one radiod ------------------------------
# rob, 2026-11-08, looking at KFS-SE-NW on the wsprdaemon RAC page: "now that
# that one PM can support multiple radiods, I'd like to have both of them
# listed, a link to both of them".  Each ka9q-web page of the VM gets a channel
# of its own, and every web channel names the radiod it shows, so the gateway
# page can label the links.

def _proxies(toml):
    import tomllib
    return {p["name"].split("-", 1)[1]: p for p in tomllib.loads(toml)["proxies"]}


def test_a_multi_radiod_vm_gets_one_web_channel_per_page(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + "rac_register fresh\n", tmp_path,
            {"SIGMOND_RAC_KA9Q_WEBS": "KFS-NW 8081\nKFS-SE 8084\n", "SIGMOND_MGMT_VM": "10.99.0.2"})
    assert r.returncode == 0, r.stdout + r.stderr
    toml = (tmp_path / "frpc-host.toml").read_text()
    names = [ln.split('"')[1].split("-", 1)[1] for ln in toml.splitlines() if ln.startswith("name = ")]
    # the 2nd page sits right after the 1st, where the gateway page reads it
    assert names == ["vm-ssh", "vm-web", "vm-web2", "vm-station", "vm-gmag", "host-ssh", "host-ui"]
    px = _proxies(toml)
    assert px["vm-web"]["metadatas"]["radiod"] == "KFS-NW"
    assert px["vm-web"]["localPort"] == 12223                      # the relay, as before
    assert px["vm-web2"]["metadatas"]["radiod"] == "KFS-SE"
    assert px["vm-web2"]["remotePort"] == 46800 + 507               # the gateway's vm_web2 band
    # no relay exists for a 2nd page: the channel dials the VM on the host-only /30
    assert (px["vm-web2"]["localIP"], px["vm-web2"]["localPort"]) == ("10.99.0.2", 8084)
    assert px["vm-web2"]["type"] == "tcp"


def test_a_single_radiod_vm_keeps_the_six_channels_and_names_its_page(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + "rac_register fresh\n", tmp_path, {"SIGMOND_RAC_KA9Q_WEBS": "AC0G 8081\n"})
    assert r.returncode == 0, r.stdout + r.stderr
    px = _proxies((tmp_path / "frpc-host.toml").read_text())
    assert sorted(px) == ["host-ssh", "host-ui", "vm-gmag", "vm-ssh", "vm-station", "vm-web"]
    assert px["vm-web"]["metadatas"] == {"radiod": "AC0G"}


def test_a_vm_that_cannot_be_asked_gets_the_six_bare_channels(tmp_path, registrar):
    # No guest agent in this scratch host: the configuration is the one every
    # station has had so far -- six channels, no radiod names.
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + "rac_register fresh\n", tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    toml = (tmp_path / "frpc-host.toml").read_text()
    assert "radiod" not in toml and "vm-web2" not in toml
    assert len(_proxies(toml)) == 6


def test_a_primary_page_without_a_radiod_name_carries_none(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + "rac_register fresh\n", tmp_path, {"SIGMOND_RAC_KA9Q_WEBS": "- 8081\nKFS-SE 8084\n"})
    assert r.returncode == 0, r.stdout + r.stderr
    px = _proxies((tmp_path / "frpc-host.toml").read_text())
    assert "metadatas" not in px["vm-web"]
    assert px["vm-web2"]["metadatas"]["radiod"] == "KFS-SE"


def test_a_third_page_takes_the_next_band_and_a_fourth_is_left_out(tmp_path, registrar):
    # gw2 reserves 46800 and 47800 for a 2nd and 3rd ka9q-web; there is no
    # band for a 4th, so it is not tunnelled, and the operator is told.
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + "rac_register fresh\n", tmp_path,
            {"SIGMOND_RAC_KA9Q_WEBS": "A 8081\nB 8084\nC 8085\nD 8086\n", "SIGMOND_MGMT_VM": "10.99.0.2"})
    assert r.returncode == 0, r.stdout + r.stderr
    px = _proxies((tmp_path / "frpc-host.toml").read_text())
    assert px["vm-web2"]["remotePort"] == 46800 + 507 and px["vm-web2"]["localPort"] == 8084
    assert px["vm-web3"]["remotePort"] == 47800 + 507 and px["vm-web3"]["localPort"] == 8085
    assert px["vm-web3"]["metadatas"]["radiod"] == "C"
    assert "vm-web4" not in px
    assert "no gateway band for a 4th ka9q-web page (D on port 8086)" in r.stdout


def test_a_page_on_the_primary_port_is_not_tunnelled_twice(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + "rac_register fresh\n", tmp_path, {"SIGMOND_RAC_KA9Q_WEBS": "A 8081\nA 8081\n"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "vm-web2" not in (tmp_path / "frpc-host.toml").read_text()


def test_every_gateway_config_carries_the_extra_page(tmp_path, registrar):
    # A station on two gateways publishes the same channels on both.
    pre = host(tmp_path, tiers=tier(registrar.port, label="HamSCI secure (TOFU)"))
    r = run(pre + "rac_register fresh\n", tmp_path, {"SIGMOND_RAC_KAQ_WEBS_UNUSED": "",
                                                       "SIGMOND_RAC_KA9Q_WEBS": "X 8081\nY 8084\n"})
    assert r.returncode == 0, r.stdout + r.stderr
    tomls = sorted(tmp_path.glob("frpc-host-*.toml"))
    assert len(tomls) >= 1
    for t in tomls:
        assert _proxies(t.read_text())["vm-web2"]["metadatas"]["radiod"] == "Y", t


def test_a_radiod_name_that_cannot_sit_in_a_toml_string_is_dropped(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + "rac_register fresh\n", tmp_path,
            {"SIGMOND_RAC_KA9Q_WEBS": 'KFS"-NW 8081\nKFS-SE 8084\n', "SIGMOND_MGMT_VM": "10.99.0.2"})
    assert r.returncode == 0, r.stdout + r.stderr
    px = _proxies((tmp_path / "frpc-host.toml").read_text())     # still parses
    assert "metadatas" not in px["vm-web"] and px["vm-web2"]["metadatas"]["radiod"] == "KFS-SE"


def test_a_duplicate_page_does_not_push_the_next_one_off_its_band(tmp_path, registrar):
    # A stale or repeated line is no page.  It must not use up vm-web2, so
    # the real 2nd page still lands there and the 3rd on vm-web3.
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + "rac_register fresh\n", tmp_path,
            {"SIGMOND_RAC_KA9Q_WEBS": "A 8081\nA 8081\nB 8084\nB 8084\nC 8085\n",
             "SIGMOND_MGMT_VM": "10.99.0.2"})
    assert r.returncode == 0, r.stdout + r.stderr
    px = _proxies((tmp_path / "frpc-host.toml").read_text())
    assert (px["vm-web2"]["remotePort"], px["vm-web2"]["localPort"]) == (46800 + 507, 8084)
    assert (px["vm-web3"]["remotePort"], px["vm-web3"]["localPort"]) == (47800 + 507, 8085)
    assert "4th" not in r.stdout


def test_the_writing_line_counts_the_channels_it_writes(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + "rac_register fresh\n", tmp_path,
            {"SIGMOND_RAC_KA9Q_WEBS": "A 8081\nB 8084\nC 8085\n", "SIGMOND_MGMT_VM": "10.99.0.2"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "(8 channels, gateway gw2" in r.stdout, r.stdout


# -- a radiod added (or removed) after registration ---------------------------
# The host asks the VM for its pages when it writes the configuration.
# `sigmond-setup --rac-register` on a registered host asks again and rewrites
# only the ka9q-web channels: the RAC number, the ports, the token and the
# keys stay as they are, and the gateway is not asked anything.

def _register_with(tmp_path, registrar, webs):
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + "rac_register fresh\n", tmp_path,
            {"SIGMOND_RAC_KA9Q_WEBS": webs, "SIGMOND_MGMT_VM": "10.99.0.2"})
    assert r.returncode == 0, r.stdout + r.stderr
    (tmp_path / "systemctl.calls").unlink()
    return pre


def _without_extra_webs(toml):
    # the configuration with its vm-web2/3 blocks and its radiod names taken out
    out, skip = [], False
    for block in toml.split("\n\n"):
        if '-vm-web2"' in block or '-vm-web3"' in block:
            continue
        out.append("\n".join(ln for ln in block.splitlines()
                             if not ln.startswith("radiod = ") and ln != "[proxies.metadatas]"
                             or "user = " in block and ln == "[proxies.metadatas]"))
    return "\n\n".join(out)


def test_refresh_adds_the_channel_of_a_radiod_added_later(tmp_path, registrar):
    pre = _register_with(tmp_path, registrar, "A 8081\n")
    before = (tmp_path / "frpc-host-gw2.toml").read_text()
    r = run(pre + 'rac_register refresh; echo "rc=$?"\n', tmp_path,
            {"SIGMOND_RAC_KA9Q_WEBS": "A 8081\nB 8084\n", "SIGMOND_MGMT_VM": "10.99.0.2"})
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    after = (tmp_path / "frpc-host-gw2.toml").read_text()
    assert len(registrar.bodies) == 1                       # the gateway was not asked again
    names = [ln.split('"')[1].split("-", 1)[1] for ln in after.splitlines() if ln.startswith("name = ")]
    assert names == ["vm-ssh", "vm-web", "vm-web2", "vm-station", "vm-gmag", "host-ssh", "host-ui"]
    px = _proxies(after)
    assert px["vm-web2"]["metadatas"]["radiod"] == "B"
    assert (px["vm-web2"]["localIP"], px["vm-web2"]["localPort"], px["vm-web2"]["remotePort"]) \
        == ("10.99.0.2", 8084, 46800 + 507)
    # everything else stands exactly as registered
    assert _without_extra_webs(after) == _without_extra_webs(before)
    assert stat.S_IMODE((tmp_path / "frpc-host-gw2.toml").stat().st_mode) == 0o600
    assert (tmp_path / "frpc-host.toml").is_symlink()
    assert calls(tmp_path) == ["try-restart sigmond-rac-host@gw2.service"]
    assert "7 channels" in r.stdout


def test_refresh_drops_the_channel_of_a_radiod_removed_since(tmp_path, registrar):
    pre = _register_with(tmp_path, registrar, "A 8081\nB 8084\nC 8085\n")
    before = (tmp_path / "frpc-host-gw2.toml").read_text()
    r = run(pre + 'rac_register refresh; echo "rc=$?"\n', tmp_path,
            {"SIGMOND_RAC_KA9Q_WEBS": "A 8081\nC 8085\n", "SIGMOND_MGMT_VM": "10.99.0.2"})
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    after = (tmp_path / "frpc-host-gw2.toml").read_text()
    px = _proxies(after)
    assert "vm-web3" not in px and px["vm-web2"]["metadatas"]["radiod"] == "C"
    assert len(registrar.bodies) == 1
    assert _without_extra_webs(after) == _without_extra_webs(before)
    assert len(_proxies(after)) == 7


def test_refresh_with_nothing_changed_touches_nothing(tmp_path, registrar):
    pre = _register_with(tmp_path, registrar, "A 8081\nB 8084\n")
    before = (tmp_path / "frpc-host-gw2.toml").read_text()
    r = run(pre + 'rac_register refresh; echo "rc=$?"\n', tmp_path,
            {"SIGMOND_RAC_KA9Q_WEBS": "A 8081\nB 8084\n", "SIGMOND_MGMT_VM": "10.99.0.2"})
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "frpc-host-gw2.toml").read_text() == before
    assert calls(tmp_path) == [] and len(registrar.bodies) == 1


def test_refresh_registers_a_host_that_is_not_registered_yet(tmp_path, registrar):
    pre = host(tmp_path, tiers=tier(registrar.port))
    r = run(pre + 'rac_register refresh; echo "rc=$?"\n', tmp_path,
            {"SIGMOND_RAC_KA9Q_WEBS": "A 8081\n"})
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert len(registrar.bodies) == 1
    assert _proxies((tmp_path / "frpc-host.toml").read_text())["vm-web"]["metadatas"]["radiod"] == "A"


def test_refresh_keeps_the_channels_when_the_vm_cannot_be_asked(tmp_path, registrar):
    pre = _register_with(tmp_path, registrar, "A 8081\nB 8084\n")
    before = (tmp_path / "frpc-host-gw2.toml").read_text()
    r = run(pre + 'rac_register refresh; echo "rc=$?"\n', tmp_path)     # no guest agent here
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "frpc-host-gw2.toml").read_text() == before
    assert calls(tmp_path) == []
    assert "could not ask the VM for its ka9q-web pages" in r.stdout


def test_a_fresh_run_that_cannot_ask_the_vm_keeps_the_extra_pages(tmp_path, registrar):
    # A wizard rerun while the guest agent is slow must not drop vm-web2/3:
    # it carries the pages the current configuration names, and says so.
    pre = _register_with(tmp_path, registrar, "A 8081\nB 8084\nC 8085\n")
    before = (tmp_path / "frpc-host-gw2.toml").read_text()
    r = run(pre + 'rac_register fresh; echo "rc=$?"\n', tmp_path, {"SIGMOND_MGMT_VM": "10.99.0.2"})
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert len(registrar.bodies) == 2
    after = (tmp_path / "frpc-host-gw2.toml").read_text()
    px = _proxies(after)
    assert px["vm-web"]["metadatas"]["radiod"] == "A"
    assert (px["vm-web2"]["metadatas"]["radiod"], px["vm-web2"]["localPort"]) == ("B", 8084)
    assert (px["vm-web3"]["metadatas"]["radiod"], px["vm-web3"]["localPort"]) == ("C", 8085)
    assert after.split("\n", 1)[1] == before.split("\n", 1)[1]    # all but the dated header line
    assert "could not ask the VM for its ka9q-web pages" in r.stdout
