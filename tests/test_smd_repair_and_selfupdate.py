"""Two verbs that told the operator something untrue.

1. `smd doctor --fix` printed the findings from its PRE-fix scan after
   repairing them, so a successful repair read as a failure — "repaired
   44 path(s)" immediately followed by "44 path(s) not owned by sigmond".
   Observed on DASI002 2026-08-17. An operator seeing that either runs
   the fix again or stops trusting the tool.

2. `smd component update sigmond` silently did nothing. The self-update
   block was gated on `not components_filter`, so naming the component
   skipped it — the command printed "all components up to date" while
   its own table showed sigmond 15 commits behind. `_component_statuses`
   already had the right predicate (`not filter or 'sigmond' in
   filter`); `_apply_updates` simply never matched it.

Both are the same failure class as the missing fetch: a command that
reports success while doing nothing, or reports failure after
succeeding.
"""

import importlib.machinery
import importlib.util
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


class RepairAndRecheckTest(unittest.TestCase):
    """A repair must be reported from a scan taken AFTER it ran."""

    def setUp(self):
        self.chowned = []

    def _chown(self, d, uid):
        self.chowned.append((d, uid))

    def test_a_full_repair_leaves_nothing_outstanding(self):
        bad = [Path('/x/.git/index'), Path('/x/.git/objects/7a')]
        repaired, still = smd._repair_and_recheck(
            Path('/x'), 1000, bad, chown=self._chown, rescan=lambda d, u: [])
        self.assertEqual(repaired, 2)
        self.assertEqual(still, [])

    def test_a_partial_repair_reports_only_what_remains(self):
        """The half that could not be fixed must still be reported."""
        bad = [Path('/x/a'), Path('/x/b'), Path('/x/c')]
        repaired, still = smd._repair_and_recheck(
            Path('/x'), 1000, bad, chown=self._chown,
            rescan=lambda d, u: [Path('/x/c')])
        self.assertEqual(repaired, 2)
        self.assertEqual(still, [Path('/x/c')])

    def test_a_repair_that_changed_nothing_reports_zero_not_success(self):
        """chown can fail silently (check=False). Claiming a repair that
        did not happen is the worse of the two errors."""
        bad = [Path('/x/a'), Path('/x/b')]
        repaired, still = smd._repair_and_recheck(
            Path('/x'), 1000, bad, chown=self._chown, rescan=lambda d, u: bad)
        self.assertEqual(repaired, 0)
        self.assertEqual(still, bad)

    def test_the_chown_actually_runs(self):
        smd._repair_and_recheck(Path('/x'), 1000, [Path('/x/a')],
                                chown=self._chown, rescan=lambda d, u: [])
        self.assertEqual(self.chowned, [(Path('/x'), 1000)])


class SigmondSelfUpdateTargetingTest(unittest.TestCase):

    def test_a_bare_update_self_updates(self):
        self.assertTrue(smd._wants_sigmond_self_update(None))

    def test_naming_sigmond_self_updates(self):
        """`smd component update sigmond` must update sigmond.

        It previously printed 'all components up to date' while the same
        table reported it 15 behind.
        """
        self.assertTrue(smd._wants_sigmond_self_update({'sigmond'}))

    def test_naming_sigmond_alongside_others_self_updates(self):
        self.assertTrue(
            smd._wants_sigmond_self_update({'sigmond', 'psk-recorder'}))

    def test_naming_only_another_component_does_not(self):
        """Targeting is still respected — asking for psk-recorder must
        not quietly pull sigmond too."""
        self.assertFalse(smd._wants_sigmond_self_update({'psk-recorder'}))

    def test_it_matches_the_predicate_the_status_table_already_used(self):
        """`_component_statuses` had this right all along (bin/smd:7862).
        The two must agree, or the table and the action disagree about
        whether sigmond is in scope — which is exactly what happened."""
        for flt in (None, {'sigmond'}, {'psk-recorder'},
                    {'sigmond', 'psk-recorder'}, set()):
            expected = (not flt) or ('sigmond' in flt)
            self.assertEqual(smd._wants_sigmond_self_update(flt), bool(expected),
                             f'disagreement for {flt!r}')


if __name__ == '__main__':
    unittest.main()
