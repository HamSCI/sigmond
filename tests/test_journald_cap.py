"""Tests for scripts/install/journald-cap.sh (v3.73 row 18 follow-on).

The appliance image bakes an explicit journal cap (sigmond-appliance
provision-components.sh).  A station that updates instead of reflashing
never ran that, so sigmond's install.sh writes the same cap.  It writes
/etc/systemd/journald.conf.d/50-sigmond-journal-size.conf and restarts
systemd-journald only when the file's settings change.
"""
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / 'scripts' / 'install' / 'journald-cap.sh'
WANT = '[Journal]\nSystemMaxUse=4G\n'


def _settings(text):
    return [l for l in text.splitlines()
            if l.strip() and not l.lstrip().startswith(('#', ';'))]


class JournaldCapTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dir = self.tmp / 'journald.conf.d'
        self.calls = self.tmp / 'calls'
        fake = self.tmp / 'systemctl'
        fake.write_text(f'#!/bin/sh\necho "$@" >> {self.calls}\n')
        fake.chmod(0o755)
        self.env = dict(os.environ, JOURNALD_CONF_DIR=str(self.dir),
                        SYSTEMCTL=str(fake))
        self.conf = self.dir / '50-sigmond-journal-size.conf'

    def run_it(self):
        return subprocess.run(['bash', str(SCRIPT)], env=self.env,
                              capture_output=True, text=True)

    def restarts(self):
        if not self.calls.exists():
            return []
        return [l for l in self.calls.read_text().splitlines()
                if l == 'restart systemd-journald']

    def test_a_missing_cap_is_written_and_journald_restarted(self):
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.conf.read_text(), WANT)
        self.assertEqual(stat.S_IMODE(self.conf.stat().st_mode), 0o644)
        self.assertEqual(len(self.restarts()), 1)

    def test_a_second_run_changes_nothing(self):
        self.run_it()
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.restarts()), 1)

    def test_the_appliance_copy_with_its_comments_is_left_alone(self):
        self.dir.mkdir()
        appliance = ('# sigmond-appliance: explicit journal cap (v3.73 row 18).  See\n'
                     '# provision-components.sh for the sizing.\n' + WANT)
        self.conf.write_text(appliance)
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.conf.read_text(), appliance)
        self.assertEqual(self.restarts(), [])

    def test_a_different_cap_is_replaced_and_journald_restarted(self):
        self.dir.mkdir()
        self.conf.write_text('[Journal]\nSystemMaxUse=1G\n')
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.conf.read_text(), WANT)
        self.assertEqual(len(self.restarts()), 1)

    def test_install_sh_runs_it(self):
        text = (REPO / 'install.sh').read_text()
        self.assertIn('scripts/install/journald-cap.sh', text)

    def test_the_cap_matches_the_appliance_image(self):
        prov = REPO.parent / 'sigmond-appliance' / 'provision-components.sh'
        if not prov.exists():
            self.skipTest('no sigmond-appliance checkout beside this one')
        m = re.search(r"50-sigmond-journal-size\.conf.*?<<'JOURNALD_EOF'\n(.*?)\nJOURNALD_EOF",
                      prov.read_text(), re.S)
        self.assertIsNotNone(m, 'the appliance heredoc moved')
        self.assertEqual(_settings(m.group(1)), _settings(WANT))


if __name__ == '__main__':
    unittest.main()
