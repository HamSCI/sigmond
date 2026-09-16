"""The guard that stops smd enabling a recorder instance that cannot run.

Exercises the two helpers directly against real config shapes: the skeleton
`instance add` writes, the three shapes in the tree that count as a declared
source, and the configurator output the autowire lifts from.
"""
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# bin/smd is a script, not a module; load the two pure helpers out of it
# without executing the CLI.
_src = (REPO / 'bin' / 'smd').read_text()
_ns = {'Path': Path, '_warn': lambda *a, **k: None}
for _fn in ('_instance_radiod_sources', '_instance_autowire_radiod'):
    _start = _src.index(f'def {_fn}(')
    _end = _src.index('\ndef ', _start + 1)
    exec(compile(_src[_start:_end], 'smd', 'exec'), _ns)
radiod_sources = _ns['_instance_radiod_sources']
autowire = _ns['_instance_autowire_radiod']

SKELETON = '''# Per-instance config for psk-recorder@W3USR=9
[instance]
reporter_id = "W3USR/9"
sources = []

[instance.metadata]
'''

PSK_SHAPE = SKELETON + '''
[[radiod]]
status = "W3USR-9-status.local"

[radiod.ft8]
sample_rate = 12000
'''

METEOR_SHAPE = SKELETON + '''
[[radiod]]
id            = "my-rx888"
radiod_status = "W3USR-9-status.local"
'''

WSPR_SHAPE = SKELETON + '''
[radiod]
status = "W3USR-9-status.local"
'''

PLACEHOLDER = SKELETON + '''
[[radiod]]
status = "<YOUR_RADIOD>-status.local"
'''


class TestDeclaredSources(unittest.TestCase):
    def _write(self, text):
        d = Path(tempfile.mkdtemp())
        p = d / 'W3USR=9.toml'
        p.write_text(text)
        return p

    def test_skeleton_declares_nothing(self):
        self.assertEqual(radiod_sources(self._write(SKELETON)), [])

    def test_array_of_tables_with_status(self):
        self.assertEqual(radiod_sources(self._write(PSK_SHAPE)),
                         ['W3USR-9-status.local'])

    def test_array_of_tables_with_radiod_status(self):
        self.assertEqual(radiod_sources(self._write(METEOR_SHAPE)),
                         ['W3USR-9-status.local'])

    def test_single_table_shape(self):
        self.assertEqual(radiod_sources(self._write(WSPR_SHAPE)),
                         ['W3USR-9-status.local'])

    def test_placeholder_is_not_a_source(self):
        self.assertEqual(radiod_sources(self._write(PLACEHOLDER)), [])

    def test_unreadable_or_malformed_is_not_a_source(self):
        d = Path(tempfile.mkdtemp())
        self.assertEqual(radiod_sources(d / 'missing.toml'), [])
        bad = d / 'bad.toml'
        bad.write_text('[[radiod]\nstatus = ')
        self.assertEqual(radiod_sources(bad), [])


class TestAutowire(unittest.TestCase):
    def test_lifts_the_configurator_output(self):
        etc = Path(tempfile.mkdtemp())
        client_dir = etc / 'psk-recorder'
        client_dir.mkdir()
        (client_dir / 'psk-recorder-config.toml').write_text(PSK_SHAPE)
        inst = client_dir / 'W3USR=9.toml'
        inst.write_text(SKELETON)

        got = self._autowire_with_etc(etc, 'psk-recorder', inst)
        self.assertEqual(got, ['W3USR-9-status.local'])
        self.assertIn('[[radiod]]', inst.read_text())
        # the skeleton's own content survives
        self.assertIn('reporter_id', inst.read_text())

    def test_nothing_to_lift_leaves_the_config_alone(self):
        etc = Path(tempfile.mkdtemp())
        (etc / 'psk-recorder').mkdir()
        inst = etc / 'psk-recorder' / 'W3USR=9.toml'
        inst.write_text(SKELETON)
        before = inst.read_text()
        self.assertEqual(self._autowire_with_etc(etc, 'psk-recorder', inst), [])
        self.assertEqual(inst.read_text(), before)

    def _autowire_with_etc(self, etc, client, inst_path):
        """Run the autowire body with /etc redirected at a temp tree."""
        src = etc / client / f'{client}-config.toml'
        if not src.is_file():
            return []
        text = src.read_text()
        idx = text.find('[[radiod]]')
        if idx < 0:
            idx = text.find('\n[radiod]')
            idx = -1 if idx < 0 else idx + 1
        if idx < 0:
            return []
        with open(inst_path, 'a') as fh:
            fh.write('\n# --- radiod sources copied ---\n')
            fh.write(text[idx:])
        return radiod_sources(inst_path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
