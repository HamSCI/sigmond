"""Tests for sigmond.fleet — the fleet inventory.

The inventory answers "what hosts are there, and how do I reach each
one?"  It is deliberately dumb about credentials: ``reach`` and ``hop``
are opaque ssh destinations, and which key reaches them is the
operator's ssh config (see hamsci-ops docs/fleet-ssh-access.md).

A silently-skipped host is worse than an error — it would be invisible
in every later fan-out — so malformed input fails loudly and names the
thing it could not parse.
"""

import pytest

from sigmond.fleet import DEFAULT_FLEET_PATH, Host, load_fleet


class TestLoadFleet:

    def test_load_fleet_reads_hosts_and_freeze(self, tmp_path):
        p = tmp_path / 'fleet.toml'
        p.write_text(
            '[host.alpha]\n'
            'reach   = "root@alpha.example"\n'
            'profile = "dasi2"\n'
            'role    = "field"\n'
            '\n'
            '[host.beta]\n'
            'reach   = "-J jump.example -p 2222 root@10.0.0.9"\n'
            'profile = "dasi2"\n'
            'role    = "field"\n'
            'frozen  = "capture window through 2026-08-20T00:00Z"\n'
        )
        fleet = load_fleet(str(p))
        assert set(fleet) == {'alpha', 'beta'}
        assert fleet['beta'].frozen.startswith('capture window')
        assert fleet['alpha'].frozen is None

    def test_reach_is_opaque(self, tmp_path):
        """The loader records WHERE, and never parses it into parts.

        A reach string carrying jump, port and user must survive
        verbatim — the ssh client parses it, not us.
        """
        p = tmp_path / 'fleet.toml'
        p.write_text(
            '[host.beta]\n'
            'reach = "-J jump.example -p 2222 root@10.0.0.9"\n'
        )
        fleet = load_fleet(str(p))
        assert fleet['beta'].reach == '-J jump.example -p 2222 root@10.0.0.9'

    def test_hop_is_optional_and_opaque(self, tmp_path):
        """``hop`` carries the inner destination of a nested ssh.

        Some sigmond hosts are guests whose credential lives on their
        hypervisor, not on the devbox — the outer host runs the inner
        ssh.  A host with no hop reaches its target directly.
        """
        p = tmp_path / 'fleet.toml'
        p.write_text(
            '[host.guest]\n'
            'reach = "hypervisor.example"\n'
            'hop   = "sigmond@10.0.0.5"\n'
            '\n'
            '[host.direct]\n'
            'reach = "server.example"\n'
        )
        fleet = load_fleet(str(p))
        assert fleet['guest'].hop == 'sigmond@10.0.0.5'
        assert fleet['direct'].hop is None

    def test_optional_fields_default_rather_than_raise(self, tmp_path):
        """Only ``reach`` is mandatory; the rest describe, not locate."""
        p = tmp_path / 'fleet.toml'
        p.write_text(
            '[host.spare]\n'
            'reach = "spare.example"\n'
        )
        fleet = load_fleet(str(p))
        host = fleet['spare']
        assert host.name == 'spare'
        assert host.profile is None
        assert host.role is None
        assert host.frozen is None


class TestMissingInventory:

    def test_absent_default_inventory_is_an_empty_fleet(self, tmp_path,
                                                        monkeypatch):
        """No inventory is a legitimate state, not a crash.

        Every host that is not the devbox has no fleet inventory, and
        `smd` must keep working there.
        """
        monkeypatch.delenv('SIGMOND_FLEET', raising=False)
        monkeypatch.setattr('sigmond.fleet.DEFAULT_FLEET_PATH',
                            tmp_path / 'absent' / 'fleet.toml')
        assert load_fleet() == {}

    def test_explicit_missing_path_fails_loudly(self, tmp_path):
        """An operator who names a file wants that file.

        Returning an empty fleet here would report "no hosts" for a
        typo'd --inventory, which reads exactly like a healthy fleet
        with nothing in it.
        """
        missing = tmp_path / 'nope.toml'
        with pytest.raises(FileNotFoundError) as exc:
            load_fleet(str(missing))
        assert 'nope.toml' in str(exc.value)


class TestMalformedInventory:

    def test_malformed_toml_names_the_file(self, tmp_path):
        p = tmp_path / 'broken.toml'
        p.write_text('[host.alpha\nreach = "x"\n')
        with pytest.raises(ValueError) as exc:
            load_fleet(str(p))
        assert 'broken.toml' in str(exc.value)

    def test_host_without_reach_names_the_host(self, tmp_path):
        """Skipping it silently would hide the host from every fan-out."""
        p = tmp_path / 'fleet.toml'
        p.write_text(
            '[host.alpha]\n'
            'reach = "root@alpha.example"\n'
            '\n'
            '[host.beta]\n'
            'profile = "dasi2"\n'
        )
        with pytest.raises(ValueError) as exc:
            load_fleet(str(p))
        assert 'beta' in str(exc.value)

    def test_host_block_that_is_not_a_table_names_the_host(self, tmp_path):
        p = tmp_path / 'fleet.toml'
        p.write_text('[host]\nalpha = "root@alpha.example"\n')
        with pytest.raises(ValueError) as exc:
            load_fleet(str(p))
        assert 'alpha' in str(exc.value)

    def test_empty_inventory_file_is_an_empty_fleet(self, tmp_path):
        """A file with no [host.*] blocks is empty, not malformed."""
        p = tmp_path / 'fleet.toml'
        p.write_text('# nothing here yet\n')
        assert load_fleet(str(p)) == {}


