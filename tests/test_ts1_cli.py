"""bin/ts1 writes only to a character device.

UACNJ, 2026-11-04 → 11-08: a STAT sent while the TS-1 re-enumerated created
/dev/ttyACM1 as a regular file.  The by-id link still pointed there, so `ts1`
found a "port", and every console command landed in that file, never the TS-1.
"""
import subprocess
from pathlib import Path

TS1 = Path(__file__).resolve().parents[1] / 'bin' / 'ts1'


def run(glob, *args):
    return subprocess.run(['bash', str(TS1), *args],
                          env={'PATH': '/usr/bin:/bin', 'TS1_BYID_GLOB': glob},
                          capture_output=True, text=True, timeout=30)


def test_a_regular_file_where_the_node_belongs_is_refused(tmp_path):
    stray = tmp_path / 'ttyACM1'
    stray.write_bytes(b'\rSTAT\r')
    (tmp_path / 'usb-Adafruit_Trinket_M0_X-if00').symlink_to(stray)
    r = run(str(tmp_path / 'usb-Adafruit_Trinket_M0_*'), 'port')
    assert r.returncode == 1
    assert 'not a character device' in r.stderr
    assert stray.read_bytes() == b'\rSTAT\r'


def test_a_character_device_is_the_port(tmp_path):
    (tmp_path / 'usb-Adafruit_Trinket_M0_X-if00').symlink_to('/dev/null')
    r = run(str(tmp_path / 'usb-Adafruit_Trinket_M0_*'), 'port')
    assert r.returncode == 0
    assert r.stdout.strip() == '/dev/null'


def test_lan_lists_advertised_ts1s_without_a_local_one(tmp_path):
    # `ts1 lan` answers on a station with no TS-1 on its own bus (SIGMA).
    adv = tmp_path / 'sigmond-ts1-advertise'
    adv.write_text('#!/bin/bash\necho "ADV $*"\n')
    adv.chmod(0o755)
    r = subprocess.run(['bash', str(TS1), 'lan'],
                       env={'PATH': '/usr/bin:/bin',
                            'TS1_BYID_GLOB': str(tmp_path / 'none*'),
                            'TS1_ADVERTISE': str(adv)},
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == 'ADV --browse'


def test_trust_and_untrust_pass_through_without_a_local_ts1(tmp_path):
    adv = tmp_path / 'sigmond-ts1-advertise'
    adv.write_text('#!/bin/bash\necho "ADV $*"\n')
    adv.chmod(0o755)
    env = {'PATH': '/usr/bin:/bin', 'TS1_BYID_GLOB': str(tmp_path / 'none*'),
           'TS1_ADVERTISE': str(adv)}
    r = subprocess.run(['bash', str(TS1), 'trust', 'AC0G-B4', '9f1e37b5'], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.stdout.strip() == 'ADV --trust AC0G-B4 9f1e37b5'
    r = subprocess.run(['bash', str(TS1), 'untrust'], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.stdout.strip() == 'ADV --untrust'
