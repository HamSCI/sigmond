"""sigmond-firstrun-bringup repairs a half-configured dpkg before bring-up (#19).

DASI-011, 2026-10-14: the first-boot unattended-upgrade was cut by the install
power-off two seconds into reconfiguring grub-cloud-amd64.  dpkg stayed
half-configured, `smd bringup`'s preflight refused, and radiod never started
until someone ran `dpkg --configure -a` by hand.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'bin' / 'sigmond-firstrun-bringup'


class FirstrunDpkgRepair(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / 'personalized').write_text('')
        (self.d / 'profile.toml').write_text('profile = "dasi2"\n')

    def _exe(self, name, body):
        p = self.d / name
        p.write_text('#!/bin/bash\n' + body)
        p.chmod(0o755)
        return p

    def run_script(self, *, audit='', configure_rc=0, busy_polls=0, wait_s=30):
        d = self.d
        (d / 'busy').write_text(str(busy_polls))
        smd = self._exe('smd', f'echo "smd $(cat {d}/calls 2>/dev/null | wc -l)" >> {d}/order\n'
                               f'echo smd >> {d}/calls\nexit 0\n')
        dpkg = self._exe('dpkg', f'''
case "$1" in
  --audit) printf '%s' "{audit}" ;;
  --configure) echo "configure DEBIAN_FRONTEND=$DEBIAN_FRONTEND" >> {d}/calls
               echo configure >> {d}/order; exit {configure_rc} ;;
esac
exit 0
''')
        # busy_polls: apt-daily-upgrade.service reports active that many times.
        systemctl = self._exe('systemctl', f'''
if [ "$1" = is-active ]; then
  case "$*" in *apt-daily-upgrade.service*)
    n=$(cat {d}/busy); if [ "$n" -gt 0 ]; then echo $((n-1)) > {d}/busy
      echo waited >> {d}/calls; exit 0; fi;;
  esac
  exit 3
fi
exit 0
''')
        fuser = self._exe('fuser', 'exit 1\n')
        env = dict(
            os.environ,
            SIGMOND_FIRSTRUN_MARKER=str(d / 'marker'),
            SIGMOND_FIRSTRUN_SENTINEL=str(d / 'personalized'),
            SIGMOND_FIRSTRUN_PROFILE_FILE=str(d / 'profile.toml'),
            SIGMOND_FIRSTRUN_LOG=str(d / 'log'),
            SIGMOND_FIRSTRUN_SMD=str(smd),
            SIGMOND_FIRSTRUN_SITE_TIMING=str(d / 'absent'),
            SIGMOND_FIRSTRUN_ISOLATION=str(d / 'absent'),
            SIGMOND_FIRSTRUN_DPKG=str(dpkg),
            SIGMOND_FIRSTRUN_SYSTEMCTL=str(systemctl),
            SIGMOND_FIRSTRUN_FUSER=str(fuser),
            SIGMOND_FIRSTRUN_APT_WAIT_S=str(wait_s),
            SIGMOND_FIRSTRUN_APT_POLL_S='0.1',
        )
        r = subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True,
                           text=True, timeout=120)
        calls = (d / 'calls').read_text().split('\n') if (d / 'calls').exists() else []
        order = (d / 'order').read_text().split() if (d / 'order').exists() else []
        log = (d / 'log').read_text() if (d / 'log').exists() else ''
        return r, calls, order, log

    def test_nonempty_audit_runs_configure_before_bringup(self):
        _, calls, order, log = self.run_script(
            audit='The following packages are only half configured:\n grub-cloud-amd64\n')
        self.assertIn('configure DEBIAN_FRONTEND=noninteractive', calls)
        self.assertLess(order.index('configure'), order.index('smd'))
        self.assertIn('dpkg --configure -a', log)
        self.assertIn('exit 0', log)

    def test_empty_audit_does_not_run_configure(self):
        _, calls, order, _ = self.run_script(audit='')
        self.assertNotIn('configure', order)
        self.assertIn('smd', order)

    def test_busy_apt_waits_then_proceeds(self):
        r, calls, order, log = self.run_script(audit='half\n', busy_polls=3)
        self.assertEqual(calls.count('waited'), 3)
        self.assertLess(order.index('configure'), order.index('smd'))
        self.assertIn('waiting', log)

    def test_wait_is_bounded(self):
        _, calls, order, log = self.run_script(audit='', busy_polls=10_000, wait_s=1)
        self.assertIn('smd', order)      # gave up waiting, went on
        self.assertIn('still busy', log)

    def test_configure_failure_is_logged_and_run_continues(self):
        r, calls, order, log = self.run_script(audit='half\n', configure_rc=1)
        self.assertIn('smd', order)
        self.assertIn('exit 1', log)
        self.assertIn('result=', (self.d / 'marker').read_text())


if __name__ == '__main__':
    unittest.main()