class TestPrecedence:

    def test_env_override_beats_the_default_path(self, tmp_path, monkeypatch):
        """$SIGMOND_FLEET lets a non-install host carry an inventory.

        The devbox has no /etc/sigmond at all, so the default path can
        never resolve there.
        """
        env_file = tmp_path / 'from-env.toml'
        env_file.write_text('[host.fromenv]\nreach = "env.example"\n')
        default_file = tmp_path / 'from-etc.toml'
        default_file.write_text('[host.fromdefault]\nreach = "etc.example"\n')

        monkeypatch.setenv('SIGMOND_FLEET', str(env_file))
        monkeypatch.setattr('sigmond.fleet.DEFAULT_FLEET_PATH', default_file)

        assert set(load_fleet()) == {'fromenv'}

    def test_default_path_is_the_etc_operator_file(self):
        """The documented default, matching catalog.toml's /etc layer."""
        assert DEFAULT_FLEET_PATH.as_posix() == '/etc/sigmond/fleet.toml'


class TestShippedExample:

    def test_example_inventory_parses_and_uses_placeholders_only(self):
        """etc/fleet.toml.example ships in a PUBLIC repo.

        It must parse (it is the schema documentation) and must not
        leak a single real hostname, IP, port or username.
        """
        from pathlib import Path
        example = (Path(__file__).resolve().parent.parent
                   / 'etc' / 'fleet.toml.example')
        fleet = load_fleet(str(example))
        assert fleet, 'the example must define at least one host'

        blob = example.read_text()
        for leak in ('192.168.1.', '10.112.0.', 'wd30', 'b4pm', 'dasi002',
                     'ac0g', 'wsprdaemon', 'hamsci.org'):
            assert leak not in blob.lower(), f'real fleet data in example: {leak}'


class TestHost:

    def test_frozen_host_is_still_a_host(self):
        """Freeze prevents changes, not visibility."""
        host = Host(name='alpha', reach='root@alpha.example',
                    frozen='capture window')
        assert host.frozen == 'capture window'
        assert host.reach == 'root@alpha.example'


# ---------------------------------------------------------------------------
# Task 2 — smd fleet status
# ---------------------------------------------------------------------------

from sigmond.fleet import (  # noqa: E402
    READ_ONLY_COMMANDS,
    RunResult,
    fleet_status,
)


VERSION_OK = """image:      v3.30   [image installed on this host]

components (live):
    callhash         2a1b584
    codar-sounder    5481589
    ft8_lib          400e236
    gpsdo-monitor    19e85b0
    hamsci-dsp       1ea760a
    hf-tec           b512b50
    hf-timestd       f85f36e
    hfdl-recorder    458c8fa
    hs-uploader      73e89e1
    igmp-querier     471574e
    ka9q-python      c5dc9ed
    sigmond          517995a
"""

MANIFEST_MATCHING = VERSION_OK
MANIFEST_BEHIND = VERSION_OK.replace('hf-timestd       f85f36e',
                                     'hf-timestd       aaaaaaa')


def runner(script):
    """Build an injected fan-out from {(host, command): RunResult}.

    Mirrors ``venv_skew``'s ``probe`` and ``exec_mismatch``'s
    ``resolve`` — tests never touch a real host.
    """
    calls = []

    def run(host, command):
        calls.append((host.name, command))
        result = script.get((host.name, command))
        if result is None:
            return RunResult(rc=1, out='', err='command not found')
        return result

    run.calls = calls
    return run


def _ok(out=''):
    return RunResult(rc=0, out=out, err='')


class TestFleetStatus:

    def test_host_matching_its_blessed_manifest_reports_no_drift(self):
        fleet = {'alpha': Host(name='alpha', reach='root@alpha.example')}
        run = runner({
            ('alpha', 'true'): _ok(),
            ('alpha', 'smd version'): _ok(VERSION_OK),
            ('alpha', 'cat /etc/sigmond-appliance/manifest.txt'):
                _ok(MANIFEST_MATCHING),
        })
        [status] = fleet_status(fleet, run)
        assert status.reachable is True
        assert status.blessed_source == 'manifest'
        assert status.drift == []

    def test_host_behind_its_manifest_names_the_components(self):
        fleet = {'alpha': Host(name='alpha', reach='root@alpha.example')}
        run = runner({
            ('alpha', 'true'): _ok(),
            ('alpha', 'smd version'): _ok(VERSION_OK),
            ('alpha', 'cat /etc/sigmond-appliance/manifest.txt'):
                _ok(MANIFEST_BEHIND),
        })
        [status] = fleet_status(fleet, run)
        assert status.blessed_source == 'manifest'
        assert [d['component'] for d in status.drift] == ['hf-timestd']
        assert status.drift[0]['live'] == 'f85f36e'

    def test_host_without_a_manifest_falls_back_to_main_and_says_so(self):
        """No fleet host carries a manifest yet.

        Comparing against main is still useful, but must never be
        presented as though it were a blessed pin.
        """
        fleet = {'alpha': Host(name='alpha', reach='root@alpha.example')}
        run = runner({
            ('alpha', 'true'): _ok(),
            ('alpha', 'smd version'): _ok(VERSION_OK),
        })
        [status] = fleet_status(fleet, run, commits_behind=lambda sha: 10)
        assert status.blessed_source == 'main'
        assert status.behind == 10
        assert status.drift == []

    def test_unreachable_host_is_reported_distinctly_from_a_healthy_one(self):
        fleet = {
            'alpha': Host(name='alpha', reach='root@alpha.example'),
            'gone': Host(name='gone', reach='root@gone.example'),
        }
        run = runner({
            ('alpha', 'true'): _ok(),
            ('alpha', 'smd version'): _ok(VERSION_OK),
            ('gone', 'true'): RunResult(rc=255, out='',
                                        err='Permission denied (publickey).'),
        })
        by_name = {s.host.name: s for s in fleet_status(fleet, run)}
        assert by_name['alpha'].reachable is True
        assert by_name['gone'].reachable is False
        assert 'Permission denied' in by_name['gone'].error

    def test_one_unreachable_host_does_not_abort_the_run(self):
        """doctor.py:73's principle, and Stage 3 Task 4's defect."""
        fleet = {
            'gone': Host(name='gone', reach='root@gone.example'),
            'alpha': Host(name='alpha', reach='root@alpha.example'),
        }
        run = runner({
            ('gone', 'true'): RunResult(rc=255, out='', err='no route to host'),
            ('alpha', 'true'): _ok(),
            ('alpha', 'smd version'): _ok(VERSION_OK),
        })
        statuses = fleet_status(fleet, run)
        assert len(statuses) == 2
        assert any(s.reachable and s.components for s in statuses)

    def test_a_raising_runner_is_contained_to_its_own_host(self):
        """A fan-out that throws must not discard the other hosts."""
        fleet = {
            'boom': Host(name='boom', reach='root@boom.example'),
            'alpha': Host(name='alpha', reach='root@alpha.example'),
        }
        script = {
            ('alpha', 'true'): _ok(),
            ('alpha', 'smd version'): _ok(VERSION_OK),
        }

        def run(host, command):
            if host.name == 'boom':
                raise OSError('ssh binary missing')
            return script.get((host.name, command)) or RunResult(1, '', 'nope')

        by_name = {s.host.name: s for s in fleet_status(fleet, run)}
        assert by_name['boom'].reachable is False
        assert 'ssh binary missing' in by_name['boom'].error
        assert by_name['alpha'].components['sigmond'] == '517995a'

    def test_frozen_host_is_still_reported(self):
        """Freeze prevents changes, not visibility."""
        fleet = {'cold': Host(name='cold', reach='root@cold.example',
                              frozen='capture window through 2026-08-20')}
        run = runner({
            ('cold', 'true'): _ok(),
            ('cold', 'smd version'): _ok(VERSION_OK),
        })
        [status] = fleet_status(fleet, run)
        assert status.reachable is True
        assert status.components['sigmond'] == '517995a'
        assert status.host.frozen.startswith('capture window')

    def test_reachable_host_without_sigmond_is_not_an_error(self):
        """The jump host runs no sigmond. That is a fact, not a fault."""
        fleet = {'srv': Host(name='srv', reach='op@srv.example', role='server')}
        run = runner({('srv', 'true'): _ok()})
        [status] = fleet_status(fleet, run)
        assert status.reachable is True
        assert status.has_sigmond is False
        assert status.error is None
        assert status.blessed_source == 'none'

    def test_unparseable_version_output_is_reported_not_silently_empty(self):
        fleet = {'odd': Host(name='odd', reach='root@odd.example')}
        run = runner({
            ('odd', 'true'): _ok(),
            ('odd', 'smd version'): _ok('total gibberish\n'),
        })
        [status] = fleet_status(fleet, run)
        assert status.has_sigmond is True
        assert status.components == {}
        assert status.error and 'components' in status.error.lower()


