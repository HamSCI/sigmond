"""A deliberate hold is not a failure, and must not read as one.

`smd update` REFUSES a component whose local change collides with the
incoming one — on DASI002 that change was a real uncommitted fix, and
wspr-recorder's `uv.lock` is rewritten by install.sh on every host, so
holds are routine rather than exceptional.

But a Refusal set the same exit code (1) as a genuine failure, so no
caller could tell "the tool deliberately declined" from "something
broke". `smd fleet update` consequently reported an entire healthy host
as FAILED.

Worse: with a component held, the plan is never empty, so `smd update`
never printed its "nothing to do" sentinel — and the fleet post-check
requires that sentinel to call a host arrived. A host with routine
uv.lock churn could therefore NEVER verify, and would halt the wave
permanently.

So: a distinct exit code for held-but-nothing-failed, and a
"nothing to do" line that is still emitted when the only remaining plan
entries are holds.
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


class UpdateExitCodeTest(unittest.TestCase):

    def test_clean_run_exits_zero(self):
        self.assertEqual(smd._update_exit(held=0, failed=0), 0)

    def test_a_real_failure_exits_one(self):
        self.assertEqual(smd._update_exit(held=0, failed=1), 1)

    def test_a_hold_alone_gets_its_own_code(self):
        """Non-zero — the host is not fully updated — but distinguishable
        from a failure."""
        rc = smd._update_exit(held=1, failed=0)
        self.assertNotEqual(rc, 0)
        self.assertNotEqual(rc, 1)

    def test_a_failure_outranks_a_hold(self):
        """If anything actually broke, that is the headline."""
        self.assertEqual(smd._update_exit(held=1, failed=1), 1)


class NoActionableStepsTest(unittest.TestCase):
    """What a host says when nothing is left to run."""

    def test_a_fully_current_host_is_unchanged(self):
        rc, line = smd._no_actionable_outcome([])
        self.assertEqual(rc, 0)
        self.assertIn('nothing to do', line)

    def test_a_held_host_still_says_nothing_to_do(self):
        """The fleet post-check keys on this phrase. Without it a host
        carrying routine churn can never verify, and blocks the wave."""
        rc, line = smd._no_actionable_outcome(['wspr-recorder'])
        self.assertIn('nothing to do', line)
        self.assertNotEqual(rc, 0)

    def test_a_held_host_names_what_is_held(self):
        """'nothing to do' alone would hide the hold."""
        _rc, line = smd._no_actionable_outcome(['wspr-recorder'])
        self.assertIn('wspr-recorder', line)
        self.assertIn('HELD', line)

    def test_several_holds_are_all_named(self):
        _rc, line = smd._no_actionable_outcome(['wspr-recorder', 'ka9q-web'])
        self.assertIn('wspr-recorder', line)
        self.assertIn('ka9q-web', line)


if __name__ == '__main__':
    unittest.main()
