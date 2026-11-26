"""sigmond-site-timing: LAN discovery offers, the operator adopts (2026-11-15).

The security review found steps 2 and 4 wiring whatever answered on the LAN:
any host replying stratum 1 on UDP 123 went into chrony, any :9000 UBX/RTCM
speaker into [gnss_vtec], and the NTP reply was never matched to the request.
Now a reply counts only from the probed address, as mode 4, leap not 3,
stratum 1, echoing our random transmit timestamp; and a found server or
stream is only OFFERED until the operator records it in lan-trust.toml
(`sigmond-site-timing --trust-ntp/--trust-gnss`).  What was wired before the
record existed stays, and so does a server the wizard wrote.

The steps run verbatim out of bin/sigmond-site-timing against fake NTP and
GNSS servers on 127.0.0.x, with only paths and ports redirected.
"""
import os
import re
import socket
import struct
import subprocess
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / 'bin' / 'sigmond-site-timing'
TEXT = SCRIPT.read_text()


def _section(start, end):
    a = TEXT.index(start)
    return TEXT[a:TEXT.index(end, a)]


TRUST_FUNCS = _section('# ── the LAN trust record', 'if [ $# -gt 0 ]; then')
STEP2 = _section('# ── 2. LAN stratum-1 NTP servers', '# ── 2b. declare those LAN')
STEP4 = _section('# ── 4. local GNSS stream', '# ── 4.5 meteor-scatter')


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeNTP:
    """One UDP responder per 127.0.0.x address, each with its own behaviour."""

    def __init__(self, port, behaviours):
        self.socks = []
        for ip, how in behaviours.items():
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind((ip, port))
            s.settimeout(0.2)
            self.socks.append((s, how))
        self.stop = False
        self.threads = [threading.Thread(target=self._serve, args=sh, daemon=True)
                        for sh in self.socks]
        for t in self.threads:
            t.start()

    def _serve(self, s, how):
        while not self.stop:
            try:
                req, peer = s.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                return
            xmt = req[40:48]
            leap, mode, stratum, origin = 0, 4, 1, xmt
            if how == 'wrong-origin':
                origin = b'\x01' * 8
            elif how == 'stratum2':
                stratum = 2
            elif how == 'leap3':
                leap = 3
            elif how == 'client-mode':
                mode = 3
            pkt = (bytes([leap << 6 | 4 << 3 | mode, stratum]) + b'\0' * 10 + b'GPS\0'
                   + b'\0' * 8 + origin + b'\0' * 16)
            if how == 'other-source':
                # answer from a different address than the one probed
                o = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                o.bind(('127.0.0.7', 0))
                o.sendto(pkt, peer)
                o.close()
            else:
                s.sendto(pkt, peer)

    def close(self):
        self.stop = True
        for t in self.threads:
            t.join(1)
        for s, _ in self.socks:
            s.close()


class FakeGNSS:
    def __init__(self, ip, port, payload):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((ip, port))
        self.srv.listen(8)
        self.srv.settimeout(0.2)
        self.payload = payload
        self.stop = False
        self.t = threading.Thread(target=self._serve, daemon=True)
        self.t.start()

    def _serve(self):
        while not self.stop:
            try:
                c, _ = self.srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                c.sendall(self.payload)
            finally:
                c.close()

    def close(self):
        self.stop = True
        self.t.join(1)
        self.srv.close()


@pytest.fixture
def env(tmp_path):
    d = {
        'T': tmp_path,
        'LAN_TRUST_FILE': str(tmp_path / 'lan-trust.toml'),
        'LAN_TRUST_NOTED_FILE': str(tmp_path / 'noted'),
        'LTS': tmp_path / 'local-timeservers.conf',
        'WIZ': tmp_path / '10-sigmond-ntp.conf',
        'CFG': tmp_path / 'timestd-config.toml',
        'GCFG': tmp_path / 'gnss-vtec.toml',
        'PHYS': tmp_path / 'hamsci-physics',
        'say': tmp_path / 'say.log',
    }
    (tmp_path / 'bin').mkdir()
    d['PHYS'].mkdir()
    sc = tmp_path / 'bin' / 'systemctl'
    sc.write_text(f'#!/bin/bash\necho "$*" >> {tmp_path}/systemctl.log\n')
    sc.chmod(0o755)
    return d


