"""Tests for sigmond.purge — the full-clean path used by
``smd remove --purge`` and by ``smd remove <deprecated-name>``."""

from pathlib import Path
from unittest import mock

import pytest

from sigmond import purge


@pytest.fixture
def stub_paths(tmp_path, monkeypatch):
    """Redirect every GIT_BASE / ETC_BASE / VENV_BASE so the test
    operates in tmp_path instead of the host's real filesystem."""
    monkeypatch.setattr(purge, 'GIT_BASE', tmp_path / 'opt-git')
    monkeypatch.setattr(purge, 'ETC_BASE', tmp_path / 'etc')
    monkeypatch.setattr(purge, 'VENV_BASE', tmp_path / 'opt')
    monkeypatch.setattr(purge, 'SYSTEMD_SYSTEM', tmp_path / 'systemd')
    monkeypatch.setattr(purge, 'LOCAL_BIN', tmp_path / 'bin')
    monkeypatch.setattr(purge, 'SIGMOND_CLIENTS', tmp_path / 'sigmond-clients')
    return tmp_path


def _scaffold_client(stub_paths, name, *, deploy_toml=None,
                     with_venv=True, with_config=True):
    """Lay out a fake client install (source repo + venv + config) so
    plan_purge has something to find."""
    repo = stub_paths / 'opt-git' / name
    repo.mkdir(parents=True)
    if deploy_toml is not None:
        (repo / 'deploy.toml').write_text(deploy_toml)
    if with_venv:
        (stub_paths / 'opt' / name).mkdir(parents=True)
    if with_config:
        (stub_paths / 'etc' / name).mkdir(parents=True)
    return repo


class TestPlanPurge:
    def test_finds_everything_on_disk(self, stub_paths):
        _scaffold_client(stub_paths, 'old-client', deploy_toml='')
        plan = purge.plan_purge('old-client')
        assert plan['repo_dir'] is not None
        assert plan['venv_dir'] is not None
        assert plan['config_dir'] is not None

    def test_empty_plan_when_nothing_on_disk(self, stub_paths):
        plan = purge.plan_purge('nope')
        assert plan['repo_dir'] is None
        assert plan['venv_dir'] is None
        assert plan['config_dir'] is None
        assert plan['expanded_units'] == []
        assert plan['link_dsts'] == []

    def test_parses_units_and_link_steps_from_deploy_toml(
            self, stub_paths, monkeypatch):
        # Avoid hitting real systemctl for template expansion.
        monkeypatch.setattr(purge, '_running_template_instances',
                            lambda u: [])
        deploy = (
            '[systemd]\n'
            'units = ["old-client.service"]\n'
            '\n'
            '[[install.steps]]\n'
            'kind = "link"\n'
            'src  = "systemd/old-client.service"\n'
            'dst  = "/etc/systemd/system/old-client.service"\n'
        )
        _scaffold_client(stub_paths, 'old-client', deploy_toml=deploy)
        plan = purge.plan_purge('old-client')
        assert plan['declared_units'] == ['old-client.service']
        assert plan['expanded_units'] == ['old-client.service']
        assert plan['link_dsts'] == [
            Path('/etc/systemd/system/old-client.service')]


class TestExecutePurge:
    def test_removes_every_tree(self, stub_paths, monkeypatch):
        repo = _scaffold_client(stub_paths, 'old-client', deploy_toml='')
        venv = stub_paths / 'opt' / 'old-client'
        config = stub_paths / 'etc' / 'old-client'
        # Stub systemctl + symlink removal so the test stays hermetic.
        monkeypatch.setattr(purge.subprocess, 'run',
                            lambda *a, **kw: mock.Mock(returncode=0, stderr=''))
        plan = purge.plan_purge('old-client')
        rc = purge.execute_purge(plan)
        assert rc == 0
        assert not repo.exists()
        assert not venv.exists()
        assert not config.exists()

    def test_dry_run_makes_no_changes(self, stub_paths, monkeypatch):
        repo = _scaffold_client(stub_paths, 'old-client', deploy_toml='')
        monkeypatch.setattr(purge.subprocess, 'run',
                            lambda *a, **kw: mock.Mock(returncode=0, stderr=''))
        plan = purge.plan_purge('old-client')
        rc = purge.execute_purge(plan, dry_run=True)
        assert rc == 0
        assert repo.exists()      # still here

    def test_systemctl_failure_warns_but_continues(
            self, stub_paths, monkeypatch, capsys):
        """A failing systemctl-stop on an already-gone unit must not
        block the rm-rf phase — the purge is best-effort on the
        systemd side and authoritative on the filesystem side."""
        monkeypatch.setattr(purge, '_running_template_instances',
                            lambda u: [])
        deploy = (
            '[systemd]\n'
            'units = ["old-client.service"]\n'
        )
        repo = _scaffold_client(stub_paths, 'old-client',
                                deploy_toml=deploy, with_venv=False,
                                with_config=False)
        # Simulate systemctl errors that are NOT the "already gone" case
        # so the warning path actually fires.
        monkeypatch.setattr(
            purge.subprocess, 'run',
            lambda *a, **kw: mock.Mock(returncode=5, stderr='boom'),
        )
        plan = purge.plan_purge('old-client')
        rc = purge.execute_purge(plan)
        assert rc == 0
        assert not repo.exists()
        err = capsys.readouterr().err
        assert 'boom' in err