class TestStatusIsReadOnlyByConstruction:

    def test_every_command_it_can_issue_is_in_the_read_only_set(self):
        """`status` must be INCAPABLE of writing, not merely not writing.

        The guard is the whitelist itself: if a future edit adds a
        mutating command to the fan-out, this test fails.
        """
        for command in READ_ONLY_COMMANDS:
            assert not any(tok in command for tok in
                           ('--apply', '--fix', 'install', 'rm ', 'systemctl',
                            'update', '>', 'tee'))

    def test_status_issues_only_whitelisted_commands(self):
        fleet = {
            'alpha': Host(name='alpha', reach='root@alpha.example'),
            'gone': Host(name='gone', reach='root@gone.example'),
        }
        run = runner({
            ('alpha', 'true'): _ok(),
            ('alpha', 'smd version'): _ok(VERSION_OK),
            ('alpha', 'cat /etc/sigmond-appliance/manifest.txt'):
                _ok(MANIFEST_MATCHING),
            ('gone', 'true'): RunResult(rc=255, out='', err='denied'),
        })
        fleet_status(fleet, run)
        assert run.calls, 'the fan-out was never exercised'
        for _host, command in run.calls:
            assert command in READ_ONLY_COMMANDS, f'not read-only: {command}'


from sigmond.fleet import HostStatus, format_status, ssh_runner  # noqa: E402


class TestSshRunner:

    def _argv_for(self, host, command='smd version'):
        seen = {}

        def exec_(argv, timeout):
            seen['argv'] = argv
            seen['timeout'] = timeout
            return RunResult(rc=0, out='', err='')

        ssh_runner(exec_=exec_)(host, command)
        return seen

    def test_direct_host_runs_the_command_over_one_ssh(self):
        host = Host(name='srv', reach='op@srv.example')
        argv = self._argv_for(host)['argv']
        assert argv[0] == 'ssh'
        assert 'op@srv.example' in argv
        assert argv[-1] == 'smd version'

    def test_reach_flags_are_split_into_argv_not_passed_as_one_word(self):
        """`-J jump -p 2222 host` is four arguments, not one."""
        host = Host(name='beta', reach='-J jump.example -p 2222 root@10.0.0.9')
        argv = self._argv_for(host)['argv']
        for token in ('-J', 'jump.example', '-p', '2222', 'root@10.0.0.9'):
            assert token in argv

    def test_hop_becomes_a_nested_ssh_run_by_the_outer_host(self):
        """The inner hop's credential lives on the outer host.

        So the outer host must run the inner ssh itself. Expressed as
        -J, ssh would offer OUR key on the final hop — the one key that
        is not authorized there.
        """
        host = Host(name='guest', reach='root@hv.example',
                    hop='sigmond@10.0.0.5')
        argv = self._argv_for(host)['argv']
        assert 'root@hv.example' in argv
        assert '-J' not in argv, 'hop must not degrade to ProxyJump'
        remote = argv[-1]
        assert remote.startswith('ssh ')
        assert 'sigmond@10.0.0.5' in remote
        assert 'smd version' in remote

    def test_batchmode_is_forced_so_an_unreachable_host_never_prompts(self):
        """A password prompt in a fan-out hangs the whole run."""
        host = Host(name='srv', reach='op@srv.example')
        argv = self._argv_for(host)['argv']
        assert 'BatchMode=yes' in ' '.join(argv)
        assert 'ConnectTimeout=' in ' '.join(argv)

    def test_nested_hop_also_forces_batchmode_on_the_inner_ssh(self):
        host = Host(name='guest', reach='root@hv.example',
                    hop='sigmond@10.0.0.5')
        remote = self._argv_for(host)['argv'][-1]
        assert 'BatchMode=yes' in remote

    def test_a_timeout_is_always_applied(self):
        """An unresponsive host must not stall the fan-out forever."""
        host = Host(name='srv', reach='op@srv.example')
        assert self._argv_for(host)['timeout'] > 0


