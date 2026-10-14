"""Each client's copy of the reporter-id table matches sigmond's, byte for
byte (tasks/spec-reporter-id-v372.md §5, "The guard against drift").

psk-recorder, meteor-scatter and wspr-recorder each keep a small
_reporter_id.py rather than importing sigmond, and run it against their
copy of tests/data/reporter_id_vectors.json.  That only guards anything
while the copies agree.  A client checkout absent from beside sigmond
skips its test; a checkout that is present but lacks the copy fails.
"""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TABLE = REPO / "tests" / "data" / "reporter_id_vectors.json"


class SiblingTableTests(unittest.TestCase):

    def _check(self, client: str) -> None:
        checkout = REPO.parent / client
        if not checkout.is_dir():
            self.skipTest(f"no {client} checkout beside sigmond at {checkout}")
        copy = checkout / "tests" / "data" / "reporter_id_vectors.json"
        refresh = f"cp {TABLE} {copy}"
        self.assertTrue(copy.is_file(), f"{client} has no copy of the table; {refresh}")
        self.assertEqual(copy.read_bytes(), TABLE.read_bytes(),
                         f"{client}'s copy differs from sigmond's; {refresh}")

    def test_psk_recorder(self):
        self._check("psk-recorder")

    def test_meteor_scatter(self):
        self._check("meteor-scatter")

    def test_wspr_recorder(self):
        self._check("wspr-recorder")


if __name__ == "__main__":
    unittest.main()
