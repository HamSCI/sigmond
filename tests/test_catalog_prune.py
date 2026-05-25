"""Tests for sigmond.catalog_prune — operator-catalog minimisation."""

from pathlib import Path

import pytest

from sigmond.catalog_prune import (
    _prune_block,
    compute_minimal_operator,
    prune_operator_catalog,
    _render_minimal,
)


def _write(path: Path, body: str) -> None:
    path.write_text(body)


def test_prune_block_drops_matching_keys():
    op = {'kind': 'client', 'repo': 'https://repo/foo',
          'description': 'same'}
    repo = {'kind': 'client', 'repo': 'https://repo/foo',
            'description': 'same'}
    assert _prune_block(op, repo) == {}


def test_prune_block_keeps_diverging_keys():
    op = {'kind': 'client', 'repo': 'git@my-fork:foo',
          'description': 'same'}
    repo = {'kind': 'client', 'repo': 'https://repo/foo',
            'description': 'same'}
    assert _prune_block(op, repo) == {'repo': 'git@my-fork:foo'}


def test_prune_block_keeps_keys_repo_does_not_declare():
    op = {'custom_field': 'host-only'}
    repo = {'kind': 'client'}
    assert _prune_block(op, repo) == {'custom_field': 'host-only'}


class TestComputeMinimalOperator:
    def test_no_operator_file_is_no_op(self, tmp_path):
        repo = tmp_path / 'repo.toml'
        repo.write_text('[client.foo]\nkind = "client"\n')
        op = tmp_path / 'op.toml'  # does not exist
        minimal, report = compute_minimal_operator(op, repo)
        assert report.no_op is True
        assert minimal == {'client': {}, 'deprecated': {}}

    def test_full_duplicate_collapses_to_empty(self, tmp_path):
        repo = tmp_path / 'repo.toml'
        repo.write_text(
            '[client.foo]\n'
            'kind = "client"\n'
            'description = "foo"\n'
            'repo = "https://repo/foo"\n'
            '\n'
            '[deprecated.old]\n'
            'removed_in = "abc"\n'
        )
        op = tmp_path / 'op.toml'
        op.write_text(repo.read_text())
        minimal, report = compute_minimal_operator(op, repo)
        assert minimal == {'client': {}, 'deprecated': {}}
        assert ('client', 'foo') in report.dropped_blocks
        assert ('deprecated', 'old') in report.dropped_blocks

    def test_partial_override_keeps_only_overriding_field(self, tmp_path):
        repo = tmp_path / 'repo.toml'
        repo.write_text(
            '[client.foo]\n'
            'kind = "client"\n'
            'description = "from repo"\n'
            'repo = "https://repo/foo"\n'
        )
        op = tmp_path / 'op.toml'
        op.write_text(
            '[client.foo]\n'
            'kind = "client"\n'
            'description = "from repo"\n'
            'repo = "git@my-fork:foo"\n'   # only this diverges
        )
        minimal, report = compute_minimal_operator(op, repo)
        assert minimal['client']['foo'] == {'repo': 'git@my-fork:foo'}
        assert ('client', 'foo') in report.kept_blocks
        # Two keys pruned (kind, description), one kept (repo).
        pruned_keys = {k for _, _, k in report.pruned_keys}
        assert pruned_keys == {'kind', 'description'}

    def test_operator_only_block_kept_verbatim(self, tmp_path):
        repo = tmp_path / 'repo.toml'
        repo.write_text('[client.foo]\nkind = "client"\n')
        op = tmp_path / 'op.toml'
        op.write_text(
            '[client.host-only]\n'
            'kind = "client"\n'
            'description = "added on this host only"\n'
            'repo = "https://host/repo"\n'
        )
        minimal, _report = compute_minimal_operator(op, repo)
        assert 'host-only' in minimal['client']
        assert minimal['client']['host-only']['description'] == \
            'added on this host only'


class TestPruneOperatorCatalog:
    def test_removes_file_when_fully_redundant(self, tmp_path):
        repo = tmp_path / 'repo.toml'
        repo.write_text(
            '[client.foo]\n'
            'kind = "client"\n'
            'description = "foo"\n'
        )
        op = tmp_path / 'op.toml'
        op.write_text(repo.read_text())
        report = prune_operator_catalog(op, repo)
        assert report.removed_file is True
        assert not op.exists()
        assert report.backup_path is not None and report.backup_path.exists()

    def test_rewrites_file_with_minimal_content(self, tmp_path):
        repo = tmp_path / 'repo.toml'
        repo.write_text(
            '[client.foo]\n'
            'kind = "client"\n'
            'description = "from repo"\n'
            'repo = "https://repo/foo"\n'
        )
        op = tmp_path / 'op.toml'
        op.write_text(
            '[client.foo]\n'
            'kind = "client"\n'
            'description = "from repo"\n'
            'repo = "git@my-fork:foo"\n'
        )
        report = prune_operator_catalog(op, repo)
        assert op.exists()
        rewritten = op.read_text()
        # The override survives.
        assert 'git@my-fork:foo' in rewritten
        # The duplicated keys do not.
        assert '"from repo"' not in rewritten
        # The backup of the old file should still contain everything.
        assert report.backup_path is not None
        assert '"from repo"' in report.backup_path.read_text()

    def test_dry_run_does_not_write(self, tmp_path):
        repo = tmp_path / 'repo.toml'
        repo.write_text('[client.foo]\nkind = "client"\n')
        op = tmp_path / 'op.toml'
        op.write_text('[client.foo]\nkind = "client"\n')
        before = op.read_text()
        report = prune_operator_catalog(op, repo, dry_run=True)
        assert op.read_text() == before
        assert report.backup_path is None
        # Report still shows what would have been dropped.
        assert ('client', 'foo') in report.dropped_blocks

    def test_already_minimal_is_no_op_write(self, tmp_path):
        repo = tmp_path / 'repo.toml'
        repo.write_text('[client.foo]\nkind = "client"\n')
        op = tmp_path / 'op.toml'
        op.write_text(
            '[client.host-only]\n'
            'kind = "client"\n'
            'description = "host"\n'
        )
        original = op.read_text()
        original_mtime = op.stat().st_mtime
        report = prune_operator_catalog(op, repo)
        # No backup created — nothing pruned, nothing dropped.
        assert report.backup_path is None
        # File untouched (mtime preserved).
        assert op.stat().st_mtime == original_mtime
        assert op.read_text() == original


class TestRenderMinimal:
    def test_emits_valid_toml(self, tmp_path):
        sections = {
            'client': {
                'foo': {'repo': 'git@my-fork:foo'},
                'host-only': {
                    'kind': 'client',
                    'description': 'desc',
                    'requires': ['lib-a', 'lib-b'],
                    'start_priority': 100,
                },
            },
            'deprecated': {},
        }
        rendered = _render_minimal(sections)
        # Round-trip through tomllib to confirm it parses.
        import tomllib
        parsed = tomllib.loads(rendered)
        assert parsed['client']['foo']['repo'] == 'git@my-fork:foo'
        assert parsed['client']['host-only']['requires'] == ['lib-a', 'lib-b']
        assert parsed['client']['host-only']['start_priority'] == 100

    def test_escapes_quotes_in_strings(self):
        sections = {
            'client': {'foo': {'description': 'has "quotes" inside'}},
            'deprecated': {},
        }
        rendered = _render_minimal(sections)
        import tomllib
        parsed = tomllib.loads(rendered)
        assert parsed['client']['foo']['description'] == 'has "quotes" inside'
