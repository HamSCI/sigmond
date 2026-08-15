"""`smd doctor` — find the deploy-tree damage before it blocks an update.

Every check here corresponds to something that actually blocked the
DASI002 (Scranton) update on 2026-08-15, each discovered only when a
command failed:

* **foreign ownership** — 2996 root-owned paths in hf-timestd and 938 in
  wspr-recorder, left by an older sigmond whose `_git()` ran as root.
  `git pull` failed with "cannot create directory ... Permission denied"
  and "failed to write object".  The current `_git()` delegates to
  `gitowner.run_git()` and no longer causes this, so the bug is fixed but
  the WRECKAGE persists on every box installed before the fix, with
  nothing to detect or repair it.
* **dirty tree** — a real uncommitted fix to timestd-metrology@.service
  (StartLimit keys moved from [Service], where systemd ignores them, to
  [Unit]) blocked the pull.  Discarding it blindly would have destroyed
  work; it had to be diffed against the incoming version first.
* **local commits** — the same class, but unpushed.
* **venv skew** — hf-timestd's venv held a COPIED ka9q-python 3.22.0
  while four sibling venvs were editable off the shared checkout and had
  already updated.  Pulling the checkout did not reach it, so the new
  code was silently absent from the one service that needed it most.

The point of the tool is that each of these is reported in one pass
instead of surfacing one failed command at a time.
"""
import os
import subprocess

import pytest

from sigmond.doctor import (
    foreign_owned, git_state, venv_skew, Finding, summarise,
)


# ── ownership ────────────────────────────────────────────────────────

def test_no_findings_when_everything_matches_the_expected_owner(tmp_path):
    (tmp_path / "a").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_text("y")

    assert foreign_owned(tmp_path, os.getuid()) == []


def test_foreign_owned_paths_are_reported(tmp_path):
    """The 2996-path case: files a service user cannot write."""
    (tmp_path / "a").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_text("y")

    bad = foreign_owned(tmp_path, os.getuid() + 12345)

    # the root, the file, the subdir and its file
    assert len(bad) >= 3
    assert any(p.name == "b" for p in bad)


def test_ownership_scan_tolerates_a_missing_tree(tmp_path):
    assert foreign_owned(tmp_path / "nope", os.getuid()) == []


# ── git state ────────────────────────────────────────────────────────

def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    _git("init", "-q", "-b", "main", cwd=r)
    _git("config", "user.email", "t@t", cwd=r)
    _git("config", "user.name", "t", cwd=r)
    (r / "f").write_text("one\n")
    _git("add", "f", cwd=r)
    _git("commit", "-qm", "one", cwd=r)
    return r


def test_a_clean_repo_reports_clean(repo):
    st = git_state(repo)

    assert st["dirty"] == []
    assert st["detached"] is False


def test_a_modified_file_is_reported_not_discarded(repo):
    """The metrology case — the tool must SAY so, never fix it."""
    (repo / "f").write_text("two\n")

    st = git_state(repo)

    assert st["dirty"] == ["f"]


def test_a_detached_head_is_reported(repo):
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()
    _git("checkout", "-q", head, cwd=repo)

    assert git_state(repo)["detached"] is True


def test_git_state_on_a_non_repo_is_not_an_error(tmp_path):
    assert git_state(tmp_path)["error"] is not None


# ── venv skew ────────────────────────────────────────────────────────

def test_a_venv_installed_from_the_shared_checkout_is_not_skewed():
    probe = lambda v: {"location": "/opt/git/sigmond/ka9q-python/ka9q",
                       "version": "3.24.0"}

    assert venv_skew(["/opt/git/sigmond/psk-recorder/venv"],
                     shared="/opt/git/sigmond/ka9q-python",
                     probe=probe) == []


def test_a_venv_with_its_own_copy_is_reported():
    """hf-timestd's case: a private copy that a checkout update cannot
    reach, so the new code is silently absent."""
    probe = lambda v: {"location": v + "/lib/python3.11/site-packages/ka9q",
                       "version": "3.22.0"}

    skew = venv_skew(["/opt/git/sigmond/hf-timestd/venv"],
                     shared="/opt/git/sigmond/ka9q-python",
                     probe=probe)

    assert len(skew) == 1
    assert "3.22.0" in skew[0]["version"]


def test_a_venv_without_the_package_is_skipped():
    assert venv_skew(["/x/venv"], shared="/s", probe=lambda v: None) == []


# ── summary ──────────────────────────────────────────────────────────

def test_summary_is_clean_when_there_are_no_findings():
    ok, text = summarise([])

    assert ok is True
    assert "clean" in text.lower()


def test_summary_reports_and_fails_when_findings_exist():
    ok, text = summarise([
        Finding("hf-timestd", "ownership", "2996 path(s) not owned by timestd",
                fixable=True),
        Finding("hf-timestd", "dirty", "1 modified file", fixable=False),
    ])

    assert ok is False
    assert "hf-timestd" in text
    assert "2996" in text


def test_untracked_files_are_not_reported_as_modified(repo):
    """`?? path` does NOT block a pull, and conflating it with ` M path`
    sends the operator looking for a local edit that isn't there.

    Untracked files get their own class because they are the `.awkshim`
    hazard: harmless in place, but swept into a commit by `git add -A`.
    """
    (repo / "junk").write_text("stray\n")

    st = git_state(repo)

    assert st["dirty"] == []
    assert st["untracked"] == ["junk"]


def test_modified_and_untracked_are_reported_separately(repo):
    (repo / "f").write_text("changed\n")
    (repo / "junk").write_text("stray\n")

    st = git_state(repo)

    assert st["dirty"] == ["f"]
    assert st["untracked"] == ["junk"]
