"""A first bring-up must WAIT for a slow RX-888, not give up and abort.

AI6VN, v3.78 greenfield install, 2026-10-23.  The gate "ensure the RX-888 is
on the bus" gave up, bring-up carried on to `smd config init radiod`, that
found no card and wrote nothing, and the HARD checkpoint aborted:

    ✗ checkpoint: radiod configured FAILED — /etc/radio: 0 radiod conf(s)
    ✗ aborting (hard checkpoint). Fix, then re-run: smd bringup dasi2

radiod was left DISABLED — never started, never crashed, no journal entries
at all — on a station whose RX-888 was healthy on the bus minutes later.
Re-running `smd bringup dasi2` by hand then worked first time and wrote 10
configs.  Nothing was wrong except the waiting.

⚠ A FIRST boot is the worst case for card timing, and the causes compound:
the FX3 firmware was installed moments earlier IN THE SAME RUN, udev was
reloaded in the same run, a passed-through USB controller in a VM settles
late, and on a hub that does not follow the bootloader-to-SuperSpeed
transition the card must also be blipped by sdr-recover before it is usable.

sdr-recover's own default is 60 s, which is right for a recovery run on a
settled station and far too short here.

The trade is asymmetric, which is why the test asserts a LOWER bound rather
than an exact value: waiting is paid only by a station whose card really is
absent, once, on its first boot.  Not waiting costs a station that comes up
with no radio at all and needs a human.
"""
import unittest

from sigmond.bringup import RX888_FIRSTBOOT_WAIT_S, build_plan
from sigmond.catalog import Profile


def _dasi2():
    return Profile(
        name='dasi2',
        clients=('hf-timestd', 'wspr-recorder', 'psk-recorder', 'mag-recorder'),
        local_radiod_infra=('igmp-querier', 'gpsdo-monitor'),
        optional=('ka9q-web',),
    )


def _gate(plan):
    for s in plan.steps:
        if s.argv and 'sigmond-sdr-recover' in ' '.join(s.argv):
            return s
    return None


class SlowSdrWait(unittest.TestCase):

    def setUp(self):
        self.plan = build_plan(_dasi2(), local_radiod=True, reporter='AI6VN')
        self.step = _gate(self.plan)

    def test_the_gate_is_in_the_plan_at_all(self):
        self.assertIsNotNone(
            self.step,
            "nothing waits for the RX-888 before `config init radiod`, whose "
            "miss fails a HARD checkpoint and aborts the whole bring-up")

    def test_the_gate_passes_an_explicit_wait(self):
        argv = ' '.join(self.step.argv)
        self.assertIn('--wait-seconds', argv,
                      "without an explicit wait the gate takes sdr-recover's "
                      "60 s default, which is a recovery-run figure, not a "
                      "first-boot one")

    def test_the_wait_covers_the_measured_worst_case(self):
        # AI6VN took over three minutes from the gate running to a usable
        # card.  Anything at or under that reproduces the failure exactly.
        self.assertGreaterEqual(
            RX888_FIRSTBOOT_WAIT_S, 200,
            "AI6VN needed >3 minutes; a shorter wait reproduces the v3.78 "
            "greenfield failure where radiod was never enabled")
        argv = self.step.argv
        self.assertEqual(argv[argv.index('--wait-seconds') + 1],
                         str(RX888_FIRSTBOOT_WAIT_S))

    def test_the_gate_still_may_fail(self):
        # ⛔ A station with NO card, or one whose hub cannot switch power,
        # must still bring up everything else.  The wait must not turn a
        # cardless station into a failed install.
        self.assertTrue(
            self.step.may_fail,
            "the gate must stay non-fatal: a station with no local card "
            "still brings up its other clients")

    def test_the_gate_runs_before_radiod_is_configured(self):
        argvs = [' '.join(s.argv or []) for s in self.plan.steps]
        gate = next(i for i, a in enumerate(argvs) if 'sigmond-sdr-recover' in a)
        cfg = next(i for i, a in enumerate(argvs) if 'config init radiod' in a)
        self.assertLess(gate, cfg,
                        "waiting for the card AFTER config init has already "
                        "probed the bus helps nothing — that was the v3.42 bug")


if __name__ == '__main__':
    unittest.main()
