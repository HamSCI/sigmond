"""Library mode, RAC_MODE and the station site name.

SIGMOND_WIZARD_AS_LIB=1 lets another script source the wizard for its
functions: everything above the dispatch is defined, and nothing runs.  The
site name has two modes.  Host mode (the default) is the Proxmox behaviour.
Station mode names a bare-metal machine <CALL>-<HOSTNAME>.
"""
import os
import subprocess
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"


def run(tmp_path, body, env=None, args=""):
    mark = tmp_path / "mark"
    mark.mkdir(exist_ok=True)
    script = textwrap.dedent(f"""
        SIGMOND_WIZARD_AS_LIB=1 source {WIZARD} {args} 2>/dev/null
        MARK_DIR={mark}
        CONF_MARK={mark}/.configured
        {body}
    """)
    e = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    e.update(env or {})
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, env=e, cwd=tmp_path)


def profile(tmp_path, text):
    p = tmp_path / "site-profile.toml"
    p.write_text(text)
    return str(p)


def test_lib_mode_returns_before_dispatch(tmp_path):
    sentinel = tmp_path / "ran"
    r = run(tmp_path,
            f'rac_register(){{ touch {sentinel}; }}\n'
            'declare -F rac_site_name >/dev/null && echo defined',
            args="--rac-register")
    assert "defined" in r.stdout, r.stderr
    assert not sentinel.exists()


def test_recorded_site_wins_over_reporter(tmp_path):
    (tmp_path / "mark").mkdir()
    (tmp_path / "mark" / "rac-site").write_text("AC0G_B4\n")
    r = run(tmp_path, "REPORTER=AC0G/B9; rac_site_name")
    assert r.stdout == "AC0G_B4", r.stderr


def test_host_mode_falls_back_to_reporter(tmp_path):
    r = run(tmp_path, "REPORTER=ac0g/b4; rac_site_name")
    assert r.stdout == "AC0G_B4", r.stderr


def test_station_default_call_hostname(tmp_path):
    env = {"SIGMOND_SITE_PROFILE": profile(tmp_path, '[station]\ncallsign = "AC0G"\n'),
           "SIGMOND_RAC_HOSTNAME": "sigma"}
    r = run(tmp_path, "RAC_MODE=station; rac_site_name", env)
    assert r.stdout == "AC0G-SIGMA", r.stderr


def test_station_hostname_already_prefixed(tmp_path):
    env = {"SIGMOND_SITE_PROFILE": profile(tmp_path, '[station]\ncallsign = "AC0G"\n'),
           "SIGMOND_RAC_HOSTNAME": "ac0g-b1"}
    r = run(tmp_path, "RAC_MODE=station; rac_site_name", env)
    assert r.stdout == "AC0G-B1", r.stderr


def test_station_env_site(tmp_path):
    env = {"SIGMOND_RAC_SITE": "AC0G-X",
           "SIGMOND_SITE_PROFILE": profile(tmp_path, '[station]\ncallsign = "AC0G"\n'),
           "SIGMOND_RAC_HOSTNAME": "sigma"}
    r = run(tmp_path, "RAC_MODE=station; rac_site_name", env)
    assert r.stdout == "AC0G-X", r.stderr


def test_station_no_call_prints_nothing(tmp_path):
    env = {"SIGMOND_SITE_PROFILE": str(tmp_path / "absent.toml"),
           "SIGMOND_RAC_HOSTNAME": "sigma"}
    r = run(tmp_path, "RAC_MODE=station; rac_site_name", env)
    assert r.stdout == "", r.stderr


# -- registration in station mode ---------------------------------------------

import socket
import threading

import pytest

from tests import test_wizard_rac_register as rr

STATION_REPLY = {"user": "0123456789abcdef", "token": "tok3nTOKEN42", "rac": 17,
                 "server_addr": "127.0.0.1", "server_port": 35735,
                 "ports": {"vm_ssh": 35817, "vm_web": 45817}}
