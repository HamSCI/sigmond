"""The wizard must not promise an auto-start it retired.

⛔ Why this check exists.  `3c65569` retired `sigmond-sdr-sentinel.timer`, the
timer that every two minutes ran `smd bringup dasi2 --non-interactive` whenever
it saw an RX888 and no radiod conf.  The mechanism went; three pieces of
operator-facing prose describing it stayed behind.  Two of them print on the
console during a bare install:

  * the pre-flight told the operator that anything added later gets "picked up
    automatically";
  * the no-RX888 close said "the sentinel then brings radiod up automatically
    within ~2 min";
  * the wedged-FX3 branch said radiod "starts automatically" after the next
    power-on.

None of that happens now.  A station installed with nothing attached — the
Fargo sequence this design exists for — waits for `smd adopt` and for nothing
else.  Prose that outlives its mechanism misleads exactly the operator standing
at the console with no way to check, so it earns a test of its own, the same
way the roster's placeholder sentinel did.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WIZARD = REPO / "scripts" / "proxmox" / "sigmond-wizard.sh"

# Lines that REMOVE the retired timer are legitimate and must survive; only the
# promises are forbidden.  So match the claim, never the unit name.
RETIRED_PROMISES = (
    "the SDR sentinel watches",
    "sentinel then brings radiod up",
    "picked up automatically",
    "radiod starts automatically",
    "radiod comes up",
)


class WizardProse(unittest.TestCase):

    def setUp(self):
        self.text = WIZARD.read_text()

    def test_the_wizard_promises_no_automatic_bringup(self):
        """Nothing starts radiod on its own any more, so nothing may say so."""
        for claim in RETIRED_PROMISES:
            with self.subTest(claim=claim):
                # Report the offending LINES.  Asserting against the whole
                # file dumps 75 KB of wizard into the failure and buries the
                # one line that matters.
                hits = [f"{n}: {line.strip()}"
                        for n, line in enumerate(self.text.splitlines(), 1)
                        if claim in line]
                self.assertEqual(
                    [], hits,
                    f"wizard still promises {claim!r}:\n" + "\n".join(hits))

    def test_the_bare_install_names_the_verb_that_starts_things(self):
        """A station with no RX888 must be told what to run when one arrives."""
        m = re.search(r'^else\n\s*RADIOD_STATE="NO RX888.*?"\n',
                      self.text, re.S | re.M)
        self.assertIsNotNone(
            m, "the no-RX888 RADIOD_STATE branch moved — re-anchor this test")
        self.assertIn("smd adopt", m.group(0))

    def test_the_preflight_names_adopt_when_equipment_is_missing(self):
        """The pre-flight lists what is missing; it must say what closes it."""
        m = re.search(r'Missing equipment does not stop this install.*?fi\n',
                      self.text, re.S)
        self.assertIsNotNone(
            m, "the missing-equipment paragraph moved — re-anchor this test")
        self.assertIn("smd adopt", m.group(0))

    def test_the_retirement_itself_survives(self):
        """Guard the guard: the test above must not be satisfiable by deleting
        the code that removes the timer from an existing station."""
        self.assertIn("systemctl disable --now sigmond-sdr-sentinel.timer",
                      self.text)


if __name__ == "__main__":
    unittest.main()