def run_step(env, body, subnet='127.0.0.0/29', port=None, gport=None):
    body = (body.replace('/etc/chrony/conf.d/local-timeservers.conf', str(env['LTS']))
                .replace('/etc/chrony/conf.d/10-sigmond-ntp.conf', str(env['WIZ']))
                .replace('/etc/hamsci-physics/gnss-vtec.toml', str(env['GCFG']))
                .replace('/opt/git/sigmond/hamsci-physics', str(env['PHYS'])))
    if port:
        body = body.replace("s.connect((ip, 123))", f"s.connect((ip, {port}))")
        assert f'{port}' in body
    if gport:
        body = body.replace('(str(ip), 9000)', f'(str(ip), {gport})')
        assert f'{gport}' in body
    script = env['T'] / 'step.sh'
    script.write_text(
        'set -u\n'
        f'say(){{ echo "$*" >> {env["say"]}; }}\n'
        + TRUST_FUNCS +
        f'CHANGED=0; SUBNET="{subnet}"; CFG="{env["CFG"]}"\n'
        + body +
        f'\necho "CHANGED=$CHANGED" >> {env["T"]}/result\n')
    e = dict(os.environ, LAN_TRUST_FILE=env['LAN_TRUST_FILE'],
             LAN_TRUST_NOTED_FILE=env['LAN_TRUST_NOTED_FILE'],
             PATH=f"{env['T']}/bin:{os.environ['PATH']}")
    r = subprocess.run(['bash', str(script)], env=e, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stderr
    said = env['say'].read_text() if env['say'].exists() else ''
    env['say'].unlink(missing_ok=True)
    return said


def trust(env, *args):
    e = dict(os.environ, LAN_TRUST_FILE=env['LAN_TRUST_FILE'])
    return subprocess.run(['bash', str(SCRIPT), *args, '--no-run'], env=e,
                          capture_output=True, text=True)


# --- (a) the NTP reply must answer OUR request -------------------------------

def test_ntp_scan_keeps_only_a_matched_stratum_1_reply(env):
    port = _free_port()
    srv = FakeNTP(port, {'127.0.0.2': 'good', '127.0.0.3': 'wrong-origin',
                         '127.0.0.4': 'stratum2', '127.0.0.5': 'leap3',
                         '127.0.0.6': 'client-mode', '127.0.0.1': 'other-source'})
    try:
        said = run_step(env, STEP2, port=port)
    finally:
        srv.close()
    offers = re.findall(r'NTP OFFER: (\S+)', said)
    assert offers == ['127.0.0.2'], said


# --- (b) offer only on first sight; (c) trusted -> wired ----------------------

def test_found_server_is_offered_not_wired(env):
    port = _free_port()
    srv = FakeNTP(port, {'127.0.0.2': 'good'})
    try:
        said = run_step(env, STEP2, port=port)
    finally:
        srv.close()
    assert not env['LTS'].exists()
    assert 'sudo sigmond-site-timing --trust-ntp 127.0.0.2' in said
    assert 'CHANGED=0' in (env['T'] / 'result').read_text()


def test_trusted_server_is_wired(env):
    assert trust(env, '--trust-ntp', '127.0.0.2').returncode == 0
    said = run_step(env, STEP2, subnet='')
    assert env['LTS'].read_text() == 'server 127.0.0.2 iburst minpoll 4 maxpoll 6\n'
    assert 'trusted LAN stratum-1 NTP wired: 127.0.0.2' in said
    assert 'CHANGED=1' in (env['T'] / 'result').read_text()
    # a second run adds nothing
    run_step(env, STEP2, subnet='')
    assert env['LTS'].read_text().count('server 127.0.0.2') == 1


def test_trust_refuses_anything_but_an_address(env):
    r = trust(env, '--trust-ntp', '10.0.0.1;reboot')
    assert r.returncode == 2
    assert not Path(env['LAN_TRUST_FILE']).exists()


# --- (c) what predates the record stays ----------------------------------------

def test_existing_local_timeservers_stay_and_are_noted_once(env):
    env['LTS'].write_text('server 192.168.8.144 iburst minpoll 4 maxpoll 6\n')
    before = env['LTS'].read_text()
    said = run_step(env, STEP2)
    assert env['LTS'].read_text() == before
    assert 'wired before the trust record existed - kept' in said
    said = run_step(env, STEP2)
    assert 'before the trust record' not in said
    assert env['LTS'].read_text() == before


def test_trusting_adds_beside_existing_entries(env):
    env['LTS'].write_text('server 192.168.8.144 iburst minpoll 4 maxpoll 6\n')
    trust(env, '--trust-ntp', '192.168.8.150')
    run_step(env, STEP2, subnet='')
    lines = env['LTS'].read_text().splitlines()
    assert lines == ['server 192.168.8.144 iburst minpoll 4 maxpoll 6',
                     'server 192.168.8.150 iburst minpoll 4 maxpoll 6']


# --- (d) the server the wizard wrote counts as trusted -------------------------

def test_wizard_named_server_is_not_offered(env):
    env['WIZ'].write_text('server 127.0.0.2 iburst\n')
    port = _free_port()
    srv = FakeNTP(port, {'127.0.0.2': 'good'})
    try:
        said = run_step(env, STEP2, port=port)
    finally:
        srv.close()
    assert 'OFFER' not in said
    assert env['WIZ'].read_text() == 'server 127.0.0.2 iburst\n'


def test_step_2b_reads_the_wizard_file_as_a_candidate():
    """The wizard's server reaches T4 vetting without a trust record."""
    b2 = _section('# ── 2b. declare those LAN', '# ── 2.5 identity')
    assert '[ -f "$WIZ" ] && awk' in b2


# --- GNSS: offer, trust, keep --------------------------------------------------

UBX = b'\xb5\x62\x01\x07' + b'\0' * 100
N8UR = ('[gnss_vtec]\nenabled = true\nhost = "<YOUR_GNSS_HOST>"\nport = 9000\n'
        'device = "/dev/serial/by-id/usb-u-blox_AG_-_www.u-blox.com_u-blox_GNSS_receiver-if00"\n')


def test_gnss_stream_is_offered_not_wired(env):
    gport = _free_port()
    srv = FakeGNSS('127.0.0.3', gport, UBX)
    try:
        said = run_step(env, STEP4, gport=gport)
    finally:
        srv.close()
    assert not env['GCFG'].exists()
    assert 'GNSS stream OFFER: 127.0.0.3:9000 (UBX)' in said
    assert 'sudo sigmond-site-timing --trust-gnss 127.0.0.3' in said


def test_trusted_gnss_host_is_wired(env):
    trust(env, '--trust-gnss', '192.168.8.50')
    said = run_step(env, STEP4, subnet='')
    text = env['GCFG'].read_text()
    assert 'host = "192.168.8.50"' in text and 'enabled = true' in text
    assert 'port = 9000' in text and 'protocol = "auto"' in text
    assert oct(env['GCFG'].stat().st_mode & 0o777) == '0o644'
    assert 'restart hamsci-physics-vtec' in (env['T'] / 'systemctl.log').read_text().splitlines()
    assert 'trusted GNSS stream wired: 192.168.8.50' in said


def test_trusted_host_fills_an_unwired_file_and_keeps_its_station(env):
    env['GCFG'].write_text('[station]\ngrid = "EN15"\n\n[gnss_vtec]\nenabled = false\n'
                           'host = "<YOUR_GNSS_HOST>"\nport = 9000\n')
    trust(env, '--trust-gnss', '192.168.8.50')
    run_step(env, STEP4, subnet='')
    text = env['GCFG'].read_text()
    assert 'host = "192.168.8.50"' in text and 'grid = "EN15"' in text
    assert 'YOUR_GNSS_HOST' not in text


def test_existing_gnss_host_stays_and_is_noted_once(env):
    cfg = '[gnss_vtec]\nenabled = true\nhost = "192.168.8.60"\nport = 9000\n'
    env['GCFG'].write_text(cfg)
    said = run_step(env, STEP4)
    assert env['GCFG'].read_text() == cfg
    assert 'GNSS stream host 192.168.8.60 was set before the trust record' in said
    assert 'before the trust record' not in run_step(env, STEP4)


def test_no_hamsci_physics_no_file(env):
    env['PHYS'].rmdir()
    trust(env, '--trust-gnss', '192.168.8.50')
    said = run_step(env, STEP4, subnet='')
    assert not env['GCFG'].exists() and 'hamsci-physics is not installed' in said
    assert not (env['T'] / 'systemctl.log').exists()


def test_usb_receiver_with_placeholder_host_is_wired(env):
    """N8UR: a real device counts, whatever the host says."""
    env['GCFG'].write_text(N8UR)
    trust(env, '--trust-gnss', '192.168.8.50')
    said = run_step(env, STEP4)
    assert env['GCFG'].read_text() == N8UR
    assert 'wired' not in said and 'OFFER' not in said and 'no local GNSS' not in said
    assert not (env['T'] / 'systemctl.log').exists()


def test_usb_receiver_is_never_scanned(env):
    env['GCFG'].write_text(N8UR)
    gport = _free_port()
    srv = FakeGNSS('127.0.0.3', gport, UBX)
    try:
        said = run_step(env, STEP4, gport=gport)
    finally:
        srv.close()
    assert env['GCFG'].read_text() == N8UR
    assert said == ''


OFF_HOST = '[gnss_vtec]\nenabled = false\nhost = "192.168.8.60"\nport = 9000\n'
OFF_DEV = ('[gnss_vtec]\nenabled = false\nhost = "<YOUR_GNSS_HOST>"\n'
           'device = "/dev/serial/by-id/usb-u-blox_AG_-_www.u-blox.com_u-blox_GNSS_receiver-if00"\n')


@pytest.mark.parametrize('cfg', [OFF_HOST, OFF_DEV])
def test_disabled_real_host_or_device_is_the_operators_off(env, cfg):
    env['GCFG'].write_text(cfg)
    trust(env, '--trust-gnss', '192.168.8.50')
    said = run_step(env, STEP4)
    assert env['GCFG'].read_text() == cfg
    assert not (env['T'] / 'systemctl.log').exists()
    assert f'gnss-vtec kept off by the operator ({env["GCFG"]})' in said
    assert 'kept off' not in run_step(env, STEP4)


def test_disabled_real_device_is_never_scanned(env):
    env['GCFG'].write_text(OFF_DEV)
    gport = _free_port()
    srv = FakeGNSS('127.0.0.3', gport, UBX)
    try:
        said = run_step(env, STEP4, gport=gport)
    finally:
        srv.close()
    assert env['GCFG'].read_text() == OFF_DEV
    assert 'OFFER' not in said


def test_b2_disabled_placeholder_still_wires(env):
    env['GCFG'].write_text('[gnss_vtec]\nenabled = false\nhost = "<YOUR_GNSS_HOST>"\nport = 9000\n')
    trust(env, '--trust-gnss', '192.168.8.50')
    run_step(env, STEP4, subnet='')
    assert 'host = "192.168.8.50"' in env['GCFG'].read_text()
    assert not list(env['T'].glob('*.tmp-site-timing'))