HOST_REPLY = dict(STATION_REPLY, ports={"vm_ssh": 35817, "vm_web": 45817,
                                        "host_ssh": 50817, "host_ui": 55817})


def pre(tmp_path, port, *, station, relays=True, **kw):
    s = rr.host(tmp_path, tiers=rr.tier(port), relays=relays,
                configured=None if station else "AC0G/B4 EM38ww 2026-10-09", **kw)
    if station:
        s += "RAC_MODE=station\n"
    return s


def sh(script, tmp_path, env=None):
    return rr.run(script, tmp_path, dict({"SIGMOND_RAC_SITE": "AC0G-SIGMA"}, **(env or {})))


def proxies(toml):
    out, cur = [], None
    for ln in toml.splitlines():
        if ln.startswith("name = "):
            cur = {"name": ln.split('"')[1]}
            out.append(cur)
        elif cur is not None and ln.startswith("localPort"):
            cur["local"] = int(ln.split("=")[1])
        elif cur is not None and ln.startswith("remotePort"):
            cur["remote"] = int(ln.split("=")[1])
    return out


def test_station_toml_four_proxies(tmp_path):
    reg = rr.Registrar(reply=STATION_REPLY)
    try:
        r = sh(pre(tmp_path, reg.port, station=True) + "rac_register fresh; echo rc=$? fail=$RAC_FAIL\n", tmp_path)
    finally:
        reg.close()
    assert "rc=0 fail=" in r.stdout, r.stdout + r.stderr
    toml = (tmp_path / "frpc-host-gw2.toml").read_text()
    assert proxies(toml) == [
        {"name": "AC0G-SIGMA-vm-ssh", "local": 22, "remote": 35817},
        {"name": "AC0G-SIGMA-vm-web", "local": 8081, "remote": 45817},
        {"name": "AC0G-SIGMA-vm-station", "local": 8000, "remote": 48817},
        {"name": "AC0G-SIGMA-vm-gmag", "local": 8082, "remote": 49817}]
    assert "host-ssh" not in toml and "host-ui" not in toml
    assert (tmp_path / "marks" / "rac-site").read_text() == "AC0G-SIGMA\n"


def test_host_toml_unchanged(tmp_path):
    reg = rr.Registrar(reply=HOST_REPLY)
    try:
        r = sh(pre(tmp_path, reg.port, station=False) + "rac_register fresh\n", tmp_path)
    finally:
        reg.close()
    assert r.returncode == 0, r.stdout + r.stderr
    got = proxies((tmp_path / "frpc-host-gw2.toml").read_text())
    assert [p["name"].split("-", 1)[1] for p in got] == [
        "vm-ssh", "vm-web", "vm-station", "vm-gmag", "host-ssh", "host-ui"]
    assert [p["local"] for p in got] == [12222, 12223, 12224, 12225, 22, 8006]
    assert [p["remote"] for p in got] == [35817, 45817, 48817, 49817, 50817, 55817]


def test_station_registrar_ports_zero_when_omitted(tmp_path):
    reg = rr.Registrar(reply=STATION_REPLY)
    try:
        r = sh(pre(tmp_path, reg.port, station=True) + "rac_register fresh; echo \"[$REG]\"\n", tmp_path)
    finally:
        reg.close()
    assert "P_HSSH=0 P_HUI=0" in r.stdout, r.stdout + r.stderr


def test_host_registrar_still_requires_host_ports(tmp_path):
    reg = rr.Registrar(reply=STATION_REPLY)
    try:
        r = sh(pre(tmp_path, reg.port, station=False) + "rac_register fresh; echo \"rc=$? $RAC_FAIL\"\n", tmp_path)
    finally:
        reg.close()
    assert "rc=1" in r.stdout and "no gateway tier answered" in r.stdout


