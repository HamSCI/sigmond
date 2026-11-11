"""Tests for `smd radionet` -- the decoder VM's half of the site radio network.

Set up by hand first on a 6500 and a Beelink on an IPv6-only VLAN
(2026-11-11): a join from one VM drew 11.95 Mb/s over the tunnel and 0 before
and after.  These pin what that took inside the VM.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'lib'))

from sigmond.commands import radionet as rn  # noqa: E402

MGMT_V390 = """# The decoder VM has ONE link: a host-only /30 to the Proxmox host,
[Match]
Driver=virtio_net

[Network]
Address=10.99.0.2/30
Gateway=10.99.0.1
DNS=10.99.0.1
"""

RADIOD = """# radiod for AI6VN-1
[global]
hardware  = rx888
status    = AI6VN-1-status.local      # mDNS name for status + control multicast
ttl       = 0                  # 0 = loopback-only (single-host default,
iface     = ens18           # publish iface — ignored when ttl=0

[rx888]
device      = "rx888"
ttl = 7
"""


def test_radio_network_file():
    t = rn.render_radio_network('BC:24:11:79:F0:C4', '10.98.0.101/24', 1430)
    assert 'MACAddress=bc:24:11:79:f0:c4' in t          # lower-cased, as sysfs has it
    assert 'MTUBytes=1430' in t
    assert 'Address=10.98.0.101/24' in t
    assert 'IPv6AcceptRA=no' in t
    assert 'Destination=239.0.0.0/8' in t and 'Scope=link' in t
    # sorts before the management file, so the radio NIC never falls through
    assert rn.RADIO_NETWORK.name < rn.MGMT_NETWORK.name


def test_radio_network_rejects_bad_input():
    with pytest.raises(ValueError):
        rn.render_radio_network('not-a-mac', '10.98.0.101/24', 1430)
    with pytest.raises(ValueError):
        rn.render_radio_network('bc:24:11:79:f0:c4', 'fd00::5/64', 1430)


def test_mgmt_match_moves_from_driver_to_mac():
    out = rn.mgmt_match_by_mac(MGMT_V390, 'BC:24:11:31:81:F6')
    assert 'Driver=virtio_net' not in out
    assert out.count('MACAddress=bc:24:11:31:81:f6') == 1
    assert 'Address=10.99.0.2/30' in out and out.startswith('# The decoder VM')


def test_mgmt_match_also_replaces_name_glob_and_old_mac():
    for key in ('Name=en*', 'MACAddress=aa:bb:cc:dd:ee:ff'):
        out = rn.mgmt_match_by_mac(MGMT_V390.replace('Driver=virtio_net', key), 'bc:24:11:31:81:f6')
        assert key not in out and 'MACAddress=bc:24:11:31:81:f6' in out


def test_mgmt_match_without_a_match_key_is_refused():
    with pytest.raises(ValueError):
        rn.mgmt_match_by_mac('[Network]\nAddress=10.99.0.2/30\n', 'bc:24:11:31:81:f6')


def test_radiod_global_keys_set_and_section_kept():
    out = rn.set_global_keys(RADIOD, {'ttl': '1', 'iface': 'ens19'}, 'site radio network')
    assert rn.global_value(out, 'ttl') == '1'
    assert rn.global_value(out, 'iface') == 'ens19'
    assert 'status    = AI6VN-1-status.local' in out           # other keys untouched
    assert '[rx888]' in out and 'ttl = 7' in out               # only [global] changes
    assert out.count('# site radio network') == 2


def test_radiod_missing_key_is_added_to_global():
    out = rn.set_global_keys('[global]\nstatus = x.local\n[rx888]\n', {'ttl': '1', 'iface': 'ens19'}, 'n')
    assert rn.global_value(out, 'ttl') == '1' and rn.global_value(out, 'iface') == 'ens19'
    assert out.index('ttl = 1') < out.index('[rx888]')


def test_radiod_without_global_is_refused():
    with pytest.raises(ValueError):
        rn.set_global_keys('[rx888]\n', {'ttl': '1'}, 'n')


def test_iface_found_by_mac(tmp_path):
    for name, mac in (('ens18', 'bc:24:11:31:81:f6'), ('ens19', 'bc:24:11:79:f0:c4')):
        (tmp_path / name).mkdir()
        (tmp_path / name / 'address').write_text(mac + '\n')
    assert rn.iface_for_mac('BC:24:11:79:F0:C4', tmp_path) == 'ens19'
    assert rn.iface_for_mac('bc:24:11:00:00:00', tmp_path) is None


def test_smd_skips_the_loopback_route_on_a_radio_network():
    smd = (REPO / 'bin' / 'smd').read_text()
    i = smd.index('def _install_loopback_multicast_route(')
    body = smd[i:smd.index('\ndef ', i + 10)]
    assert "05-sigmond-radio.network').exists()" in body
    # and before the unit is installed
    assert body.index('05-sigmond-radio.network') < body.index("'sigmond-loopback-multicast.service'")


def test_smd_has_the_radionet_verbs():
    smd = (REPO / 'bin' / 'smd').read_text()
    assert "sub.add_parser('radionet'" in smd
    assert "_need_root('radionet apply')" in smd
