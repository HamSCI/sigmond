"""Guest-side kernel isolation (tuning doc Part 4b).

The regression these cover: W3USR-06 ran from build until 2026-09-28 with no
guest isolcpus at all, and `smd admin diag cpu-affinity` reported it clean the
whole time, because the warning was guarded by `if isol and ...` -- so the one
case that said nothing was the case where nothing was isolated.
"""
import unittest
from unittest import mock

from sigmond.cpu import (
    AffinityPlan,
    build_affinity_report,
    guest_isolation_reboot_pending,
    recommended_isolcpus,
    render_guest_isolation,
)


class RenderGuestIsolation(unittest.TestCase):

    def test_all_three_params_carry_the_same_set(self):
        out = render_guest_isolation({12, 13})
        self.assertIn('isolcpus=12-13', out)
        self.assertIn('nohz_full=12-13', out)
        self.assertIn('rcu_nocbs=12-13', out)

    def test_appends_rather_than_replaces_the_cmdline(self):
        # Clobbering GRUB_CMDLINE_LINUX_DEFAULT would drop console= and
        # friends, which on a headless station removes the serial console --
        # the only recovery path if the box does not come back.
        self.assertIn('${GRUB_CMDLINE_LINUX_DEFAULT}', render_guest_isolation({12, 13}))

    def test_follows_the_plan_not_the_docs_low_cpu_assumption(self):
        # The tuning doc's Part 4b table says isolcpus=0-(2N-1).  smd placed
        # radiod at 12-13 on W3USR-06; isolating 0-1 there would fence the
        # decoders and leave radiod exposed -- the exact inverse.
        out = render_guest_isolation({12, 13})
        self.assertNotIn('isolcpus=0-1', out)

    def test_non_contiguous_set_renders_as_a_list(self):
        out = render_guest_isolation({2, 3, 10, 11})
        self.assertIn('isolcpus=2-3,10-11', out)


class RebootPending(unittest.TestCase):

    def test_pending_when_cmdline_has_no_isolcpus(self):
        self.assertTrue(guest_isolation_reboot_pending({12, 13}, 'ro quiet'))

    def test_not_pending_once_the_running_kernel_carries_it(self):
        self.assertFalse(guest_isolation_reboot_pending(
            {12, 13}, 'ro quiet isolcpus=12,13 nohz_full=12,13'))

    def test_pending_when_the_running_kernel_isolates_only_part(self):
        self.assertTrue(guest_isolation_reboot_pending({12, 13}, 'ro isolcpus=12'))

    def test_superset_on_the_cmdline_is_not_pending(self):
        self.assertFalse(guest_isolation_reboot_pending({12, 13}, 'ro isolcpus=10-13'))

    def test_nothing_wanted_is_never_pending(self):
        self.assertFalse(guest_isolation_reboot_pending(set(), 'ro quiet'))


class RecommendedSetDrivesTheConfig(unittest.TestCase):

    def test_plain_host_isolates_radiods_own_cores(self):
        plan = AffinityPlan(radiod={'radiod@a.service': [12, 13]},
                            other_cpus={0, 1}, cache_split=False)
        self.assertEqual(recommended_isolcpus(plan), {12, 13})

    def test_split_l3_isolates_the_whole_island(self):
        # Cores sharing radiod's L3 slice must stay quiet or they evict it,
        # so the isolated set is wider than radiod's own pin.
        plan = AffinityPlan(radiod={'radiod@a.service': [12, 13]},
                            other_cpus={0, 1}, cache_split=True,
                            radiod_l3_cpus={8, 9, 10, 11, 12, 13})
        self.assertEqual(recommended_isolcpus(plan), {8, 9, 10, 11, 12, 13})


class UnisolatedHostIsNotSilent(unittest.TestCase):
    """The actual W3USR-06 regression."""

    def _report_with(self, isolated):
        plan = AffinityPlan(radiod={'radiod@W3USR-06.service': [12, 13]},
                            other_cpus=set(range(12)), cache_split=False)
        with mock.patch('sigmond.cpu.compute_affinity_plan', return_value=plan), \
             mock.patch('sigmond.cpu.get_isolated_cpus', return_value=isolated), \
             mock.patch('sigmond.cpu.get_radiod_cpus_by_unit',
                        return_value={'radiod@W3USR-06.service': {12, 13}}):
            return build_affinity_report()

    def test_no_isolation_at_all_produces_a_warning(self):
        joined = ' '.join(self._report_with(set()).warnings)
        self.assertIn('NOT fenced', joined)
        self.assertIn('isolcpus', joined)

    def test_correct_isolation_produces_no_isolation_warning(self):
        self.assertNotIn('NOT fenced',
                         ' '.join(self._report_with({12, 13}).warnings))

    def test_partial_isolation_still_warns_but_differently(self):
        # cpu 13 isolated, 12 not: the old wording is right here, the new one
        # would be wrong -- something IS isolated, just not enough.
        joined = ' '.join(self._report_with({13}).warnings)
        self.assertIn('outside isolated pool', joined)
        self.assertNotIn('NOT fenced', joined)


if __name__ == '__main__':
    unittest.main()
