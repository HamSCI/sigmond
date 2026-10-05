"""No SDR at install: carry on without the radio, and never call it done.

mjh, 2026-10-04, from Fargo: "We should definitely not write 'done' when not
done.  If the SDR is not found, we can offer the user an abort but by default
continue to install without radiod-dependent steps."

WB6CXC-7 (2026-10-01) is the case that made it necessary: its RX-888
enumerated 32 minutes after bring-up looked, the hard 'radiod configured'
checkpoint aborted the run, and a never-again marker left the station
half-installed with every unit green.  These tests pin four things:

1. the plan installs everything but defers every radiod-dependent step, and
   leaves the radio half DISABLED so no catch-all start can launch it unconfigured;
2. a person at a terminal may abort, and the default is to carry on;
3. firstrun records result=awaiting-sdr, which is never final and spends no
   retry budget;
4. the SDR's arrival re-runs firstrun, and nothing in the unit stops it.
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from sigmond.catalog import Profile
from sigmond.bringup import build_plan

REPO = Path(__file__).resolve().parent.parent
FIRSTRUN = REPO / 'bin' / 'sigmond-firstrun-bringup'

RADIOD_BOUND = ('hf-timestd', 'wspr-recorder', 'psk-recorder')


def _dasi2():
    return Profile(
        name='dasi2',
        clients=('hf-timestd', 'wspr-recorder', 'psk-recorder', 'mag-recorder'),
        local_radiod_infra=('igmp-querier', 'gpsdo-monitor'),
        optional=('ka9q-web',),
    )


def _argvs(plan):
    return [' '.join(s.argv or []) for s in plan.steps]


class DeferredPlan(unittest.TestCase):

    def setUp(self):
        self.p = build_plan(_dasi2(), local_radiod=True, sdr_absent=True,
                            reporter='AC0G/T1')
        self.argv = _argvs(self.p)

    def test_the_plan_is_marked_partial(self):
        self.assertTrue(self.p.sdr_absent)
        self.assertFalse(build_plan(_dasi2(), local_radiod=True).sdr_absent)

    def test_no_radiod_dependent_step_survives(self):
        kinds = {s.kind for s in self.p.steps}
        self.assertNotIn('wisdom', kinds)
        self.assertNotIn('wait-wisdom', kinds)
        self.assertNotIn('wait-streaming', kinds)
        self.assertFalse(any(s.check == 'radiod-configured' for s in self.p.steps))
        self.assertFalse(any(a.endswith(' apply') for a in self.argv),
                         'smd apply starts radiod; it must wait for the SDR')
        for c in ('radiod',) + RADIOD_BOUND:
            self.assertFalse(any(f'config init {c}' in a for a in self.argv), c)
        self.assertFalse(any('instance' in a for a in self.argv))
        self.assertFalse(any('cpu-affinity' in a for a in self.argv))
        self.assertFalse(any('sigmond-sdr-recover' in a for a in self.argv))

    def test_the_radio_half_is_installed_but_never_enabled(self):
        for c in ('ka9q-radio', 'igmp-querier') + RADIOD_BOUND:
            inst = [a for a in self.argv if a.endswith(f'--components {c} --yes --no-enable')]
            self.assertEqual(len(inst), 1, f'{c} must install with --no-enable')
            self.assertFalse(any(a.endswith(f'enable {c}') for a in self.argv),
                             f'{c} must not be enabled before it is configured')
            self.assertFalse(any(a.endswith(f'start --components {c}') for a in self.argv), c)

    def test_the_independent_track_runs_whole(self):
        self.assertIn('smd enable mag-recorder', self.argv)
        self.assertTrue(any(a.endswith('install --components mag-recorder --yes')
                            for a in self.argv))
        self.assertIn('smd config init mag-recorder --non-interactive', self.argv)
        self.assertIn('smd start --components mag-recorder', self.argv)

    def test_the_station_level_steps_still_run(self):
        self.assertIn('smd admin uploader manifest --write --enable', self.argv)
        self.assertTrue(any(s.check == 'validate' for s in self.p.steps))

    def test_the_catch_all_start_names_only_what_the_plan_activated(self):
        # A bare `smd start` starts everything topology calls enabled, and the
        # golden VM enables the radio half before bring-up ever runs (v3.67
        # nested test: igmp-querier started, ka9q-web "unit not found", rc 1).
        self.assertNotIn('smd start', self.argv)
        catch_all = [' '.join(s.argv) for s in self.p.steps
                     if s.label.startswith('start the components this plan activated')]
        self.assertEqual(len(catch_all), 1, catch_all)
        named = catch_all[0].split()[-1].split(',')
        self.assertIn('mag-recorder', named)
        for c in ('ka9q-radio', 'ka9q-web', 'igmp-querier', 'gpsdo-monitor') + RADIOD_BOUND:
            self.assertNotIn(c, named, f'{c} is deferred; the catch-all must not start it')

    def test_a_full_plan_keeps_the_bare_catch_all(self):
        self.assertIn('smd start', _argvs(build_plan(_dasi2(), local_radiod=True)))

    def test_a_remote_radiod_host_is_never_deferred(self):
        p = build_plan(_dasi2(), local_radiod=False,
                       remote_status_dns='x-status.local', sdr_absent=True)
        self.assertFalse(p.sdr_absent)
        self.assertIn('smd enable wspr-recorder', _argvs(p))


def _load_smd():
    os.environ.setdefault('SIGMOND_NO_VENV_REEXEC', '1')
    loader = importlib.machinery.SourceFileLoader(
        'smd_under_test_no_sdr', str(REPO / 'bin' / 'smd'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class OperatorChoice(unittest.TestCase):

    def _run(self, answer, *, tty=True, non_interactive=False):
        smd = _load_smd()
        prof = Profile(name='t', description='', clients=(),
                       local_radiod_infra=('igmp-querier',), optional=())
        args = Namespace(profile='t', dry_run=False, non_interactive=non_interactive)
        built = []

        def fake_build_plan(*a, **kw):
            built.append(kw)
            raise SystemExit('stop before executing anything')

        with mock.patch.object(smd, '_detect_local_sdr', lambda: False), \
             mock.patch('sigmond.catalog.load_profiles', lambda: {'t': prof}), \
             mock.patch('sigmond.bringup.build_plan', fake_build_plan), \
             mock.patch.object(smd, '_need_root', lambda *_: False), \
             mock.patch('sys.stdin.isatty', return_value=tty), \
             mock.patch('builtins.input', return_value=answer) as asked:
            try:
                rc = smd.cmd_bringup(args)
            except SystemExit:
                rc = None
        return rc, built, asked

    def test_abort_is_offered_and_honoured(self):
        rc, built, asked = self._run('a')
        self.assertEqual(rc, 1)
        self.assertEqual(built, [], 'abort must install nothing')
        asked.assert_called_once()

    def test_the_default_is_to_carry_on(self):
        rc, built, asked = self._run('')
        self.assertEqual(len(built), 1)
        self.assertTrue(built[0]['sdr_absent'])

    def test_an_unattended_run_is_never_asked(self):
        rc, built, asked = self._run('a', non_interactive=True)
        asked.assert_not_called()
        self.assertTrue(built[0]['sdr_absent'])


class Verdict(unittest.TestCase):

    def test_a_deferred_run_ends_partial_rc3_never_complete(self):
        import io, contextlib
        smd = _load_smd()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = smd._bringup_finish(build_plan(_dasi2(), local_radiod=True,
                                                sdr_absent=True), 'dasi2')
        self.assertEqual(rc, 3)
        self.assertEqual(rc, smd.BRINGUP_PARTIAL_RC)
        self.assertIn('PARTIAL', out.getvalue())
        self.assertNotIn('complete', out.getvalue().replace('completes', ''))

    def test_a_full_run_still_ends_complete_rc0(self):
        smd = _load_smd()
        self.assertEqual(smd._bringup_finish(
            build_plan(_dasi2(), local_radiod=True), 'dasi2'), 0)

    def test_a_failed_step_is_never_complete(self):
        # AC0G-ND v3.67: three steps exited 1 and the run still said complete.
        import io, contextlib
        smd = _load_smd()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = smd._bringup_finish(build_plan(_dasi2(), local_radiod=True), 'dasi2',
                                     ['start psk-recorder@AC0G/ND (exit 1)'])
        self.assertEqual(rc, smd.BRINGUP_INCOMPLETE_RC)
        self.assertNotEqual(rc, 0)
        self.assertIn('INCOMPLETE', out.getvalue())
        self.assertIn('psk-recorder', out.getvalue())
        self.assertNotIn("'dasi2' complete", out.getvalue())

    def test_sdr_recover_is_the_only_may_fail_step(self):
        p = build_plan(_dasi2(), local_radiod=True)
        tolerant = [s.argv[0] for s in p.steps if s.may_fail]
        self.assertEqual(tolerant, ['/usr/local/sbin/sigmond-sdr-recover'])


class FirstrunAwaitingSdr(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / 'personalized').write_text('')
        (self.d / 'profile.toml').write_text('profile = "dasi2"\n')

    def _stub(self, name, rc):
        p = self.d / name
        p.write_text(f'#!/bin/bash\necho {name} >> {self.d}/calls\nexit {rc}\n')
        p.chmod(0o755)
        return p

    def _smd_sequence(self, rcs):
        """An smd stub whose Nth call exits rcs[N] (the last one repeats)."""
        p = self.d / 'smd'
        p.write_text('#!/bin/bash\n'
                     f'echo smd >> {self.d}/calls\n'
                     f'n=$(grep -c smd {self.d}/calls)\n'
                     f'rcs=({" ".join(str(r) for r in rcs)})\n'
                     'i=$(( n - 1 )); [ $i -ge ${#rcs[@]} ] && i=$(( ${#rcs[@]} - 1 ))\n'
                     'exit ${rcs[$i]}\n')
        p.chmod(0o755)
        return p

    def _lsusb(self, rx888: bool):
        """Put an lsusb on PATH that does or does not show an RX888."""
        b = self.d / 'bin'
        b.mkdir(exist_ok=True)
        line = 'Bus 008 Device 003: ID 04b4:00f1 Cypress Semiconductor Corp. RX888mk2' if rx888 \
            else 'Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub'
        (b / 'lsusb').write_text(f'#!/bin/bash\necho "{line}"\n')
        (b / 'lsusb').chmod(0o755)

    def run_script(self, smd_rc, marker=None, smd_rcs=None, rx888=False):
        m = self.d / 'marker'
        if marker is not None:
            m.write_text(marker)
        self._lsusb(rx888)
        env = dict(os.environ,
                   PATH=f"{self.d / 'bin'}:{os.environ.get('PATH', '')}",
                   SIGMOND_FIRSTRUN_SETTLE_S='0',
                   SIGMOND_FIRSTRUN_MARKER=str(m),
                   SIGMOND_FIRSTRUN_SENTINEL=str(self.d / 'personalized'),
                   SIGMOND_FIRSTRUN_PROFILE_FILE=str(self.d / 'profile.toml'),
                   SIGMOND_FIRSTRUN_LOG=str(self.d / 'log'),
                   SIGMOND_FIRSTRUN_SMD=str(self._smd_sequence(smd_rcs) if smd_rcs
                                            else self._stub('smd', smd_rc)),
                   SIGMOND_FIRSTRUN_SITE_TIMING=str(self._stub('site-timing', 0)),
                   SIGMOND_FIRSTRUN_ISOLATION=str(self._stub('isolation', 0)))
        r = subprocess.run(['bash', str(FIRSTRUN)], env=env,
                           capture_output=True, text=True, timeout=120)
        calls = (self.d / 'calls').read_text().split() if (self.d / 'calls').exists() else []
        return r, calls, m.read_text() if m.exists() else ''

    def test_partial_is_recorded_as_awaiting_sdr_never_done(self):
        r, calls, marker = self.run_script(3)
        self.assertIn('result=awaiting-sdr', marker)
        self.assertNotIn('result=ok', marker)
        self.assertIn('attempts=0', marker, 'a missing SDR spends no retry budget')

    def test_awaiting_sdr_runs_again_and_completes(self):
        r, calls, marker = self.run_script(0, marker='attempts=0\nresult=awaiting-sdr\nx\n')
        self.assertIn('smd', calls, 'awaiting-sdr must let bring-up run again')
        self.assertIn('result=ok', marker)

    def test_awaiting_sdr_never_gives_up(self):
        marker = None
        for _ in range(5):
            _, _, marker = self.run_script(3, marker=marker)
        self.assertIn('result=awaiting-sdr', marker)
        self.assertNotIn('gave-up', marker)

    def test_an_sdr_that_arrives_during_the_run_is_not_lost(self):
        # AC0G-ND v3.67, 2026-10-05: the RX888 enumerated 80 s into a run that
        # had already deferred the radio; its arrival event hit a unit that was
        # already active, so the run ended awaiting-sdr with the card present.
        r, calls, marker = self.run_script(None, smd_rcs=[3, 0], rx888=True)
        self.assertEqual(calls.count('smd'), 2, 'card on the bus after a partial -> run again')
        self.assertIn('result=ok', marker)

    def test_no_card_after_a_partial_means_no_second_run(self):
        r, calls, marker = self.run_script(None, smd_rcs=[3, 0], rx888=False)
        self.assertEqual(calls.count('smd'), 1)
        self.assertIn('result=awaiting-sdr', marker)

    def test_the_rerun_is_bounded(self):
        r, calls, marker = self.run_script(None, smd_rcs=[3], rx888=True)
        self.assertEqual(calls.count('smd'), 3)
        self.assertIn('result=awaiting-sdr', marker)

    def test_a_completed_station_exits_without_running(self):
        r, calls, _ = self.run_script(0, marker='attempts=1\nresult=ok\nx\n')
        self.assertNotIn('smd', calls)


class ArrivalTrigger(unittest.TestCase):

    def test_the_unit_no_longer_refuses_to_start_once_a_marker_exists(self):
        unit = (REPO / 'systemd' / 'sigmond-firstrun-bringup.service').read_text()
        active = [l for l in unit.splitlines() if not l.lstrip().startswith('#')]
        self.assertFalse(any('firstrun-bringup-done' in l for l in active),
                         'a marker condition kills result=retry and awaiting-sdr')

    def test_the_rx888_arrival_asks_for_firstrun(self):
        rule = (REPO / 'udev' / '90-sigmond-sdr-arrival.rules').read_text()
        live = [l for l in rule.splitlines() if l and not l.startswith('#')]
        self.assertEqual(len(live), 1)
        for frag in ('ACTION=="add"', 'ATTR{idVendor}=="04b4"',
                     'ATTR{idProduct}=="00f1"',
                     'SYSTEMD_WANTS}+="sigmond-firstrun-bringup.service"'):
            self.assertIn(frag, live[0])

    def test_install_sh_installs_the_rule(self):
        self.assertIn('udev/90-sigmond-sdr-arrival.rules',
                      (REPO / 'install.sh').read_text())


if __name__ == '__main__':
    unittest.main()