def test_station_skips_relay_precondition(tmp_path):
    reg = rr.Registrar(reply=STATION_REPLY)
    try:
        r = sh(pre(tmp_path, reg.port, station=True, relays=False)
               + "rac_register fresh; echo rc=$? fail=$RAC_FAIL\n", tmp_path)
        assert "rc=0 fail=" in r.stdout, r.stdout + r.stderr
        h = tmp_path / "h"
        h.mkdir()
        r = sh(pre(h, reg.port, station=False, relays=False)
               + "rac_register fresh; echo rc=$? fail=$RAC_FAIL\n", h)
    finally:
        reg.close()
    assert "rc=1 fail=the VM relays are not installed" in r.stdout, r.stdout + r.stderr


def test_station_direct_probe_four_proxies(tmp_path):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    threading.Thread(target=lambda: [srv.accept() for _ in range(5)], daemon=True).start()
    claim = tmp_path / "claim.toml"
    fake = tmp_path / "frpc"
    fake_src = (f'''#!/usr/bin/env python3
import http.server, shutil, sys
shutil.copy(sys.argv[2], "{claim}")
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        b = b'[' + b','.join([b'{{"status":"running"}}'] * 4) + b']'
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", 7502), H).serve_forever()
''')
    tiers = f"HamSCI|127.0.0.1|DIRECT|off|{port}"
    pre_s = rr.host(tmp_path, tiers=tiers, configured=None, relays=False)
    fake.write_text(fake_src)
    fake.chmod(0o755)
    script = pre_s + \
        'RAC_MODE=station; DASI_NUM=19\nrac_register fresh; echo "rc=$? fail=$RAC_FAIL"; echo "[$REG]"\n'
    r = sh(script, tmp_path)
    srv.close()
    assert "rc=0 fail=" in r.stdout, r.stdout + r.stderr
    got = proxies(claim.read_text())
    assert [(p["name"].split("-", 2)[2], p["local"]) for p in got] == [
        ("vm-ssh", 22), ("vm-web", 8081), ("vm-station", 8000), ("vm-gmag", 8082)]
    assert "P_HSSH=0 P_HUI=0" in r.stdout


def test_payload_found_in_opt_checkout(tmp_path):
    d = tmp_path / "opt" / "sigmond-rac"
    d.mkdir(parents=True)
    ran = tmp_path / "installed"
    (d / "install-host.sh").write_text(f"#!/bin/sh\ntouch {ran}\n")
    (d / "install-host.sh").chmod(0o755)
    script = rr.host(tmp_path, tiers="", frpc=False) + \
        'rac_payload_ready; echo rc=$?\n'
    r = sh(script, tmp_path, {"SIGMOND_RAC_PAYLOAD_DIRS": f"{tmp_path}/none:{d}"})
    assert ran.exists(), r.stdout + r.stderr


# -- installer and units ---------------------------------------------------------

def install(tmp_path, mode):
    """Run rac_install_host_files with its absolute paths moved under tmp_path."""
    sbin, units, mark = tmp_path / "sbin", tmp_path / "units", tmp_path / "mark"
    for d in (sbin, units, mark, tmp_path / "bin"):
        d.mkdir(exist_ok=True)
    (tmp_path / "bin" / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "bin" / "systemctl").chmod(0o755)
    script = textwrap.dedent(f"""
        SIGMOND_WIZARD_AS_LIB=1 source {WIZARD} 2>/dev/null
        RAC_MODE={mode}
        MARK_DIR={mark}
        RAC_SWITCH={sbin}/sigmond-rac-switch
        body=$(declare -f rac_install_host_files \\
               | sed -e 's|/usr/local/sbin|{sbin}|g' -e 's|/etc/systemd/system|{units}|g')
        eval "$body"
        rac_install_host_files
    """)
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": f"{tmp_path}/bin:{os.environ['PATH']}", "HOME": str(tmp_path)})
    return r, units, mark