class TestFormatStatus:

    def test_the_four_states_are_visually_distinguishable(self):
        statuses = [
            HostStatus(host=Host(name='ok', reach='r'), reachable=True,
                       has_sigmond=True, components={'sigmond': '517995a'},
                       blessed_source='manifest', drift=[]),
            HostStatus(host=Host(name='behind', reach='r'), reachable=True,
                       has_sigmond=True, components={'sigmond': '517995a'},
                       blessed_source='main', behind=10),
            HostStatus(host=Host(name='gone', reach='r'), reachable=False,
                       error='Permission denied (publickey).'),
            HostStatus(host=Host(name='srv', reach='r'), reachable=True,
                       has_sigmond=False),
        ]
        report = format_status(statuses)
        assert 'unreachable' in report.lower()
        assert 'no sigmond' in report.lower()
        assert '10' in report

    def test_the_main_fallback_is_never_presented_as_a_blessed_pin(self):
        """"current with main" and "matches what we blessed" differ."""
        statuses = [
            HostStatus(host=Host(name='b4', reach='r'), reachable=True,
                       has_sigmond=True, components={'sigmond': '517995a'},
                       blessed_source='main', behind=10),
        ]
        report = format_status(statuses).lower()
        assert 'no manifest' in report or 'unblessed' in report
        assert 'main' in report

    def test_a_frozen_host_is_shown_with_its_reason(self):
        statuses = [
            HostStatus(host=Host(name='cold', reach='r',
                                 frozen='capture window through 2026-08-20'),
                       reachable=True, has_sigmond=True,
                       components={'sigmond': '517995a'},
                       blessed_source='main', behind=0),
        ]
        report = format_status(statuses)
        assert 'frozen' in report.lower()
        assert 'capture window' in report

    def test_an_empty_fleet_says_so_rather_than_printing_nothing(self):
        """Silence reads as success. It is not."""
        report = format_status([])
        assert report.strip()
        assert 'no hosts' in report.lower()


from sigmond.fleet import commits_behind_main  # noqa: E402


class TestCommitsBehindMain:

    def test_counts_commits_between_the_host_sha_and_main(self):
        calls = []

        def git(argv):
            calls.append(argv)
            return RunResult(rc=0, out='10\n', err='')

        assert commits_behind_main('/repo', git=git)('517995a') == 10
        assert any('rev-list' in a for a in calls[-1])

    def test_an_unknown_sha_is_unknown_not_zero(self):
        """A SHA the local checkout has never seen must not read as
        'current'. Zero here would report a stale host as up to date."""
        def git(argv):
            return RunResult(rc=128, out='',
                             err="fatal: bad revision 'deadbee..origin/main'")

        assert commits_behind_main('/repo', git=git)('deadbee') is None

    def test_unparseable_output_is_unknown_not_zero(self):
        def git(argv):
            return RunResult(rc=0, out='not a number\n', err='')

        assert commits_behind_main('/repo', git=git)('517995a') is None


# ---------------------------------------------------------------------------
# Task 3 — smd fleet doctor
# ---------------------------------------------------------------------------

from sigmond.fleet import fleet_doctor, format_doctor  # noqa: E402


DOCTOR_FINDINGS = """hf-timestd:
    venv-skew: ka9q-python from a private copy, not the shared checkout
"""


class TestFleetDoctor:

    def test_a_host_with_findings_keeps_its_report(self):
        fleet = {'sick': Host(name='sick', reach='root@sick.example')}
        run = runner({
            ('sick', 'true'): _ok(),
            ('sick', 'smd doctor'): RunResult(rc=1, out=DOCTOR_FINDINGS, err=''),
        })
        [result] = fleet_doctor(fleet, run)
        assert result.reachable is True
        assert result.clean is False
        assert 'venv-skew' in result.report

    def test_a_clean_host_is_distinguishable_from_one_with_findings(self):
        fleet = {
            'sick': Host(name='sick', reach='root@sick.example'),
            'well': Host(name='well', reach='root@well.example'),
        }
        run = runner({
            ('sick', 'true'): _ok(),
            ('sick', 'smd doctor'): RunResult(rc=1, out=DOCTOR_FINDINGS),
            ('well', 'true'): _ok(),
            ('well', 'smd doctor'): _ok('deploy trees clean'),
        })
        by_name = {r.host.name: r for r in fleet_doctor(fleet, run)}
        assert by_name['well'].clean is True
        assert by_name['sick'].clean is False

    def test_an_unreachable_host_is_neither_clean_nor_dirty(self):
        """Reporting it as clean would hide it. As dirty would libel it."""
        fleet = {'gone': Host(name='gone', reach='root@gone.example')}
        run = runner({('gone', 'true'): RunResult(rc=255, out='',
                                                  err='denied')})
        [result] = fleet_doctor(fleet, run)
        assert result.reachable is False
        assert result.clean is None
        assert 'denied' in result.error

    def test_one_unreachable_host_does_not_abort_the_run(self):
        fleet = {
            'gone': Host(name='gone', reach='root@gone.example'),
            'well': Host(name='well', reach='root@well.example'),
        }
        run = runner({
            ('gone', 'true'): RunResult(rc=255, out='', err='denied'),
            ('well', 'true'): _ok(),
            ('well', 'smd doctor'): _ok('deploy trees clean'),
        })
        assert len(fleet_doctor(fleet, run)) == 2

    def test_a_frozen_host_is_still_examined(self):
        """doctor reports; it never repairs. Freeze has nothing to stop."""
        fleet = {'cold': Host(name='cold', reach='root@cold.example',
                              frozen='capture window')}
        run = runner({
            ('cold', 'true'): _ok(),
            ('cold', 'smd doctor'): RunResult(rc=1, out=DOCTOR_FINDINGS),
        })
        [result] = fleet_doctor(fleet, run)
        assert result.clean is False

    def test_a_host_without_sigmond_is_reported_not_counted_as_clean(self):
        fleet = {'srv': Host(name='srv', reach='op@srv.example', role='server')}
        run = runner({
            ('srv', 'true'): _ok(),
            ('srv', 'smd doctor'): RunResult(rc=127, out='',
                                             err='smd: command not found'),
        })
        [result] = fleet_doctor(fleet, run)
        assert result.has_sigmond is False
        assert result.clean is None

    def test_doctor_never_offers_fix(self):
        """`smd fleet doctor` reports; it does not repair."""
        fleet = {'sick': Host(name='sick', reach='root@sick.example')}
        run = runner({
            ('sick', 'true'): _ok(),
            ('sick', 'smd doctor'): RunResult(rc=1, out=DOCTOR_FINDINGS),
        })
        fleet_doctor(fleet, run)
        for _host, command in run.calls:
            assert '--fix' not in command
            assert command in READ_ONLY_COMMANDS