class TestDeprecatedOnDisk:
    def test_picks_up_dirs_only_in_deprecated_set(self, stub_paths):
        # Two on disk, only one in the deprecation list.
        (stub_paths / 'opt-git' / 'wsprdaemon-client').mkdir(parents=True)
        (stub_paths / 'opt-git' / 'still-supported').mkdir(parents=True)
        deprecated = {'wsprdaemon-client': mock.Mock()}
        assert purge.deprecated_on_disk(deprecated) == ['wsprdaemon-client']

    def test_skips_deprecated_entries_with_no_dir(self, stub_paths):
        deprecated = {'long-gone-client': mock.Mock()}
        assert purge.deprecated_on_disk(deprecated) == []


class TestKj6mkiLeftovers:
    """`smd component remove --purge --force` on KJ6MKI (2026-11-12) left
    hf-tec-qa@.service/.timer links dangling, gmag-webui.deploy.toml in
    /etc/sigmond/clients, and empty /var/lib/<name> dirs."""

    def _hf_tec(self, stub_paths, monkeypatch):
        monkeypatch.setattr(purge, '_running_template_instances', lambda u: [])
        state = stub_paths / 'var-lib' / 'hf-tec'
        log = stub_paths / 'var-log' / 'hf-tec'
        deploy = (
            '[systemd]\nunits = []\ntemplated_units = ["hf-tec@.service"]\n\n'
            f'[[install.steps]]\nkind = "mkdir"\ndst = "{state}"\n\n'
            f'[[install.steps]]\nkind = "mkdir"\ndst = "{log}"\n')
        repo = _scaffold_client(stub_paths, 'hf-tec', deploy_toml=deploy)
        (repo / 'systemd').mkdir()
        sysd = stub_paths / 'systemd'
        (sysd / 'timers.target.wants').mkdir(parents=True)
        for u in ('hf-tec@.service', 'hf-tec-qa@.service', 'hf-tec-qa@.timer'):
            (sysd / u).symlink_to(repo / 'systemd' / u)
        (sysd / 'timers.target.wants' / 'hf-tec-qa@x.timer').symlink_to(
            repo / 'systemd' / 'hf-tec-qa@.timer')
        (sysd / 'other.service').symlink_to(stub_paths / 'elsewhere.service')
        # a sibling checkout that only shares the name prefix: never ours
        sib = repo.parent / (repo.name + '-sibling')
        (sib / 'systemd').mkdir(parents=True)
        (sysd / 'sibling.service').symlink_to(sib / 'systemd' / 'sibling.service')
        (stub_paths / 'bin').mkdir()
        (stub_paths / 'bin' / 'hf-tec').symlink_to(repo / 'venv' / 'bin' / 'hf-tec')
        clients = stub_paths / 'sigmond-clients'
        clients.mkdir()
        (clients / 'hf-tec.deploy.toml').write_text('x')
        (clients / 'hf-tec@KJ6MKI.sources.toml').write_text('x')
        (clients / 'psk-recorder.deploy.toml').write_text('x')
        (state / 'sub').mkdir(parents=True)
        log.mkdir(parents=True)
        (log / 'keep.log').write_text('data')
        return repo, sysd, clients, state, log

    def test_plan_names_every_leftover(self, stub_paths, monkeypatch):
        self._hf_tec(stub_paths, monkeypatch)
        plan = purge.plan_purge('hf-tec')
        assert 'hf-tec@.service' in plan['declared_units']
        names = sorted(p.name for p in plan['owned_links'])
        assert names == ['hf-tec', 'hf-tec-qa@.service', 'hf-tec-qa@.timer',
                         'hf-tec-qa@x.timer', 'hf-tec@.service']
        assert sorted(p.name for p in plan['client_files']) == [
            'hf-tec.deploy.toml', 'hf-tec@KJ6MKI.sources.toml']
        assert any('state dir' in l for l in purge.render_plan(plan))

    def test_execute_removes_them_and_keeps_data(self, stub_paths, monkeypatch):
        repo, sysd, clients, state, log = self._hf_tec(stub_paths, monkeypatch)
        calls = []
        monkeypatch.setattr(purge.subprocess, 'run',
                            lambda a, **kw: calls.append(a) or mock.Mock(returncode=0, stderr=''))
        assert purge.execute_purge(purge.plan_purge('hf-tec')) == 0
        assert not any(p.name.startswith('hf-tec') for p in sysd.iterdir())
        assert not list((sysd / 'timers.target.wants').iterdir())
        assert (sysd / 'other.service').is_symlink()           # not ours: kept
        assert (sysd / 'sibling.service').is_symlink()         # prefix-only sibling: kept
        assert not (stub_paths / 'bin' / 'hf-tec').is_symlink()
        assert sorted(p.name for p in clients.iterdir()) == ['psk-recorder.deploy.toml']
        assert not state.exists()                              # empty tree: gone
        assert (log / 'keep.log').read_text() == 'data'        # holds data: kept
        assert ['systemctl', 'disable', 'hf-tec@.service'] in calls
