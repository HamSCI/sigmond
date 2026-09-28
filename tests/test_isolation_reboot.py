"""sigmond-isolation-reboot: the station reboots itself, so test every branch.

The failure this guards against is not "isolation didn't activate" -- it is a
station that reboots forever because isolation can never activate, burying the
cause and staying unreachable in between.

Exit codes: 0 nothing to do · 10 reboot invoked · 20 one reboot already spent.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'bin' / 'sigmond-isolation-reboot'

DROP_IN = (
    '# managed by smd\n'
    '# mentions isolcpus in prose, which must NOT be parsed as the value\n'
    'GRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT} '
    'isolcpus=12-13 nohz_full=12-13 rcu_nocbs=12-13"\n'
)


class IsolationReboot(unittest.TestCase):

    def run_script(self, *, cfg=None, cmdline='', marker=False):
        d = Path(self.tmp.name)
        cfg_p, cmd_p, mark_p = d / 'cfg', d / 'cmdline', d / 'marker'
        rebooted = d / 'rebooted'
        if cfg is not None:
            cfg_p.write_text(cfg)
        cmd_p.write_text(cmdline)
        if marker:
            mark_p.write_text('2026-09-28T00:00:00+00:00\n')
        env = dict(os.environ,
                   SIGMOND_ISOL_CFG=str(cfg_p),
                   SIGMOND_ISOL_CMDLINE=str(cmd_p),
                   SIGMOND_ISOL_MARKER=str(mark_p),
                   SIGMOND_ISOL_REBOOT=f'touch {rebooted}')
        r = subprocess.run(['bash', str(SCRIPT)], env=env,
                           capture_output=True, text=True, timeout=60)
        return r, rebooted.exists(), mark_p.exists()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    # --- nothing to do ---------------------------------------------------

    def test_no_drop_in_does_not_reboot(self):
        r, rebooted, _ = self.run_script(cfg=None, cmdline='ro quiet')
        self.assertEqual(r.returncode, 0)
        self.assertFalse(rebooted)
        self.assertIn('nothing staged', r.stdout)

    def test_drop_in_without_isolcpus_does_not_reboot(self):
        r, rebooted, _ = self.run_script(cfg='# nothing here\n', cmdline='ro')
        self.assertEqual(r.returncode, 0)
        self.assertFalse(rebooted)

    def test_already_active_does_not_reboot(self):
        r, rebooted, _ = self.run_script(
            cfg=DROP_IN, cmdline='ro isolcpus=12-13 nohz_full=12-13')
        self.assertEqual(r.returncode, 0)
        self.assertFalse(rebooted)
        self.assertIn('ACTIVE', r.stdout)

    def test_range_and_list_spellings_are_the_same_set(self):
        # "12-13" staged vs "12,13" running.  A string compare here would
        # reboot the station on every single boot, forever.
        r, rebooted, _ = self.run_script(
            cfg=DROP_IN, cmdline='ro isolcpus=12,13 nohz_full=12,13')
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(rebooted)

    def test_wider_running_set_counts_as_active(self):
        r, rebooted, _ = self.run_script(cfg=DROP_IN, cmdline='ro isolcpus=10-13')
        # 10-13 != 12-13 as a set, so this DOES reboot -- the staged plan is
        # authoritative and a mismatch must converge on it.
        self.assertEqual(r.returncode, 10)
        self.assertTrue(rebooted)

    # --- the reboot ------------------------------------------------------

    def test_staged_and_inactive_reboots_once(self):
        r, rebooted, marker = self.run_script(cfg=DROP_IN, cmdline='ro quiet')
        self.assertEqual(r.returncode, 10)
        self.assertTrue(rebooted)
        self.assertTrue(marker, 'marker must be written so it cannot repeat')

    def test_marker_is_written_before_rebooting(self):
        # If the reboot command itself is what writes the marker, a crash
        # between the two leaves the loop guard disarmed.  Prove ordering by
        # making the "reboot" assert the marker already exists.
        d = Path(self.tmp.name)
        cfg_p, cmd_p, mark_p = d / 'cfg', d / 'cmdline', d / 'marker'
        cfg_p.write_text(DROP_IN)
        cmd_p.write_text('ro quiet')
        env = dict(os.environ,
                   SIGMOND_ISOL_CFG=str(cfg_p),
                   SIGMOND_ISOL_CMDLINE=str(cmd_p),
                   SIGMOND_ISOL_MARKER=str(mark_p),
                   SIGMOND_ISOL_REBOOT=f'test -e {mark_p}')
        r = subprocess.run(['bash', str(SCRIPT)], env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 10)

    # --- the loop guard --------------------------------------------------

    def test_second_attempt_refuses_and_says_why(self):
        r, rebooted, _ = self.run_script(cfg=DROP_IN, cmdline='ro quiet',
                                         marker=True)
        self.assertEqual(r.returncode, 20)
        self.assertFalse(rebooted, 'a reboot loop is the failure mode')
        self.assertIn('NOT rebooting again', r.stdout)
        self.assertIn('WITHOUT guest isolation', r.stdout)

    def test_marker_does_not_suppress_the_happy_path(self):
        # A spent marker must not make an already-isolated box look broken.
        r, rebooted, _ = self.run_script(
            cfg=DROP_IN, cmdline='ro isolcpus=12-13', marker=True)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(rebooted)


if __name__ == '__main__':
    unittest.main()
