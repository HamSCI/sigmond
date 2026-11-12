"""KJ6MKI, 2026-11-12: `smd sink upload` re-rendered pipelines.toml and broke
a converted station's WD-servers-only routing.

* a producer delivering via the WD servers (psk-recorder ``server-merge``,
  meteor-scatter ``deposit``) gets no direct pskreporter pipeline;
* a site's ``[identity] ssh_key_file`` in the installed manifest survives;
* a PSWS pipeline skipped for a missing recorder config says how to fix it.
"""

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond import uploader_manifest as um

PSK_OWN = um.PSK_PRODUCERS['psk-recorder'][0]
MS_OWN = um.PSK_PRODUCERS['meteor-scatter'][0]

SERVER = {'psk-recorder': {'PSK_DELIVERY_PIPELINES': 'server-merge'},
          'meteor-scatter': {'METEOR_SCATTER_DELIVERY_MODE': 'deposit'}}


def _env(table):
    """A units_env stub: client -> [(unit, env)] from ``table``; a value of
    OSError raises it."""
    def read(client, topology):
        v = table.get(client, [])
        if v is OSError:
            raise OSError('systemctl failed')
        return v
    return read


class ServerDeliveredProducers(unittest.TestCase):

    def test_server_merge_and_deposit_both_count(self):
        got = um.server_delivered_producers(None, units_env=_env({
            'psk-recorder': [('psk-recorder@A', SERVER['psk-recorder'])],
            'meteor-scatter': [('meteor-scatter@A', SERVER['meteor-scatter'])],
        }))
        self.assertEqual(got, {'psk-recorder', 'meteor-scatter'})

    def test_unset_psk_env_is_the_server_merge_default(self):
        got = um.server_delivered_producers(None, units_env=_env({
            'psk-recorder': [('psk-recorder@A', {})]}))
        self.assertEqual(got, {'psk-recorder'})

    def test_unset_meteor_env_is_direct_and_keeps_the_pipeline(self):
        got = um.server_delivered_producers(None, units_env=_env({
            'meteor-scatter': [('meteor-scatter@A', {})]}))
        self.assertEqual(got, set())

    def test_one_lowered_instance_keeps_the_pipeline(self):
        got = um.server_delivered_producers(None, units_env=_env({
            'psk-recorder': [
                ('psk-recorder@A', SERVER['psk-recorder']),
                ('psk-recorder@B', {'PSK_DELIVERY_PIPELINES': 'server-raw'})],
        }))
        self.assertEqual(got, set())

    def test_server_merge_beside_direct_keeps_the_pipeline(self):
        got = um.server_delivered_producers(None, units_env=_env({
            'psk-recorder': [('psk-recorder@A', {
                'PSK_DELIVERY_PIPELINES': 'direct,server-merge'})]}))
        self.assertEqual(got, set())

    def test_no_instances_keeps_the_pipeline(self):
        self.assertEqual(um.server_delivered_producers(
            None, units_env=_env({})), set())

    def test_unreadable_env_keeps_the_pipeline(self):
        with self.assertLogs(um.logger, logging.WARNING):
            got = um.server_delivered_producers(None, units_env=_env({
                'psk-recorder': OSError,
                'meteor-scatter': [('meteor-scatter@A',
                                    SERVER['meteor-scatter'])]}))
        self.assertEqual(got, {'meteor-scatter'})


class DropServerDelivered(unittest.TestCase):

    PIPES = [{'name': 'wspr-wsprdaemon'}, {'name': PSK_OWN},
             {'name': MS_OWN}]

    def names(self, out):
        return [p['name'] for p in out]

    def test_split_drops_only_that_producers_pipeline(self):
        out = um.drop_server_delivered_pipelines(
            self.PIPES, {'psk-recorder'},
            store_version=um.SPLIT_STORE_VERSION)
        self.assertEqual(self.names(out), ['wspr-wsprdaemon', MS_OWN])

    def test_split_drops_both(self):
        out = um.drop_server_delivered_pipelines(
            self.PIPES, {'psk-recorder', 'meteor-scatter'},
            store_version=um.SPLIT_STORE_VERSION)
        self.assertEqual(self.names(out), ['wspr-wsprdaemon'])

    def test_shared_pipeline_goes_only_when_both_producers_do(self):
        pipes = [{'name': 'wspr-wsprdaemon'}, {'name': um.SHARED_PSK_PIPELINE}]
        one = um.drop_server_delivered_pipelines(
            pipes, {'psk-recorder'}, store_version=1)
        self.assertEqual(self.names(one),
                         ['wspr-wsprdaemon', um.SHARED_PSK_PIPELINE])
        both = um.drop_server_delivered_pipelines(
            pipes, {'psk-recorder', 'meteor-scatter'}, store_version=1)
        self.assertEqual(self.names(both), ['wspr-wsprdaemon'])

    def test_nothing_server_delivered_changes_nothing(self):
        out = um.drop_server_delivered_pipelines(
            self.PIPES, set(), store_version=um.SPLIT_STORE_VERSION)
        self.assertEqual(out, self.PIPES)


class SiteKeyOverride(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        d = Path(self.td.name)
        self.keys = d / 'keys'
        self.keys.mkdir()
        (self.keys / 'id_ed25519_host').write_text('k')
        self.wd_key = self.keys / 'id_rsa_wsprdaemon'
        self.manifest = d / 'pipelines.toml'
        for p in (mock.patch.object(um, 'KEYS_DIR', self.keys),
                  mock.patch.object(um, 'MANIFEST_PATH', self.manifest)):
            p.start()
            self.addCleanup(p.stop)

    def write_identity(self, key):
        self.manifest.write_text(
            f'[identity]\ncall = "KJ6MKI"\nssh_key_file = "{key}"\n')

    def test_no_manifest_uses_the_default(self):
        self.assertEqual(um.host_key_file(),
                         str(self.keys / 'id_ed25519_host'))

    def test_an_existing_site_key_survives(self):
        self.wd_key.write_text('k')
        self.write_identity(self.wd_key)
        self.assertEqual(um.host_key_file(), str(self.wd_key))
        self.assertEqual(um.resolve_tokens(
            'wspr-recorder', mock.Mock(host=mock.Mock(grid='CM87'),
                                       radiods={}), 'KJ6MKI')
            ['ssh_key_file'], str(self.wd_key))

    def test_a_missing_site_key_falls_back_to_the_default(self):
        self.write_identity(self.wd_key)          # file never created
        self.assertEqual(um.host_key_file(),
                         str(self.keys / 'id_ed25519_host'))

    def test_an_unreadable_manifest_falls_back_to_the_default(self):
        self.manifest.write_text('not = [toml')
        self.assertEqual(um.host_key_file(),
                         str(self.keys / 'id_ed25519_host'))

    def test_build_identity_carries_the_site_key(self):
        self.wd_key.write_text('k')
        self.write_identity(self.wd_key)
        coord = mock.Mock()
        coord.host.grid = 'CM87'
        with mock.patch.object(um, '_psws_station_for_host',
                               return_value=None):
            ident = um.build_identity(coord, 'KJ6MKI')
        self.assertEqual(ident['ssh_key_file'], str(self.wd_key))


if __name__ == '__main__':
    unittest.main()
