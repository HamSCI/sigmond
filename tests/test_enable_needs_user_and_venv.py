"""No unit is enabled before its service user and its executable exist, and
`smd config init` enables nothing for a client topology has turned off.

WWV-2 (fresh bare-metal install, 2026-11-16): station-web.service was enabled
with no `stationweb` user and no venv -- 217/USER, then 203/EXEC, in a
restart loop -- and mag-recorder was enabled on a host with no magnetometer,
where its install had created no `magrec` user (217/USER).  e806b57 moved
enabling from install to `smd config init`; `smd apply` and `smd enable`
still enable every declared unit.  The gate below sits in
apply_systemd_enables (apply, config init) and in smd's
_enable_units_for_boot (smd enable).
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond import installer
from sigmond.commands import client_config
from sigmond.lifecycle import UnitRef

REPO = Path(__file__).resolve().parent.parent

WEB = """# /etc/systemd/system/station-web.service
[Unit]
Description=station web

[Service]
User=stationweb
ExecStart=/opt/git/sigmond/station-web/venv/bin/station-web --port 8080

[Install]
WantedBy=multi-user.target
"""

UPLOAD_TIMER = """[Unit]
Description=upload timer

[Timer]
OnCalendar=daily

[Install]
WantedBy=timers.target
"""

UPLOAD_SERVICE = """[Service]
Type=oneshot
User=magrec
ExecStart=-/usr/local/bin/mag-recorder upload
"""


def _systemctl(units, states, calls):
    def run(argv, **kw):
        calls.append(list(argv))
        i = argv.index('systemctl')
        verb, unit = argv[i + 1], argv[i + 2] if len(argv) > i + 2 else ''
        if verb == 'is-enabled':
            return subprocess.CompletedProcess(argv, 0, states.get(unit, 'disabled') + '\n', '')
        if verb == 'cat':
            if unit in units:
                return subprocess.CompletedProcess(argv, 0, units[unit], '')
            return subprocess.CompletedProcess(argv, 1, '', f'No files found for {unit}.')
        return subprocess.CompletedProcess(argv, 0, '', '')
    return run


class Gate(unittest.TestCase):

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.repo = Path(td.name)
        self.calls = []

    def enables(self, units, deployed, *, users=(), paths=()):
        (self.repo / 'deploy.toml').write_text(
            '[systemd]\nunits = [' + ', '.join(f'"{u}"' for u in deployed) + ']\n')
        with mock.patch.object(installer.subprocess, 'run',
                               _systemctl(units, {}, self.calls)), \
             mock.patch.object(installer, '_user_exists', lambda u: u in users), \
             mock.patch.object(installer, '_path_exists', lambda p: str(p) in paths):
            return installer.apply_systemd_enables(self.repo)

    def enabled(self):
        return [c[-1] for c in self.calls if 'enable' in c]

    def test_no_user_no_enable(self):
        msgs = self.enables({'station-web.service': WEB}, ['station-web.service'],
                            paths={'/opt/git/sigmond/station-web/venv/bin/station-web'})
        self.assertEqual(self.enabled(), [])
        self.assertTrue(any('stationweb' in m and m.startswith('  warning') for m in msgs), msgs)

    def test_no_venv_no_enable(self):
        msgs = self.enables({'station-web.service': WEB}, ['station-web.service'],
                            users={'stationweb'})
        self.assertEqual(self.enabled(), [])
        self.assertTrue(any('venv/bin/station-web' in m for m in msgs), msgs)

    def test_user_and_venv_present_is_enabled(self):
        self.enables({'station-web.service': WEB}, ['station-web.service'],
                     users={'stationweb'},
                     paths={'/opt/git/sigmond/station-web/venv/bin/station-web'})
        self.assertEqual(self.enabled(), ['station-web.service'])

    def test_a_timer_waits_for_its_services_user(self):
        msgs = self.enables({'mag-recorder-upload.timer': UPLOAD_TIMER,
                             'mag-recorder-upload.service': UPLOAD_SERVICE},
                            ['mag-recorder-upload.timer'],
                            paths={'/usr/local/bin/mag-recorder'})
        self.assertEqual(self.enabled(), [])
        self.assertFalse(any('start' in c for c in self.calls))
        self.assertTrue(any('magrec' in m for m in msgs), msgs)

    def test_a_drop_in_reset_execstart_is_honoured(self):
        text = WEB + ('\n# /etc/systemd/system/station-web.service.d/x.conf\n'
                      '[Service]\nExecStart=\nExecStart=/usr/bin/true\n')
        self.enables({'station-web.service': text}, ['station-web.service'],
                     users={'stationweb'}, paths={'/usr/bin/true'})
        self.assertEqual(self.enabled(), ['station-web.service'])

    def test_specifiers_and_dynamic_users_are_not_judged(self):
        text = ('[Service]\nDynamicUser=yes\nUser=x-%i\n'
                'ExecStart=%h/bin/thing\n[Install]\nWantedBy=multi-user.target\n')
        self.enables({'odd.service': text}, ['odd.service'])
        self.assertEqual(self.enabled(), ['odd.service'])


class ConfigInitRespectsTopology(unittest.TestCase):

    def topo(self, text):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / 'topology.toml'
        p.write_text(text)
        return p

    def run_enable(self, topo_path, client='mag-recorder'):
        with mock.patch.object(client_config, 'TOPOLOGY_PATH', topo_path), \
             mock.patch('sigmond.installer.apply_systemd_enables',
                        return_value=[]) as ase, \
             mock.patch.object(client_config, 'info') as info:
            client_config._enable_configured_units(Path('/nonexistent'), client=client)
        return ase, ' '.join(str(c) for c in info.call_args_list)

    def test_a_client_topology_turned_off_is_not_enabled(self):
        p = self.topo('[component.mag-recorder]\nenabled = false\n')
        ase, said = self.run_enable(p)
        ase.assert_not_called()
        self.assertIn('topology', said)

    def test_an_enabled_client_is(self):
        p = self.topo('[component.mag-recorder]\nenabled = true\n')
        ase, _ = self.run_enable(p)
        ase.assert_called_once()

    def test_no_topology_file_changes_nothing(self):
        ase, _ = self.run_enable(Path('/nonexistent/topology.toml'))
        ase.assert_called_once()


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader("smd_under_test_enable_gate",
                                                  str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_enable_gate", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class SmdEnable(unittest.TestCase):
    """`smd enable` persists units for boot; not one that cannot run."""

    def test_a_unit_without_its_user_is_not_enabled_at_boot(self):
        smd = _load_smd()
        calls = []
        unit = UnitRef(component='station-web', unit='station-web.service',
                       template=None, instance=None, kind='service',
                       source='deploy.toml:x')

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            if cmd[:2] == ['systemctl', 'cat']:
                return subprocess.CompletedProcess(cmd, 0, WEB, '')
            return subprocess.CompletedProcess(cmd, 0, 'disabled\n', '')

        with mock.patch.object(smd, '_run', side_effect=fake_run), \
             mock.patch.object(smd, 'resolve_units', return_value=[unit]), \
             mock.patch.object(installer, '_user_exists', lambda u: False), \
             mock.patch.object(installer, '_path_exists', lambda p: True), \
             mock.patch.object(smd, '_warn') as warn:
            done = smd._enable_units_for_boot('station-web')
        self.assertEqual(done, [])
        self.assertNotIn(['systemctl', 'enable', 'station-web.service'], calls)
        self.assertIn('stationweb', ' '.join(str(c) for c in warn.call_args_list))


    def test_smd_start_neither_enables_nor_starts_a_unit_that_cannot_run(self):
        smd = _load_smd()
        calls = []

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            if cmd[:2] == ['systemctl', 'cat']:
                return subprocess.CompletedProcess(cmd, 0, WEB, '')
            return subprocess.CompletedProcess(cmd, 3, '', '')   # not enabled

        with mock.patch.object(smd, '_run', side_effect=fake_run), \
             mock.patch.object(installer, '_user_exists', lambda u: False), \
             mock.patch.object(installer, '_path_exists', lambda p: True), \
             mock.patch.object(smd, '_err'):
            ready = smd._enable_unit_for_start('station-web.service')
        self.assertFalse(ready)
        self.assertFalse(any(c[:2] == ['systemctl', 'enable'] for c in calls))


if __name__ == '__main__':
    unittest.main()