def test_install_station_units(tmp_path):
    r, units, mark = install(tmp_path, "station")
    assert r.returncode == 0, r.stderr
    svc = (units / "sigmond-rac-register.service").read_text()
    assert "ExecStart=/usr/local/sbin/sigmond-rac retry\n" in svc.replace(str(tmp_path / "sbin"), "/usr/local/sbin")
    assert list(units.glob("sigmond-rac-pair.*")) == []
    assert (mark / "rac-mode").read_text().strip() == "station"


def test_install_host_units_unchanged(tmp_path):
    r, units, mark = install(tmp_path, "host")
    assert r.returncode == 0, r.stderr
    svc = (units / "sigmond-rac-register.service").read_text()
    assert "--rac-retry" in svc
    assert sorted(p.name for p in units.glob("sigmond-rac-pair.*")) == [
        "sigmond-rac-pair.path", "sigmond-rac-pair.service", "sigmond-rac-pair.timer"]
    assert (mark / "rac-mode").read_text().strip() == "host"


# -- final review fixes ---------------------------------------------------------

ACTIVATE = "sudo /opt/git/sigmond/sigmond/scripts/rac/sigmond-rac activate"


def fail_of(tmp_path, *, station, frpc=True, env=None):
    s = rr.host(tmp_path, tiers=rr.tier(1), relays=False, frpc=frpc, configured=None)
    if station:
        s += "RAC_MODE=station\n"
    e = {"SIGMOND_RAC_PAYLOAD_DIRS": "/nonexistent",
         "SIGMOND_SITE_PROFILE": str(tmp_path / "absent.toml"),
         "SIGMOND_RAC_HOSTNAME": "sigma"}
    e.update(env or {})
    r = rr.run(s + "rac_register fresh; echo \"rc=$? fail=$RAC_FAIL\"\n", tmp_path, e)
    return r.stdout


def test_station_missing_payload_names_station_commands(tmp_path):
    out = fail_of(tmp_path, station=True, frpc=False, env={"SIGMOND_RAC_SITE": "AC0G-X"})
    assert "rc=1" in out and "payload" in out and "is missing" in out, out
    assert ACTIVATE in out
    assert "sigmond-setup" not in out and "pm-align" not in out and "install-host.sh" not in out


def test_station_no_name_names_station_commands(tmp_path):
    out = fail_of(tmp_path, station=True)
    assert "rc=1" in out and "no station name" in out, out
    assert ACTIVATE in out and "--site" in out
    assert "sigmond-setup" not in out


def test_host_failure_messages_unchanged(tmp_path):
    out = fail_of(tmp_path, station=False)
    assert ("fail=this host records no station name yet; run: sigmond-setup --reconfigure"
            in out), out
    h = tmp_path / "h"
    h.mkdir()
    out = fail_of(h, station=False, frpc=False, env={"SIGMOND_RAC_SITE": "X"})
    out2 = rr.run(rr.host(h, tiers=rr.tier(1), relays=False, frpc=False,
                          configured="AC0G/B4 EM38ww 2026-10-09")
                  + "rac_register fresh; echo \"fail=$RAC_FAIL\"\n", h,
                  {"SIGMOND_RAC_PAYLOAD_DIRS": "/nonexistent"}).stdout
    assert ("fail=sigmond-rac payload missing (frpc/CA); run install-host.sh, "
            "then sigmond-setup --reconfigure") in out2, out2


