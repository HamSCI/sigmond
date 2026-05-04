"""Tests for sigmond.ui.format_data_path_tag (CONTRACT-v0.5 §16.7)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond.ui import format_data_path_tag


class TestFormatDataPathTag(unittest.TestCase):

    def test_none_or_missing_yields_empty(self):
        self.assertEqual(format_data_path_tag(None), '')
        self.assertEqual(format_data_path_tag({}), '')

    def test_radiod_ka9q_python_is_default_no_tag(self):
        dp = {'kind': 'radiod-ka9q-python', 'radiod_id': 'k3lr'}
        self.assertEqual(format_data_path_tag(dp), '')

    def test_radiod_direct(self):
        dp = {'kind': 'radiod-direct', 'radiod_id': 'k3lr'}
        self.assertEqual(format_data_path_tag(dp), '[radiod-direct]')

    def test_kiwisdr(self):
        dp = {'kind': 'kiwisdr', 'details': {'hostname': 'kiwi.example'}}
        self.assertEqual(format_data_path_tag(dp), '[kiwisdr]')

    def test_meta_client_file_with_upstream(self):
        dp = {
            'kind': 'file',
            'details': {'upstream_client': 'wspr-recorder',
                        'spool': '/var/spool/wsprdaemon/recording/X'},
        }
        self.assertEqual(format_data_path_tag(dp), '[file:wspr-recorder]')

    def test_replay_file_without_upstream(self):
        dp = {'kind': 'file', 'details': {'description': 'test fixture'}}
        self.assertEqual(format_data_path_tag(dp), '[file]')

    def test_replay_file_no_details(self):
        self.assertEqual(format_data_path_tag({'kind': 'file'}), '[file]')

    def test_other_kind_passes_through(self):
        self.assertEqual(format_data_path_tag({'kind': 'other'}), '[other]')
        self.assertEqual(
            format_data_path_tag({'kind': 'something-new'}),
            '[something-new]',
        )

    def test_non_dict_input(self):
        self.assertEqual(format_data_path_tag('not a dict'), '')
        self.assertEqual(format_data_path_tag(['kind', 'file']), '')


if __name__ == '__main__':
    unittest.main()
