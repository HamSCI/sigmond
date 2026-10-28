"""deepen_history: a shallow component checkout gains its commit history
without old file contents, so the docs index can date its pages.

Real temporary git repos throughout; origins are local file:// bare repos,
so no test touches the network.  See bin/smd `deepen_history`.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

GIT_ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid',
    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
}


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_deepen", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_deepen", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def git(*args, cwd=None, env_extra=None):
    env = dict(os.environ, **GIT_ENV, **(env_extra or {}))
    return subprocess.run(['git', *args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


FIRST_DATE = '2025-01-02T03:04:05+00:00'
FIRST_EPOCH = '1735787045'


@pytest.fixture
def origin(tmp_path):
    """A bare origin with three commits; docs/a.md only in the first."""
    work = tmp_path / 'work'
    work.mkdir()
    git('init', '-q', '-b', 'main', cwd=work)
    (work / 'docs').mkdir()
    (work / 'docs' / 'a.md').write_text('a\n')
    git('add', '.', cwd=work)
    git('commit', '-q', '-m', 'one', cwd=work,
        env_extra={'GIT_AUTHOR_DATE': FIRST_DATE,
                   'GIT_COMMITTER_DATE': FIRST_DATE})
    for n in ('two', 'three'):
        (work / f'{n}.txt').write_text(n + '\n')
        git('add', '.', cwd=work)
        git('commit', '-q', '-m', n, cwd=work)
    bare = tmp_path / 'origin.git'
    git('clone', '-q', '--bare', str(work), str(bare))
    # GitHub honours --filter; a local origin ignores it unless told to.
    git('config', 'uploadpack.allowFilter', 'true', cwd=bare)
    return bare


def _shallow_clone(origin, dest):
    git('clone', '-q', '--depth', '1', f'file://{origin}', str(dest))
    assert git('rev-parse', '--is-shallow-repository', cwd=dest) == 'true'
    return dest


def _config(repo, key):
    r = subprocess.run(['git', '-C', str(repo), 'config', '--get', key],
                       capture_output=True, text=True,
                       env=dict(os.environ, **GIT_ENV))
    return r.stdout.strip() if r.returncode == 0 else None


def test_shallow_clone_gains_history_and_keeps_its_worktree(origin, tmp_path):
    clone = _shallow_clone(origin, tmp_path / 'clone')
    # Before: the one commit git has dates docs/a.md as the boundary.
    assert git('log', '--format=%ct', '--', 'docs/a.md', cwd=clone) != FIRST_EPOCH

    assert smd.deepen_history(clone) == 'deepened'

    assert git('rev-parse', '--is-shallow-repository', cwd=clone) == 'false'
    assert git('log', '-1', '--format=%ct', '--', 'docs/a.md',
               cwd=clone) == FIRST_EPOCH
    assert git('rev-list', '--count', 'HEAD', cwd=clone) == '3'
    assert git('status', '--porcelain', cwd=clone) == ''
    assert _config(clone, 'remote.origin.promisor') == 'true'
    assert _config(clone, 'remote.origin.partialclonefilter') is None


def test_after_deepening_new_commits_arrive_with_their_contents(
        origin, tmp_path):
    """Later fetches are not blob-less: a new commit's file contents arrive
    with the fetch, so they read back, and merge, with the origin gone."""
    clone = _shallow_clone(origin, tmp_path / 'clone')
    assert smd.deepen_history(clone) == 'deepened'

    work = tmp_path / 'work2'
    git('clone', '-q', f'file://{origin}', str(work))
    (work / 'docs' / 'new.md').write_text('brand new page\n')
    git('add', '.', cwd=work)
    git('commit', '-q', '-m', 'four', cwd=work)
    git('push', '-q', 'origin', 'main', cwd=work)

    git('fetch', '-q', 'origin', cwd=clone)
    # Offline from here: a lazy fetch would fail, so each step below proves
    # the fetch itself brought the new commit's contents.
    origin.rename(tmp_path / 'origin-gone')
    blob = git('rev-parse', 'origin/main:docs/new.md', cwd=clone)
    git('cat-file', '-e', blob, cwd=clone)            # raises if missing
    git('merge', '-q', '--ff-only', 'origin/main', cwd=clone)
    assert (clone / 'docs' / 'new.md').read_text() == 'brand new page\n'


def test_full_repo_returns_full_and_never_fetches(origin, tmp_path):
    clone = tmp_path / 'full'
    git('clone', '-q', f'file://{origin}', str(clone))
    calls = []

    def spy(cmd, **kw):
        calls.append(cmd)
        return subprocess.run(cmd, **kw)

    assert smd.deepen_history(clone, run=spy) == 'full'
    assert calls, 'the shallow check must go through run='
    assert not any('fetch' in c for c in calls), calls
    assert _config(clone, 'remote.origin.promisor') is None


def test_refusing_origin_fails_and_leaves_the_clone_as_it_was(
        origin, tmp_path, capsys):
    clone = _shallow_clone(origin, tmp_path / 'clone')
    shutil.rmtree(origin)          # the origin now refuses every fetch

    assert smd.deepen_history(clone) == 'failed'

    assert git('rev-parse', '--is-shallow-repository', cwd=clone) == 'true'
    assert _config(clone, 'remote.origin.promisor') is None
    assert _config(clone, 'remote.origin.partialclonefilter') is None
    assert git('status', '--porcelain', cwd=clone) == ''
    out = capsys.readouterr()
    lines = [l for l in (out.out + out.err).splitlines() if l.strip()]
    assert len(lines) == 1 and 'clone' in lines[0], lines


def test_component_fetch_deepens_before_it_fetches(origin, tmp_path,
                                                   monkeypatch):
    """`_fetch_checkout` deepens a checkout before its usual fetch.  (The
    test below checks that the daily fetch path calls `_fetch_checkout`.)"""
    order = []
    monkeypatch.setattr(smd, 'deepen_history',
                        lambda repo, **kw: order.append(('deepen', repo)) or 'full')
    real_fetch = smd._git_fetch_as_owner
    monkeypatch.setattr(smd, '_git_fetch_as_owner',
                        lambda repo: order.append(('fetch', repo))
                        or real_fetch(repo))
    clone = _shallow_clone(origin, tmp_path / 'clone')
    smd._fetch_checkout(clone)
    assert order == [('deepen', clone), ('fetch', clone)]


def test_component_statuses_fetches_through_fetch_checkout():
    """Both fetch sites in `_component_statuses` (the per-component rows and
    sigmond's own row) go through `_fetch_checkout`, so the daily fetch that
    sigmond-component-fetch.service runs deepens every checkout."""
    import inspect
    src = inspect.getsource(smd._component_statuses)
    assert '_fetch_checkout(repo_dir)' in src
    assert '_fetch_checkout(sigmond_dir)' in src
    assert '_git_fetch_as_owner(' not in src


def test_fetch_that_fails_after_unshallowing_counts_as_deepened(origin,
                                                               tmp_path):
    """git can exit nonzero after it wrote the unshallowed packs.  Taking the
    promisor key out then would leave promisor packs with no promisor
    remote, a broken clone.  So a non-shallow result counts as deepened."""
    clone = _shallow_clone(origin, tmp_path / 'clone')

    def fetch_lies(cmd, **kw):
        r = subprocess.run(cmd, **kw)
        if 'fetch' in cmd and '--unshallow' in cmd:
            return subprocess.CompletedProcess(cmd, 1, '', 'simulated failure')
        return r

    assert smd.deepen_history(clone, run=fetch_lies) == 'deepened'
    assert git('rev-parse', '--is-shallow-repository', cwd=clone) == 'false'
    assert git('rev-list', '--count', 'HEAD', cwd=clone) == '3'
    assert _config(clone, 'remote.origin.promisor') == 'true'
    assert _config(clone, 'remote.origin.partialclonefilter') is None


def test_failure_keeps_keys_that_were_already_there(origin, tmp_path):
    clone = _shallow_clone(origin, tmp_path / 'clone')
    git('config', 'remote.origin.promisor', 'true', cwd=clone)
    shutil.rmtree(origin)          # the origin now refuses every fetch

    assert smd.deepen_history(clone) == 'failed'

    assert git('rev-parse', '--is-shallow-repository', cwd=clone) == 'true'
    assert _config(clone, 'remote.origin.promisor') == 'true'
    assert _config(clone, 'remote.origin.partialclonefilter') is None
