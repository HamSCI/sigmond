"""sigmond-ts1-advertise: one TS-1 shared by splitter, its USB on one host.

Michael's site, 2026-11-14: one TS-1 feeds four RX888s through splitters,
but its USB console plugs into AC0G-B4 only.  SIGMA's bring-up found no
Trinket and left T6 off, although its RX888 hears the pilot.  The host that
holds the USB now publishes the probe result as a DNS-SD service
(`_ts1._tcp`) through avahi; every other host browses it.

These tests pin the payload both ways: probe -> TXT -> avahi service file,
and avahi-browse's parsable output -> records.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "bin" / "sigmond-ts1-advertise"


def _load():
    loader = importlib.machinery.SourceFileLoader("ts1adv_under_test", str(TOOL))
    spec = importlib.util.spec_from_file_location("ts1adv_under_test", str(TOOL), loader=loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


m = _load()

B4_PROBE = """TS1_PRESENT=yes
TS1_PORT=/dev/ttyACM0
TS1_FIRMWARE=TimeSync v2.6, Board ID # 9f1e37b5
TS1_MODE=GPS-PPS
TS1_GPS_LOCK=yes
TS1_SATS=13
TS1_REF_HZ=27000000
TS1_REF_SOURCE=external
TS1_TX_HZ=84225000
"""


# ── probe -> payload ──────────────────────────────────────────────────────────

def test_probe_lines_parse_to_a_dict():
    kv = m.parse_probe(B4_PROBE + "junk line\n\n")
    assert kv["TS1_TX_HZ"] == "84225000"
    assert kv["TS1_FIRMWARE"] == "TimeSync v2.6, Board ID # 9f1e37b5"
    assert "junk line" not in kv


def test_payload_carries_the_contract_fields():
    p = m.payload_from_probe(m.parse_probe(B4_PROBE), host="AC0G-B4", now=1_800_000_000)
    assert p == {
        "schema": "1", "host": "AC0G-B4", "present": "yes", "mode": "GPS-PPS",
        "gps_lock": "yes", "tx_hz": "84225000", "sats": "13",
        "ref_source": "external", "firmware": "TimeSync v2.6, Board ID # 9f1e37b5",
        "board_id": "9f1e37b5", "ts": "1800000000",
    }


def test_an_unlocked_ts1_is_still_published_as_unlocked():
    # The consumer decides; the publisher reports what the console said.
    p = m.payload_from_probe(m.parse_probe(B4_PROBE.replace("GPS_LOCK=yes", "GPS_LOCK=no")),
                             host="h", now=1)
    assert p["gps_lock"] == "no"


def test_missing_lock_line_reads_as_not_locked():
    p = m.payload_from_probe(m.parse_probe("TS1_PRESENT=yes\nTS1_TX_HZ=84225000\n"), host="h", now=1)
    assert p["gps_lock"] == "no"
    assert "mode" not in p and "board_id" not in p


def test_values_are_sanitised_for_txt_and_xml():
    kv = m.parse_probe('TS1_PRESENT=yes\nTS1_FIRMWARE=Time"Sync; <v9> \\ \xe9\n')
    p = m.payload_from_probe(kv, host="h", now=1)
    assert p["firmware"] == "TimeSync <v9>"
    assert all(ord(c) < 127 for c in p["firmware"])


def test_action_publish_keep_withdraw():
    assert m.decide_action(m.parse_probe(B4_PROBE))[0] == "publish"
    busy = "TS1_PRESENT=yes\nTS1_PORT=/dev/ttyACM0\nTS1_ERROR=port busy (another process holds /dev/ttyACM0)\n"
    assert m.decide_action(m.parse_probe(busy))[0] == "keep"
    assert m.decide_action(m.parse_probe("TS1_PRESENT=no\n"))[0] == "withdraw"
    stray = "TS1_PRESENT=yes\nTS1_ERROR=/dev/ttyACM1 is not a character device\n"
    assert m.decide_action(m.parse_probe(stray))[0] == "withdraw"
    # `ts1 probe` without hf-timestd's script prints only an error on stderr
    assert m.decide_action(m.parse_probe(""))[0] == "withdraw"


# ── payload -> avahi static service file ─────────────────────────────────────

def test_service_xml_round_trips_the_payload():
    p = m.payload_from_probe(m.parse_probe(B4_PROBE), host="AC0G-B4", now=1_800_000_000)
    x = m.service_xml(p)
    root = ET.fromstring(x.split("?>", 1)[1] if x.startswith("<?xml") else x)
    svc = root.find("service")
    assert svc.findtext("type") == "_ts1._tcp"
    txt = dict(t.text.split("=", 1) for t in svc.findall("txt-record"))
    assert txt == p
    assert root.findtext("name") == "TS-1 9f1e37b5 on AC0G-B4"


def test_service_xml_escapes_markup():
    p = {"schema": "1", "host": "h", "firmware": "a<b>&c"}
    root = ET.fromstring(m.service_xml(p).split("?>", 1)[1])
    assert "firmware=a<b>&c" in [t.text for t in root.iter("txt-record")]


# ── avahi-browse -rtp -> records ─────────────────────────────────────────────

def _browse_line(p, iface="enp1s0", proto="IPv4", name=None, addr="192.168.1.176"):
    txt = " ".join('"%s=%s"' % (k, v) for k, v in reversed(list(p.items())))
    name = (name or "TS-1 9f1e37b5 on AC0G-B4").replace(" ", "\\032")
    return f"=;{iface};{proto};{name};_ts1._tcp;local;AC0G-B4.local;{addr};9;{txt}"


def test_browse_output_parses_and_merges_only_the_same_advert():
    p = m.payload_from_probe(m.parse_probe(B4_PROBE), host="AC0G-B4", now=1_800_000_000)
    out = "\n".join([
        "+;enp1s0;IPv4;TS-1\\0329f1e37b5\\032on\\032AC0G-B4;_ts1._tcp;local",
        _browse_line(p),
        _browse_line(p, proto="IPv6", addr="fe80::1"),
        _browse_line(p, iface="tailscale0"),
    ])
    recs = m.parse_browse(out)
    assert len(recs) == 1
    r = recs[0]
    assert {k: r[k] for k in p} == p
    assert r["service_name"] == "TS-1 9f1e37b5 on AC0G-B4"
    assert r["address"] == "192.168.1.176"
    assert r["addresses"] == ["192.168.1.176", "fe80::1"]


def test_two_ts1s_are_two_records():
    a = m.payload_from_probe(m.parse_probe(B4_PROBE), host="AC0G-B4", now=1)
    b = dict(a, board_id="1234abcd", host="OTHER")
    out = _browse_line(a) + "\n" + _browse_line(b, name="TS-1 1234abcd on OTHER")
    assert sorted(r["board_id"] for r in m.parse_browse(out)) == ["1234abcd", "9f1e37b5"]


def test_the_same_board_from_two_hosts_stays_two_records():
    # Never collapse before a trust decision: a spoof must show as a conflict.
    old = m.payload_from_probe(m.parse_probe(B4_PROBE), host="OLD", now=100)
    new = dict(old, host="NEW", ts="200")
    out = _browse_line(old, name="TS-1 9f1e37b5 on OLD") + "\n" + _browse_line(new, name="TS-1 9f1e37b5 on NEW")
    assert sorted(r["host"] for r in m.parse_browse(out)) == ["NEW", "OLD"]


def test_one_service_name_with_two_payloads_stays_two_records():
    a = m.payload_from_probe(m.parse_probe(B4_PROBE), host="AC0G-B4", now=1)
    out = _browse_line(a) + "\n" + _browse_line(dict(a, tx_hz="84000000"), addr="192.168.1.66")
    assert len(m.parse_browse(out)) == 2


def test_txt_decimal_escapes_and_quotes_decode():
    out = '=;e;IPv4;x;_ts1._tcp;local;h.local;1.2.3.4;9;"schema=1" "firmware=a\\"b\\092c\\233"'
    r = m.parse_browse(out)[0]
    assert r["firmware"] == 'a"b\\c\xe9'


def test_a_record_with_another_schema_is_dropped():
    out = '=;e;IPv4;x;_ts1._tcp;local;h.local;1.2.3.4;9;"schema=2" "tx_hz=1"'
    assert m.parse_browse(out) == []


# ── the publish cycle, end to end, with stubs ────────────────────────────────

def _env(tmp_path, probe_text, probe_rc=0):
    stub = tmp_path / "ts1"
    (tmp_path / "probe.out").write_text(probe_text)
    stub.write_text(f"#!/bin/bash\ncat {tmp_path}/probe.out\nexit {probe_rc}\n")
    stub.chmod(0o755)
    svcdir = tmp_path / "services"
    svcdir.mkdir()
    env = dict(os.environ, TS1_ADVERTISE_PROBE_CMD=str(stub),
               TS1_ADVERTISE_SERVICE_FILE=str(svcdir / "sigmond-ts1.service"),
               TS1_ADVERTISE_HOST="AC0G-B4")
    return env, svcdir / "sigmond-ts1.service"


def _run(env, *args):
    return subprocess.run([sys.executable, str(TOOL), *args], env=env,
                          capture_output=True, text=True, timeout=30)


def test_publish_writes_the_service_file(tmp_path):
    env, svc = _env(tmp_path, B4_PROBE)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert "_ts1._tcp" in svc.read_text()
    assert "tx_hz=84225000" in svc.read_text()
    assert "published" in r.stdout


def test_absent_withdraws_and_busy_keeps(tmp_path):
    env, svc = _env(tmp_path, B4_PROBE)
    _run(env)
    before = svc.read_text()
    (tmp_path / "probe.out").write_text("TS1_PRESENT=yes\nTS1_ERROR=port busy (x)\n")
    r = _run(env)
    assert svc.read_text() == before and "kept" in r.stdout
    (tmp_path / "probe.out").write_text("TS1_PRESENT=no\n")
    r = _run(env)
    assert not svc.exists() and "withdrawn" in r.stdout
    r = _run(env)                      # nothing to withdraw: still quiet and 0
    assert r.returncode == 0


def test_a_missing_services_directory_is_said_not_crashed(tmp_path):
    env, svc = _env(tmp_path, B4_PROBE)
    env["TS1_ADVERTISE_SERVICE_FILE"] = str(tmp_path / "nope" / "x.service")
    r = _run(env)
    assert r.returncode == 0
    assert "avahi" in r.stdout


def test_browse_prints_json(tmp_path):
    p = m.payload_from_probe(m.parse_probe(B4_PROBE), host="AC0G-B4", now=1)
    stub = tmp_path / "avahi-browse"
    (tmp_path / "browse.out").write_text(_browse_line(p) + "\n")
    stub.write_text(f"#!/bin/bash\necho \"$*\" > {tmp_path}/args\ncat {tmp_path}/browse.out\n")
    stub.chmod(0o755)
    env = dict(os.environ, TS1_ADVERTISE_BROWSE_CMD=str(stub))
    r = _run(env, "--browse")
    assert r.returncode == 0, r.stderr
    recs = json.loads(r.stdout)
    assert recs[0]["tx_hz"] == "84225000"
    args = (tmp_path / "args").read_text()
    assert "_ts1._tcp" in args and "-r" in args and "-t" in args and "-p" in args


def test_browse_fails_loudly_when_avahi_browse_is_missing(tmp_path):
    env = dict(os.environ, TS1_ADVERTISE_BROWSE_CMD=str(tmp_path / "missing"))
    r = _run(env, "--browse")
    assert r.returncode == 2
    assert "avahi-browse" in r.stderr


# ── the operator's trust record (LAN arming needs it) ────────────────────────

def test_trust_values_are_validated():
    assert m.valid_host("AC0G-B4") and m.valid_host("sigma.local")
    assert m.valid_board_id("9f1e37b5")
    for bad in ("", "-x", "a\nb", "a b", 'a"b', "[x]", "a" * 64, "h\x00"):
        assert not m.valid_host(bad), bad
    for bad in ("", "9f1e\n37b5", "a" * 33, "ab-cd", 'x"', "#1"):
        assert not m.valid_board_id(bad), bad


def _trust_env(tmp_path, adverts, samprate="129600000", browse_ok=True):
    """A stub avahi-browse showing `adverts` [(payload, address)], a radiod conf."""
    (tmp_path / "sigmond").mkdir(exist_ok=True)
    (tmp_path / "radio").mkdir(exist_ok=True)
    (tmp_path / "radio" / "radiod@x.conf").write_text(
        "[global]\nhardware = rx888\nsamprate = 12000\n\n[rx888]\n"
        + (f"samprate = {samprate}\n" if samprate else ""))
    (tmp_path / "browse.out").write_text("\n".join(
        _browse_line(p, name=f"TS-1 {p['board_id']} on {p['host']}", addr=a) for p, a in adverts) + "\n")
    stub = tmp_path / "avahi-browse"
    stub.write_text(f"#!/bin/bash\ncat {tmp_path}/browse.out\n" if browse_ok else "#!/bin/bash\nexit 127\n")
    stub.chmod(0o755)
    return dict(os.environ, TS1_TRUST_FILE=str(tmp_path / "sigmond" / "ts1-trust.toml"),
                TS1_ADVERTISE_BROWSE_CMD=str(stub),
                RADIOD_CONF_GLOB=str(tmp_path / "radio" / "radiod@*.conf"))


def _b4(**kw):
    p = m.payload_from_probe(m.parse_probe(B4_PROBE), host="AC0G-B4", now=int(__import__("time").time()))
    p.update(kw)
    return p


def _trusted(tmp_path):
    import tomllib
    f = tmp_path / "sigmond" / "ts1-trust.toml"
    return tomllib.loads(f.read_text())["trusted"] if f.exists() else None


TRUST = ("--trust", "AC0G-B4", "9f1e37b5", "84225000")


def test_trust_with_no_advert_visible_records_the_declaration(tmp_path):
    # SIGMA behind no mDNS path: the operator's declaration is enough.
    r = _run(_trust_env(tmp_path, []), *TRUST)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _trusted(tmp_path) == {"host": "AC0G-B4", "board_id": "9f1e37b5", "tx_hz": 84225000,
                                  "adc_hz": 129600000, "frequency_hz": 45375000}
    assert "not cross-checked" in r.stdout


def test_trust_with_avahi_browse_missing_still_records(tmp_path):
    r = _run(_trust_env(tmp_path, [], browse_ok=False), *TRUST)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _trusted(tmp_path)["frequency_hz"] == 45375000


def test_trust_cross_checked_against_one_matching_advert(tmp_path):
    env = _trust_env(tmp_path, [(_b4(), "192.168.1.176")])
    r = _run(env, *TRUST)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _trusted(tmp_path)["address"] == "192.168.1.176"
    assert "cross-checked" in r.stdout and "not cross-checked" not in r.stdout
    assert json.loads(_run(env, "--trusted").stdout)["frequency_hz"] == 45375000
    assert _run(env, "--untrust").returncode == 0 and _trusted(tmp_path) is None


def test_trust_at_64_8_msps_folds(tmp_path):
    assert _run(_trust_env(tmp_path, [], samprate="64.8M"), *TRUST).returncode == 0
    assert _trusted(tmp_path)["frequency_hz"] == 19425000


def test_trust_refuses_conflicts_and_shows_every_claimant(tmp_path):
    now = int(__import__("time").time())
    cases = {
        "two hosts claim the board": [(_b4(), "192.168.1.176"), (_b4(host="EVIL"), "192.168.1.66")],
        "same host, another TX": [(_b4(), "192.168.1.176"), (_b4(tx_hz="84000000"), "192.168.1.66")],
        "the board under another host": [(_b4(host="EVIL"), "192.168.1.66")],
        "the host with another board": [(_b4(board_id="deadbeef"), "192.168.1.66")],
        "one advert, another TX": [(_b4(tx_hz="84000000"), "192.168.1.176")],
        "ts in the future": [(_b4(ts=str(now + 3600)), "192.168.1.176")],
        "ts stale": [(_b4(ts=str(now - 7200)), "192.168.1.176")],
        "ts missing": [(_b4(ts="x"), "192.168.1.176")],
    }
    for name, adverts in cases.items():
        r = _run(_trust_env(tmp_path, adverts), *TRUST)
        assert r.returncode == 1, (name, r.stdout, r.stderr)
        assert _trusted(tmp_path) is None, name
        for _, addr in adverts:
            assert addr in r.stderr, (name, r.stderr)


def test_one_advert_from_two_ipv4_addresses_is_refused(tmp_path):
    p = _b4()
    env = _trust_env(tmp_path, [])
    (tmp_path / "browse.out").write_text(_browse_line(p) + "\n" + _browse_line(p, addr="192.168.1.66") + "\n")
    r = _run(env, *TRUST)
    assert r.returncode == 1 and "several IPv4" in r.stderr and _trusted(tmp_path) is None


def test_an_unrelated_ts1_does_not_block_trust(tmp_path):
    other = _b4(host="FAR", board_id="1234abcd", tx_hz="70000000")
    r = _run(_trust_env(tmp_path, [(other, "192.168.1.9")]), *TRUST)
    assert r.returncode == 0, r.stderr


def test_trust_refuses_what_it_cannot_read(tmp_path):
    for args, rate in ((TRUST, ""),                                         # no samprate
                       (("--trust", "AC0G-B4", "9f1e37b5", "84225000x"), "129600000"),
                       (("--trust", "AC0G-B4", "9f1e37b5", "1e8"), "129600000"),
                       (("--trust", "AC0G-B4", "9f1e37b5", "129590000"), "129600000")):  # alias near DC
        r = _run(_trust_env(tmp_path, [], samprate=rate), *args)
        assert r.returncode != 0, args
        assert _trusted(tmp_path) is None


def test_trust_refuses_hostile_values_and_writes_nothing(tmp_path):
    env = _trust_env(tmp_path, [(_b4(), "192.168.1.176")])
    for host, board, tx in (("AC0G-B4\n[timing]", "9f1e37b5", "84225000"),
                            ("AC0G-B4", 'x"\nenabled = true', "84225000"),
                            ("AC0G-B4", "a" * 40, "84225000"),
                            ("AC0G-B4", "9f1e37b5", "84225000\nenabled = true"),
                            ("AC0G-B4", "9f1e37b5", "-84225000")):
        r = _run(env, "--trust", host, board, tx)
        assert r.returncode == 2, (host, board, tx, r.stderr)
    assert not (tmp_path / "sigmond" / "ts1-trust.toml").exists()


def test_a_trust_record_must_be_self_consistent():
    good = {"host": "AC0G-B4", "board_id": "9f1e37b5", "tx_hz": 84225000,
            "adc_hz": 129600000, "frequency_hz": 45375000, "address": "192.168.1.176"}
    assert m.valid_trust(dict(good)) == good
    for bad in (dict(good, frequency_hz=45375001), dict(good, tx_hz="84225000"),
                dict(good, tx_hz=True), dict(good, address="b4.local"),
                dict(good, extra=1), {k: v for k, v in good.items() if k != "tx_hz"}):
        assert m.valid_trust(bad) == {}, bad