class TestFormatDoctor:

    def _results(self):
        fleet = {
            'sick': Host(name='sick', reach='r'),
            'well': Host(name='well', reach='r'),
            'gone': Host(name='gone', reach='r'),
        }
        run = runner({
            ('sick', 'true'): _ok(),
            ('sick', 'smd doctor'): RunResult(rc=1, out=DOCTOR_FINDINGS),
            ('well', 'true'): _ok(),
            ('well', 'smd doctor'): _ok('deploy trees clean'),
            ('gone', 'true'): RunResult(rc=255, out='', err='denied'),
        })
        return fleet_doctor(fleet, run)

    def test_all_three_outcomes_appear_in_the_summary(self):
        """Aggregate without hiding."""
        report = format_doctor(self._results()).lower()
        assert 'sick' in report and 'well' in report and 'gone' in report
        assert 'unreachable' in report
        assert 'clean' in report

    def test_findings_are_shown_not_merely_counted(self):
        assert 'venv-skew' in format_doctor(self._results())

    def test_an_empty_fleet_says_so(self):
        assert 'no hosts' in format_doctor([]).lower()


# ---------------------------------------------------------------------------
# Task 4 — smd fleet update
# ---------------------------------------------------------------------------

from sigmond.fleet import (  # noqa: E402
    UPDATE_COMMANDS,
    AmbiguousCanary,
    NoCanary,
    choose_canary,
    fleet_update,
    format_update,
)


CURRENT = 'host is current — nothing to do'
HAS_PLAN = '(dry run — re-run with --apply)\n\n  [pull] sigmond'


def _fleet(*hosts):
    return {h.name: h for h in hosts}


class TestChooseCanary:

    def test_the_host_marked_canary_is_chosen(self):
        fleet = _fleet(
            Host(name='remote', reach='r'),
            Host(name='near', reach='r', canary=True),
        )
        assert choose_canary(fleet).name == 'near'

    def test_no_marked_canary_refuses_rather_than_guessing(self):
        """Inventory order is an accident, not a decision.

        Picking the first host would make the blast radius of a bad
        update depend on alphabetical luck.
        """
        fleet = _fleet(Host(name='a', reach='r'), Host(name='b', reach='r'))
        with pytest.raises(NoCanary):
            choose_canary(fleet)

    def test_two_marked_canaries_refuse_rather_than_picking_one(self):
        fleet = _fleet(Host(name='a', reach='r', canary=True),
                       Host(name='b', reach='r', canary=True))
        with pytest.raises(AmbiguousCanary):
            choose_canary(fleet)

    def test_a_single_host_fleet_needs_no_mark(self):
        """With one host there is no wave to protect."""
        fleet = _fleet(Host(name='only', reach='r'))
        assert choose_canary(fleet).name == 'only'

    def test_a_frozen_host_is_never_the_canary(self):
        """The canary is the host that goes first. A frozen host goes
        nowhere, so it cannot lead."""
        fleet = _fleet(Host(name='cold', reach='r', canary=True,
                            frozen='capture window'),
                       Host(name='warm', reach='r'))
        with pytest.raises(NoCanary):
            choose_canary(fleet)


class TestFleetUpdateDryRun:

    def test_dry_run_is_the_default_and_never_applies(self):
        fleet = _fleet(Host(name='a', reach='r', canary=True))
        run = runner({('a', 'true'): _ok(), ('a', 'smd update'): _ok(HAS_PLAN)})
        results = fleet_update(fleet, run)
        assert all(not r.applied for r in results)
        for _host, command in run.calls:
            assert '--apply' not in command
            assert command in UPDATE_COMMANDS

    def test_a_current_host_produces_an_empty_plan(self):
        fleet = _fleet(Host(name='a', reach='r', canary=True))
        run = runner({('a', 'true'): _ok(), ('a', 'smd update'): _ok(CURRENT)})
        [result] = fleet_update(fleet, run)
        assert result.empty_plan is True

    def test_dry_run_plans_every_host_not_just_the_canary(self):
        """A dry run changes nothing, so there is no reason to stage it."""
        fleet = _fleet(Host(name='a', reach='r', canary=True),
                       Host(name='b', reach='r'))
        run = runner({
            ('a', 'true'): _ok(), ('a', 'smd update'): _ok(HAS_PLAN),
            ('b', 'true'): _ok(), ('b', 'smd update'): _ok(HAS_PLAN),
        })
        assert len(fleet_update(fleet, run)) == 2


class TestFrozenHostsAreSkippedLoudly:

    def test_a_frozen_host_is_never_contacted(self):
        fleet = _fleet(Host(name='cold', reach='r',
                            frozen='capture window through 2026-08-20'),
                       Host(name='warm', reach='r', canary=True))
        run = runner({
            ('warm', 'true'): _ok(), ('warm', 'smd update'): _ok(CURRENT),
        })
        fleet_update(fleet, run)
        assert all(host != 'cold' for host, _cmd in run.calls)

    def test_the_skip_appears_in_the_output_with_its_reason(self):
        """Never silent. A silent skip is indistinguishable from a host
        that was updated."""
        fleet = _fleet(Host(name='cold', reach='r',
                            frozen='capture window through 2026-08-20'),
                       Host(name='warm', reach='r', canary=True))
        run = runner({
            ('warm', 'true'): _ok(), ('warm', 'smd update'): _ok(CURRENT),
        })
        report = format_update(fleet_update(fleet, run))
        assert 'cold' in report
        assert 'SKIPPED' in report.upper()
        assert 'capture window' in report


