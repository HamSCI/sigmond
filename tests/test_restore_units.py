"""Tests for sigmond.restore_units: which installed unit files a rollback
retires (v3.73 row 4).

A restore moves a component back to an older commit.  A unit file that the
newer commit ships and the older one does not stays installed, because the
older install.sh knows nothing of it.  plan_retire picks those files out,
and only those whose installed bytes still equal what the newer commit
shipped: a file somebody edited after install stays where it is.
"""
import unittest
from pathlib import Path

from sigmond.restore_units import plan_retire

SYS = Path('/etc/systemd/system')


def _plan(removed, newer, installed, dest_map=None):
    return plan_retire(removed,
                       read_newer=lambda p: newer.get(p),
                       read_installed=lambda d: installed.get(d),
                       dest_map=dest_map or {},
                       systemd_dir=SYS)


class PlanRetireTests(unittest.TestCase):

    def test_a_unit_only_the_newer_commit_ships_is_retired(self):
        retire, kept = _plan(['systemd/sigmond-new.timer'],
                             {'systemd/sigmond-new.timer': b'T'},
                             {SYS / 'sigmond-new.timer': b'T'})
        self.assertEqual(retire, [SYS / 'sigmond-new.timer'])
        self.assertEqual(kept, [])

    def test_an_edited_install_stays_and_says_why(self):
        retire, kept = _plan(['systemd/sigmond-new.service'],
                             {'systemd/sigmond-new.service': b'shipped'},
                             {SYS / 'sigmond-new.service': b'edited'})
        self.assertEqual(retire, [])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0][0], SYS / 'sigmond-new.service')
        self.assertIn('differs', kept[0][1])

    def test_a_file_never_installed_needs_nothing(self):
        retire, kept = _plan(['systemd/sigmond-new.service'],
                             {'systemd/sigmond-new.service': b'S'}, {})
        self.assertEqual((retire, kept), ([], []))

    def test_a_drop_in_lands_where_the_dest_map_says(self):
        dest_map = {'radiod-new.conf': 'radiod@.service.d/60-sigmond-new.conf'}
        dest = SYS / 'radiod@.service.d' / '60-sigmond-new.conf'
        retire, _ = _plan(['systemd/radiod-new.conf'],
                          {'systemd/radiod-new.conf': b'D'}, {dest: b'D'},
                          dest_map=dest_map)
        self.assertEqual(retire, [dest])

    def test_a_conf_without_a_mapping_is_left_alone(self):
        # A .conf has no fixed home, so nothing can be said about it.
        retire, kept = _plan(['systemd/other.conf'],
                             {'systemd/other.conf': b'C'},
                             {SYS / 'other.conf': b'C'})
        self.assertEqual((retire, kept), ([], []))

    def test_paths_outside_systemd_are_ignored(self):
        retire, kept = _plan(['bin/sigmond-new', 'docs/x.service'],
                             {'bin/sigmond-new': b'B', 'docs/x.service': b'X'},
                             {SYS / 'x.service': b'X'})
        self.assertEqual((retire, kept), ([], []))

    def test_an_unreadable_newer_blob_retires_nothing(self):
        retire, kept = _plan(['systemd/sigmond-new.timer'], {},
                             {SYS / 'sigmond-new.timer': b'T'})
        self.assertEqual(retire, [])
        self.assertEqual(len(kept), 1)


if __name__ == '__main__':
    unittest.main()
