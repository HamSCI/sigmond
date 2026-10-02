"""sigmond-firstrun-bringup: retry a transient failure, and wire the site either way.

WB6CXC-7, 2026-10-01, is the station this is written against.  From its own
/var/log/sigmond/firstrun-bringup.log:

    22:30:25  bring-up exited 1  (smd config init radiod -> "0 radiod conf(s)")
    22:30:25  marker written; this unit will not run again
    22:30:25  cpu isolation: nothing staged (...cfg absent) - no reboot

The RX-888 enumerated at 23:02:18 -- 32 minutes later.  The cause had cured
itself, but the marker was already final, so the station never tried again.
Two things were then skipped forever because they lived inside the success
branch: the sigmond-site-timing re-run (=> ZERO metrology channels, while
timestd-metrology.target still reported "active" with ConsistsOf= empty) and,
indirectly, isolation activation.  Every unit was green.

So the rule "never retry" was right about infinite loops and wrong about zero
attempts.  These tests pin the bounded middle.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'bin' / 'sigmond-firstrun-bringup'


class FirstrunRetry(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        # A personalized host with a profile, so we reach the bring-up itself.
        (self.d / 'personalized').write_text('')
        (self.d / 'profile.toml').write_text('profile = "dasi2"\n')

    def _stub(self, name, rc, note=''):
        """A recorded stub: writes a breadcrumb, exits rc."""
        p = self.d / name
        p.write_text(f'#!/bin/bash\necho "{note}" >> {self.d}/calls\nexit {rc}\n')
        p.chmod(0o755)
        return p

    def run_script(self, *, smd_rc=0, site_rc=0, marker=None,
                   site_timing=True, isolation=True):
        marker_p = self.d / 'marker'
        if marker is not None:
            marker_p.write_text(marker)
        env = dict(
            os.environ,
            SIGMOND_FIRSTRUN_MARKER=str(marker_p),
            SIGMOND_FIRSTRUN_SENTINEL=str(self.d / 'personalized'),
            SIGMOND_FIRSTRUN_PROFILE_FILE=str(self.d / 'profile.toml'),
            SIGMOND_FIRSTRUN_LOG=str(self.d / 'log'),
            SIGMOND_FIRSTRUN_SMD=str(self._stub('smd', smd_rc, 'smd')),
            SIGMOND_FIRSTRUN_SITE_TIMING=(
                str(self._stub('site-timing', site_rc, 'site-timing'))
                if site_timing else str(self.d / 'absent')),
            SIGMOND_FIRSTRUN_ISOLATION=(
                str(self._stub('isolation', 0, 'isolation'))
                if isolation else str(self.d / 'absent')),
        )
        r = subprocess.run(['bash', str(SCRIPT)], env=env,
                           capture_output=True, text=True, timeout=120)
        calls = (self.d / 'calls').read_text().split() if (self.d / 'calls').exists() else []
        txt = marker_p.read_text() if marker_p.exists() else ''
        return r, calls, txt

    # --- the WB6CXC-7 case -------------------------------------------------

    def test_a_failed_bringup_is_marked_retry_not_final(self):
        _, _, marker = self.run_script(smd_rc=1)

        self.assertIn('result=retry', marker)
        self.assertIn('attempts=1', marker)

    def test_a_retry_marker_lets_the_next_boot_try_again(self):
        """The whole point: the 22:30 failure must not be the last word."""
        r, calls, marker = self.run_script(smd_rc=0, marker='attempts=1\nresult=retry\n')

        self.assertIn('smd', calls, 'bring-up did not re-run on a retry marker')
        self.assertIn('result=ok', marker)
        self.assertIn('attempts=2', marker)

    def test_site_wiring_runs_even_when_bringup_FAILS(self):
        """This is the line that cost WB6CXC-7 its metrology channels."""
        _, calls, _ = self.run_script(smd_rc=1)

        self.assertIn('site-timing', calls,
                      'site wiring was skipped on a failed bring-up — the exact '
                      'defect that left the station measuring nothing')

    def test_isolation_activation_runs_even_when_bringup_fails(self):
        _, calls, _ = self.run_script(smd_rc=1)

        self.assertIn('isolation', calls)

    # --- the bound ---------------------------------------------------------

    def test_it_gives_up_after_the_third_attempt(self):
        """Bounded, so a genuinely broken station cannot loop forever."""
        r, _, marker = self.run_script(smd_rc=1, marker='attempts=2\nresult=retry\n')

        self.assertIn('result=gave-up', marker)
        self.assertIn('attempts=3', marker)
        self.assertIn('FAILED 3 times', r.stdout)

    def test_a_gave_up_marker_is_final(self):
        _, calls, _ = self.run_script(smd_rc=0, marker='attempts=3\nresult=gave-up\n')

        self.assertNotIn('smd', calls, 'gave-up must not re-run bring-up')

    def test_a_success_marker_is_final(self):
        _, calls, _ = self.run_script(smd_rc=0, marker='attempts=1\nresult=ok\n')

        self.assertNotIn('smd', calls)

    def test_a_LEGACY_marker_is_final(self):
        """Upgrade safety.  Markers written before this change are a bare
        timestamp with no result= line; a station that already completed must
        not be dragged through bring-up again by the new code."""
        _, calls, _ = self.run_script(smd_rc=0, marker='2026-09-30T12:00:00+00:00\n')

        self.assertNotIn('smd', calls,
                         'a pre-existing legacy marker must be treated as final')

    # --- the null ----------------------------------------------------------

    def test_a_successful_bringup_does_not_ask_for_a_retry(self):
        _, _, marker = self.run_script(smd_rc=0)

        self.assertIn('result=ok', marker)
        self.assertNotIn('retry', marker)

    def test_an_unpersonalized_host_is_left_alone(self):
        (self.d / 'personalized').unlink()
        r, calls, _ = self.run_script(smd_rc=0)

        self.assertEqual(r.returncode, 0)
        self.assertNotIn('smd', calls)

    def test_missing_site_timing_is_reported_not_silent(self):
        """The failure mode being designed out is silence, not absence."""
        r, _, _ = self.run_script(smd_rc=0, site_timing=False)

        self.assertIn('not present', r.stdout)
        self.assertIn('timing chain unwired', r.stdout)

    def test_zero_metrology_channels_is_said_out_loud(self):
        """`systemctl list-units` finds none in a test environment, which is
        exactly the state to shout about: 'site wiring complete' with zero
        channels is the shape of the WB6CXC-7 failure."""
        r, _, _ = self.run_script(smd_rc=0)

        self.assertIn('NO metrology channels', r.stdout)
        self.assertIn('measures nothing', r.stdout)


if __name__ == '__main__':
    unittest.main()