def updating_runner(names):
    """A fan-out that models a host actually being updated.

    `smd update` reports a plan until `--apply` runs on that host, and
    an empty plan afterwards — which is the whole basis of the
    post-check. A fixed script cannot express that, and a host scripted
    as already-current never applies at all, so ordering assertions
    against one prove nothing.
    """
    calls = []
    updated = set()

    def run(host, command):
        calls.append((host.name, command))
        if host.name not in names:
            return RunResult(rc=1, out='', err='unexpected host')
        # This fake host grants the scoped sudoers rule, so the elevated
        # form is answered rather than refused.
        bare = command[len('sudo -n '):] if command.startswith('sudo -n ') \
            else command
        if bare == 'true':
            return _ok()
        if bare == 'smd update --apply':
            updated.add(host.name)
            return _ok('done')
        if bare == 'smd update':
            return _ok(CURRENT if host.name in updated else HAS_PLAN)
        if bare == 'smd version':
            return _ok(VERSION_OK)
        return RunResult(rc=1, out='', err='unexpected command')

    run.calls = calls
    return run


class TestCanaryThenWave:

    def test_the_canary_is_verified_before_any_other_host_is_touched(self):
        fleet = _fleet(Host(name='wave1', reach='r'),
                       Host(name='canary', reach='r', canary=True),
                       Host(name='wave2', reach='r'))
        run = updating_runner({'canary', 'wave1', 'wave2'})
        fleet_update(fleet, run, apply=True)

        order = [(h, c) for h, c in run.calls if c != 'true']
        assert ('canary', 'smd update --apply') in order
        canary_verified = max(i for i, (h, c) in enumerate(order)
                              if h == 'canary')
        first_wave_touch = min(i for i, (h, c) in enumerate(order)
                               if h != 'canary')
        assert canary_verified < first_wave_touch

    def test_the_canary_is_verified_by_re_planning_not_by_assumption(self):
        """`smd update` is idempotent and yields an empty plan on a
        current host — so re-planning IS the post-check."""
        fleet = _fleet(Host(name='canary', reach='r', canary=True))
        run = updating_runner({'canary'})
        [result] = fleet_update(fleet, run, apply=True)
        assert result.applied is True
        assert result.verified is True
        # The last thing it did was re-plan (elevated, so the fetch can
        # reach repos owned by other users).
        assert run.calls[-1][1].endswith('smd update')

    def test_every_wave_host_is_updated_once_the_canary_holds(self):
        fleet = _fleet(Host(name='canary', reach='r', canary=True),
                       Host(name='w1', reach='r'),
                       Host(name='w2', reach='r'))
        run = updating_runner({'canary', 'w1', 'w2'})
        results = fleet_update(fleet, run, apply=True)
        assert all(r.verified is True for r in results)
        assert all(r.applied for r in results)


class TestStopOnFirstFailure:

    def test_a_canary_that_fails_its_post_check_stops_the_wave(self):
        fleet = _fleet(Host(name='canary', reach='r', canary=True),
                       Host(name='wave', reach='r'))
        run = runner({
            ('canary', 'true'): _ok(),
            ('canary', 'smd update'): _ok(HAS_PLAN),   # still has a plan
            ('canary', 'smd update --apply'): _ok('done'),
            ('wave', 'true'): _ok(),
            ('wave', 'smd update'): _ok(CURRENT),
        })
        results = fleet_update(fleet, run, apply=True)
        by_name = {r.host.name: r for r in results}
        assert by_name['canary'].verified is False
        assert by_name['wave'].halted is True
        assert all(host != 'wave' for host, _c in run.calls)

    def test_a_failing_apply_stops_the_run(self):
        fleet = _fleet(Host(name='canary', reach='r', canary=True),
                       Host(name='wave', reach='r'))
        run = runner({
            ('canary', 'true'): _ok(),
            ('canary', 'smd update'): _ok(HAS_PLAN),
            ('canary', 'smd update --apply'): RunResult(rc=1, out='',
                                                        err='install failed'),
            ('wave', 'true'): _ok(),
        })
        by_name = {r.host.name: r for r in fleet_update(fleet, run, apply=True)}
        assert by_name['canary'].error
        assert by_name['wave'].halted is True

    def test_a_wave_host_failing_halts_the_hosts_behind_it(self):
        fleet = _fleet(Host(name='canary', reach='r', canary=True),
                       Host(name='w1', reach='r'),
                       Host(name='w2', reach='r'))
        run = runner({
            ('canary', 'true'): _ok(),
            ('canary', 'smd update'): _ok(CURRENT),
            ('canary', 'smd update --apply'): _ok('done'),
            ('w1', 'true'): _ok(),
            ('w1', 'smd update'): _ok(HAS_PLAN),      # never goes current
            ('w1', 'smd update --apply'): _ok('done'),
            ('w2', 'true'): _ok(),
            ('w2', 'smd update'): _ok(CURRENT),
        })
        by_name = {r.host.name: r for r in fleet_update(fleet, run, apply=True)}
        assert by_name['w1'].verified is False
        assert by_name['w2'].halted is True

    def test_an_unreachable_canary_stops_the_run(self):
        fleet = _fleet(Host(name='canary', reach='r', canary=True),
                       Host(name='wave', reach='r'))
        run = runner({
            ('canary', 'true'): RunResult(rc=255, out='', err='denied'),
            ('wave', 'true'): _ok(),
        })
        by_name = {r.host.name: r for r in fleet_update(fleet, run, apply=True)}
        assert by_name['canary'].reachable is False
        assert by_name['wave'].halted is True