def test_stray_rac_mode_cannot_flip_the_wizard_outside_library_mode(tmp_path):
    text = WIZARD.read_text()
    block = text[text.index("# RAC_MODE:"):text.index("rac_station_site_default(){")]
    for lib, want in (("1", "station"), ("0", "host"), ("", "host")):
        r = subprocess.run(["bash", "-c", f'SIGMOND_WIZARD_AS_LIB={lib}\n{block}\necho "$RAC_MODE"'],
                           capture_output=True, text=True, env={"PATH": os.environ["PATH"], "RAC_MODE": "station"})
        assert r.stdout.strip() == want, (lib, r.stdout, r.stderr)
    r = subprocess.run(["bash", "-c", f'SIGMOND_WIZARD_AS_LIB=1\n{block}\necho "$RAC_MODE"'],
                       capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
    assert r.stdout.strip() == "host"


def test_station_default_empty_for_bad_hostnames(tmp_path):
    prof = profile(tmp_path, '[station]\ncallsign = "AC0G"\n')
    for host in ("", "localhost", "LocalHost.localdomain", "ac0g", "AC0G"):
        r = run(tmp_path, "RAC_MODE=station; rac_site_name",
                {"SIGMOND_SITE_PROFILE": prof, "SIGMOND_RAC_HOSTNAME": host})
        assert r.stdout == "", (host, r.stdout, r.stderr)
    r = run(tmp_path, "RAC_MODE=station; rac_site_name",
            {"SIGMOND_SITE_PROFILE": prof, "SIGMOND_RAC_HOSTNAME": "sigma"})
    assert r.stdout == "AC0G-SIGMA"


def test_station_toml_header_names_sigmond_rac(tmp_path):
    reg = rr.Registrar(reply=STATION_REPLY)
    try:
        sh(pre(tmp_path, reg.port, station=True) + "rac_register fresh\n", tmp_path)
    finally:
        reg.close()
    first = (tmp_path / "frpc-host-gw2.toml").read_text().splitlines()[0]
    assert first.startswith("# Written by sigmond-rac ") and "station mode" in first


# -- payload readiness: the per-gateway CAs -----------------------------------------

def _payload_with_marker(tmp_path):
    d = tmp_path / "opt" / "sigmond-rac"
    d.mkdir(parents=True)
    ran = tmp_path / "installed"
    (d / "install-host.sh").write_text(
        f"#!/bin/sh\ntouch {ran}\n"
        f"[ -n \"$TEST_INSTALL_CAS\" ] && echo CA > {tmp_path}/ca-hamsci.crt\n"
        "exit 0\n")
    (d / "install-host.sh").chmod(0o755)
    return d, ran


def test_station_payload_not_ready_with_only_the_legacy_ca(tmp_path):
    # sigma, B2 and B1 hold frpc and the legacy CA from the old install.  Ready
    # needs the per-gateway CAs, so the installer runs.
    d, ran = _payload_with_marker(tmp_path)
    script = rr.host(tmp_path, tiers="", frpc=True) + \
        f'rm -f {tmp_path}/ca-hamsci.crt\nRAC_MODE=station\nrac_payload_ready; echo rc=$?\n'
    r = sh(script, tmp_path, {"SIGMOND_RAC_PAYLOAD_DIRS": str(d)})
    assert ran.exists(), r.stdout + r.stderr
    assert "rc=1" in r.stdout          # the installer left a CA out


def test_station_payload_ready_once_the_installer_brings_both_cas(tmp_path):
    d, ran = _payload_with_marker(tmp_path)
    script = rr.host(tmp_path, tiers="", frpc=True) + \
        f'rm -f {tmp_path}/ca-hamsci.crt\nRAC_MODE=station\nrac_payload_ready; echo rc=$?\n'
    r = sh(script, tmp_path, {"SIGMOND_RAC_PAYLOAD_DIRS": str(d), "TEST_INSTALL_CAS": "1"})
    assert ran.exists() and "rc=0" in r.stdout, r.stdout + r.stderr


def test_station_payload_with_all_cas_does_not_rerun_the_installer(tmp_path):
    d, ran = _payload_with_marker(tmp_path)
    (tmp_path / "ca-hamsci.crt").write_text("CA\n")
    script = rr.host(tmp_path, tiers="", frpc=True) + \
        'RAC_MODE=station\nrac_payload_ready; echo rc=$?\n'
    r = sh(script, tmp_path, {"SIGMOND_RAC_PAYLOAD_DIRS": str(d)})
    assert not ran.exists() and "rc=0" in r.stdout, r.stdout + r.stderr


def test_host_payload_with_frpc_and_legacy_ca_stays_ready_without_installer(tmp_path):
    d, ran = _payload_with_marker(tmp_path)
    script = rr.host(tmp_path, tiers="", frpc=True) + 'rac_payload_ready; echo rc=$?\n'
    r = sh(script, tmp_path, {"SIGMOND_RAC_PAYLOAD_DIRS": str(d)})
    assert not ran.exists() and "rc=0" in r.stdout, r.stdout + r.stderr


# -- a bare-metal station with more than one radiod ---------------------------

def test_station_extra_page_dials_its_own_service(tmp_path):
    reg = rr.Registrar(reply=STATION_REPLY)
    try:
        r = sh(pre(tmp_path, reg.port, station=True) + "rac_register fresh\n", tmp_path,
               {"SIGMOND_RAC_KA9Q_WEBS": "AC0G-SIGMA 8081\nAC0G-2 8084\n"})
    finally:
        reg.close()
    assert r.returncode == 0, r.stdout + r.stderr
    toml = (tmp_path / "frpc-host-gw2.toml").read_text()
    assert proxies(toml) == [
        {"name": "AC0G-SIGMA-vm-ssh", "local": 22, "remote": 35817},
        {"name": "AC0G-SIGMA-vm-web", "local": 8081, "remote": 45817},
        {"name": "AC0G-SIGMA-vm-web2", "local": 8084, "remote": 46817},
        {"name": "AC0G-SIGMA-vm-station", "local": 8000, "remote": 48817},
        {"name": "AC0G-SIGMA-vm-gmag", "local": 8082, "remote": 49817}]
    import tomllib
    px = {p["name"]: p for p in tomllib.loads(toml)["proxies"]}
    assert px["AC0G-SIGMA-vm-web2"]["localIP"] == "127.0.0.1"        # bare metal: its own service
    assert px["AC0G-SIGMA-vm-web"]["metadatas"]["radiod"] == "AC0G-SIGMA"
    assert px["AC0G-SIGMA-vm-web2"]["metadatas"]["radiod"] == "AC0G-2"


def test_station_reads_its_pages_from_ka9q_web_units(tmp_path):
    # The real discovery, on this machine: the primary page's radiod and port
    # come from ka9q-web.service's command line, every other page from the
    # ka9q-web@<radiod> env files `smd install ka9q-web` writes.
    envd = tmp_path / "ka9q-web"
    envd.mkdir()
    (envd / "KFS-SE.env").write_text("KA9Q_WEB_STATUS=KFS-SE-status.local\nKA9Q_WEB_PORT=8084\n")
    (envd / "KFS-X.env").write_text("# no port line yet\n")
    (envd / "KFS-Y.env").write_text("KA9Q_WEB_STATUS=KFS-Y-status.local\nKA9Q_WEB_PORT=8085\n")
    body = (pre(tmp_path, 1, station=True)
            + "systemctl(){ case \"$1\" in show) echo '{ path=/usr/local/sbin/ka9q-web ; "
              "argv[]=/usr/local/sbin/ka9q-web -m KFS-NW-status.local -p 8081 ; ignore_errors=no }' ;; esac; }\n"
            + "rac_ka9q_webs\n")
    r = sh(body, tmp_path, {"SIGMOND_KA9Q_WEB_ENV_DIR": str(envd)})
    assert r.stdout == "KFS-NW 8081\nKFS-SE 8084\nKFS-Y 8085\n", r.stdout + r.stderr


def test_station_without_a_ka9q_web_unit_still_names_the_primary_port(tmp_path):
    body = pre(tmp_path, 1, station=True) + "rac_ka9q_webs\n"
    r = sh(body, tmp_path, {"SIGMOND_KA9Q_WEB_ENV_DIR": str(tmp_path / "absent")})
    assert r.stdout == "- 8081\n", r.stdout + r.stderr