class TestUpdateVocabulary:

    def test_update_commands_carry_no_destructive_verb_beyond_apply(self):
        for command in UPDATE_COMMANDS:
            assert 'rm ' not in command
            assert '--fix' not in command
            assert 'systemctl' not in command


class TestFormatUpdate:

    def test_a_dry_run_says_it_changed_nothing(self):
        fleet = _fleet(Host(name='a', reach='r', canary=True))
        run = runner({('a', 'true'): _ok(), ('a', 'smd update'): _ok(HAS_PLAN)})
        report = format_update(fleet_update(fleet, run)).lower()
        assert 'dry run' in report
        assert '--apply' in report

    def test_a_halted_host_is_reported_as_halted_not_as_clean(self):
        fleet = _fleet(Host(name='canary', reach='r', canary=True),
                       Host(name='wave', reach='r'))
        run = runner({
            ('canary', 'true'): RunResult(rc=255, out='', err='denied'),
            ('wave', 'true'): _ok(),
        })
        report = format_update(fleet_update(fleet, run, apply=True))
        assert 'HALTED' in report.upper()
        assert 'wave' in report


class TestPostCheckCannotPassOnAHostThatNeverMoved:
    """`smd update` computes HEAD..@{u} WITHOUT fetching.

    A host whose origin/main ref is stale therefore reports "nothing to
    do" while genuinely behind — observed live on B4 2026-08-17, whose
    last fetch was two days old. An empty plan is consequently NOT
    evidence that a host reached the intended state, so the post-check
    also confirms the SHA actually arrived.
    """

    def test_an_empty_plan_alone_does_not_verify_a_stale_host(self):
        fleet = _fleet(Host(name='stale', reach='r', canary=True))
        run = runner({
            ('stale', 'true'): _ok(),
            # Reports current because its upstream ref is two days old.
            ('stale', 'smd update'): _ok(CURRENT),
            ('stale', 'smd version'): _ok(VERSION_OK),   # sigmond 517995a
        })
        [result] = fleet_update(fleet, run, apply=True,
                                commits_behind=lambda sha: 10)
        assert result.verified is False
        assert 'behind' in (result.error or '').lower()

    def test_a_host_that_really_arrived_verifies(self):
        fleet = _fleet(Host(name='good', reach='r', canary=True))
        run = runner({
            ('good', 'true'): _ok(),
            ('good', 'smd update'): _ok(CURRENT),
            ('good', 'smd version'): _ok(VERSION_OK),
        })
        [result] = fleet_update(fleet, run, apply=True,
                                commits_behind=lambda sha: 0)
        assert result.verified is True

    def test_an_unknown_position_is_not_treated_as_arrived(self):
        """None means "we could not tell". Failing closed halts the
        wave; failing open rolls a bad change across the fleet."""
        fleet = _fleet(Host(name='murky', reach='r', canary=True))
        run = runner({
            ('murky', 'true'): _ok(),
            ('murky', 'smd update'): _ok(CURRENT),
            ('murky', 'smd version'): _ok(VERSION_OK),
        })
        [result] = fleet_update(fleet, run, apply=True,
                                commits_behind=lambda sha: None)
        assert result.verified is False

    def test_without_a_position_check_the_dry_run_still_flags_the_gap(self):
        """Even in dry-run, "current" must not be reported bare when the
        host is behind — that is the misreading that started this."""
        fleet = _fleet(Host(name='stale', reach='r'))
        run = runner({
            ('stale', 'true'): _ok(),
            ('stale', 'smd update'): _ok(CURRENT),
            ('stale', 'smd version'): _ok(VERSION_OK),
        })
        [result] = fleet_update(fleet, run, commits_behind=lambda sha: 10)
        assert result.empty_plan is True
        assert result.behind == 10
        report = format_update([result])
        assert 'stale ref' in report.lower() or 'behind' in report.lower()


# ---------------------------------------------------------------------------
# A held component is not a failed host; and remote output is not a
# terminal, so it must not arrive wearing colour codes.
# ---------------------------------------------------------------------------

from sigmond.fleet import HELD_EXIT, strip_ansi  # noqa: E402


HELD_OUT = ('  x  wspr-recorder: HELD — 1 modified file(s) (uv.lock) — diff '
            'against origin/main before discarding\n'
            'nothing to do — 1 component(s) HELD (wspr-recorder); '
            'the rest of the host is current\n')


class TestHeldIsNotFailed:

    def test_a_held_component_is_reported_as_held_not_failed(self):
        fleet = _fleet(Host(name='churn', reach='r'))
        run = runner({
            ('churn', 'true'): _ok(),
            ('churn', 'smd update'): RunResult(rc=HELD_EXIT, out=HELD_OUT),
        })
        [result] = fleet_update(fleet, run)
        assert result.error is None, 'a deliberate hold is not an error'
        assert result.held == ['wspr-recorder']

    def test_the_report_says_held_and_names_the_component(self):
        fleet = _fleet(Host(name='churn', reach='r'))
        run = runner({
            ('churn', 'true'): _ok(),
            ('churn', 'smd update'): RunResult(rc=HELD_EXIT, out=HELD_OUT),
        })
        report = format_update(fleet_update(fleet, run))
        assert 'HELD' in report
        assert 'wspr-recorder' in report
        assert 'FAILED' not in report

    def test_a_held_host_can_still_verify_and_not_block_the_wave(self):
        """Routine uv.lock churn must not halt the fleet forever.

        A hold is a steady state the operator has accepted; the host is
        current in every respect the update could act on.
        """
        fleet = _fleet(Host(name='churn', reach='r', canary=True),
                       Host(name='after', reach='r'))
        run = runner({
            ('churn', 'true'): _ok(),
            ('churn', 'smd update'): RunResult(rc=HELD_EXIT, out=HELD_OUT),
            ('churn', 'smd version'): _ok(VERSION_OK),
            ('after', 'true'): _ok(),
            ('after', 'smd update'): _ok(CURRENT),
            ('after', 'smd version'): _ok(VERSION_OK),
        })
        by_name = {r.host.name: r
                   for r in fleet_update(fleet, run, apply=True,
                                         commits_behind=lambda sha: 0)}
        assert by_name['churn'].verified is True
        assert by_name['after'].halted is False

    def test_a_genuine_failure_is_still_a_failure(self):
        fleet = _fleet(Host(name='broken', reach='r'))
        run = runner({
            ('broken', 'true'): _ok(),
            ('broken', 'smd update'): RunResult(rc=1, out='', err='boom'),
        })
        [result] = fleet_update(fleet, run)
        assert result.error
        assert 'FAILED' in format_update([result])


class TestStripAnsi:

    def test_colour_codes_are_removed(self):
        assert strip_ansi('\x1b[31m✗\x1b[0m  held') == '✗  held'

    def test_plain_text_is_untouched(self):
        assert strip_ansi('nothing to do') == 'nothing to do'

    def test_none_and_empty_survive(self):
        assert strip_ansi('') == ''
        assert strip_ansi(None) is None

    def test_the_fan_out_strips_what_it_receives(self):
        """smd colours its output; a pipe is not a terminal, but the
        remote end cannot tell. Strip at the boundary rather than
        letting escapes into every report."""
        def exec_(argv, timeout):
            return RunResult(rc=0, out='\x1b[32m✓\x1b[0m ok',
                             err='\x1b[31mbad\x1b[0m')

        result = ssh_runner(exec_=exec_)(Host(name='h', reach='r'), 'smd version')
        assert result.out == '✓ ok'
        assert result.err == 'bad'


# ---------------------------------------------------------------------------
# Scoped elevation
# ---------------------------------------------------------------------------
#
# Two checks need root and degrade honestly without it:
#   * exec-mismatch reads /proc/<pid>/exe, unreadable for another user's
#     process — every service reported `running=(unreadable)`.
#   * _git_fetch_as_owner can only drop privileges when it HAS them, so
#     as `sigmond` it cannot fetch a timestd-owned repo and the ref
#     stays stale (hf-timestd sat 19 commits behind, invisible).
#
# The elevation is per-verb, never blanket: sudoers matches arguments
# exactly, so `smd doctor` permits only the bare form. `--fix` and
# `--apply` still require a password.

from sigmond.fleet import run_elevated, sudo_refused  # noqa: E402


class TestSudoRefusalDetection:

    def test_a_password_prompt_is_a_refusal(self):
        assert sudo_refused(RunResult(rc=1, err='sudo: a password is required'))

    def test_not_in_sudoers_is_a_refusal(self):
        assert sudo_refused(RunResult(
            rc=1, err='sudo: user sigmond is not allowed to execute'))

    def test_a_successful_run_is_not_a_refusal(self):
        assert not sudo_refused(RunResult(rc=0, out='deploy trees clean'))

    def test_the_commands_own_failure_is_not_a_refusal(self):
        """`smd doctor` exits 1 when it FINDS something. Treating that as
        a sudo refusal would silently rerun it unelevated and report less."""
        assert not sudo_refused(RunResult(rc=1, out='hf-timestd:\n  venv-skew'))


class TestRunElevated:

    def test_it_prefers_the_elevated_form(self):
        seen = []

        def run(host, command):
            seen.append(command)
            return _ok('clean')

        result, elevated = run_elevated(run, Host(name='h', reach='r'),
                                        'smd doctor')
        assert seen == ['sudo -n smd doctor']
        assert elevated is True
        assert result.out == 'clean'

    def test_it_falls_back_when_sudo_refuses(self):
        """A host without the rule must still be reported, not dropped."""
        seen = []

        def run(host, command):
            seen.append(command)
            if command.startswith('sudo -n'):
                return RunResult(rc=1, err='sudo: a password is required')
            return _ok('clean')

        result, elevated = run_elevated(run, Host(name='h', reach='r'),
                                        'smd doctor')
        assert seen == ['sudo -n smd doctor', 'smd doctor']
        assert elevated is False
        assert result.out == 'clean'

    def test_it_does_not_retry_when_the_command_itself_failed(self):
        """Running a findings-producing doctor twice would double the work
        and could report different results from the two runs."""
        seen = []

        def run(host, command):
            seen.append(command)
            return RunResult(rc=1, out='findings')

        _result, elevated = run_elevated(run, Host(name='h', reach='r'),
                                         'smd doctor')
        assert seen == ['sudo -n smd doctor']
        assert elevated is True


class TestElevationIsReported:

    def test_doctor_records_whether_it_ran_elevated(self):
        fleet = _fleet(Host(name='h', reach='r'))
        run = runner({
            ('h', 'true'): _ok(),
            ('h', 'sudo -n smd doctor'): _ok('deploy trees clean'),
        })
        [result] = fleet_doctor(fleet, run)
        assert result.elevated is True
        assert result.clean is True

    def test_an_unelevated_host_says_so_in_the_report(self):
        """Coverage differs between the two, so a reader must be able to
        tell which ran — a short report must not read as a clean one."""
        fleet = _fleet(Host(name='h', reach='r'))
        run = runner({
            ('h', 'true'): _ok(),
            ('h', 'sudo -n smd doctor'): RunResult(
                rc=1, err='sudo: a password is required'),
            ('h', 'smd doctor'): _ok('deploy trees clean'),
        })
        results = fleet_doctor(fleet, run)
        assert results[0].elevated is False
        report = format_doctor(results)
        assert 'unelevated' in report.lower() or 'not elevated' in report.lower()

    def test_elevation_never_reaches_a_mutating_form(self):
        """The whole point of pinning verbs: --fix and --apply must never
        be issued with sudo, or the sudoers rule would have to permit
        them."""
        fleet = _fleet(Host(name='h', reach='r'))
        run = runner({
            ('h', 'true'): _ok(),
            ('h', 'sudo -n smd doctor'): _ok('clean'),
        })
        fleet_doctor(fleet, run)
        for _host, command in run.calls:
            assert '--fix' not in command
            assert '--apply' not in command
