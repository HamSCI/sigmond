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
from pathlib import Path

import pytest

from sigmond.doctor import (
    component_checkouts, foreign_owned, git_state, venv_skew,
    Finding, summarise, manifest_drift, exec_mismatch,
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


def test_venv_contents_are_not_flagged_as_foreign(tmp_path):
    """A venv legitimately belongs to its SERVICE user, not the checkout
    owner — mag-recorder's venv alone produced 1330 false findings on the
    first live run.  A diagnostic that cries wolf gets ignored.

    Build metadata (egg-info, __pycache__) is NOT excluded: that is what
    blocked `pip install` on DASI002 with "Cannot update time stamp of
    directory 'ka9q_python.egg-info'".
    """
    (tmp_path / "venv" / "lib").mkdir(parents=True)
    (tmp_path / "venv" / "lib" / "x").write_text("service-owned")
    (tmp_path / "src.egg-info").mkdir()
    (tmp_path / "src.egg-info" / "PKG-INFO").write_text("blocks installs")

    bad = foreign_owned(tmp_path, os.getuid() + 12345)
    names = {p.name for p in bad}

    assert "x" not in names          # inside venv/ — excluded
    assert "PKG-INFO" in names       # egg-info — still reported


def test_inspecting_a_repo_does_not_write_the_index(repo):
    """`git status` refreshes the stat cache and REWRITES .git/index — so a
    diagnostic run as root leaves a root-owned index behind, recreating
    the exact damage it exists to find.  Observed on DASI002: `.git/index`
    reappeared immediately after `smd doctor --fix` repaired it.

    `--no-optional-locks` is git's documented flag for read-only status
    tools.  The index must be byte-identical after inspection.
    """
    idx = repo / ".git" / "index"
    before = idx.read_bytes(), idx.stat().st_mtime_ns

    git_state(repo)

    assert (idx.read_bytes(), idx.stat().st_mtime_ns) == before


def test_an_unreadable_entry_does_not_crash_the_scan(tmp_path, monkeypatch):
    """`/opt/git/sigmond/.ssh` is not readable by the invoking user, and on
    Python 3.13 `Path.exists()` propagates PermissionError rather than
    returning False — so `smd doctor` died with a traceback on B4 while
    working fine on B3 (3.11).  A diagnostic must survive the very
    permission problems it exists to report.
    """
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / ".git").mkdir()
    (tmp_path / "unreadable").mkdir()

    real_exists = Path.exists

    def boom(self, *a, **kw):
        # Match the entry precisely: pytest's tmp_path embeds the test
        # name, so a substring check fires for every path under it.
        if self.parent.name == "unreadable" or self.name == "unreadable":
            raise PermissionError(13, "Permission denied", str(self))
        return real_exists(self, *a, **kw)

    monkeypatch.setattr(Path, "exists", boom)

    assert [d.name for d in component_checkouts(tmp_path)] == ["good"]


def test_tool_and_build_caches_are_not_flagged(tmp_path):
    """B4's first run buried the real findings: 18460 paths for `.vscode`,
    3080 for `dist`, 2340 for `.venv`, 1523 for `.ruff_cache`.  None of
    those block a git operation or an install — they are editor and build
    detritus whose ownership is irrelevant.

    `.git` and `*.egg-info` stay in scope: a root-owned `.git/index` is
    the signature of the bug this tool exists to find, and an unwritable
    egg-info is what blocks pip.
    """
    for junk in ('.venv', '.vscode', '.ruff_cache', 'dist', '__pycache__'):
        (tmp_path / junk).mkdir()
        (tmp_path / junk / 'x').write_text('noise')
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git' / 'index').write_text('signal')
    (tmp_path / 'p.egg-info').mkdir()
    (tmp_path / 'p.egg-info' / 'PKG-INFO').write_text('signal')

    names = {p.name for p in foreign_owned(tmp_path, os.getuid() + 12345)}

    assert 'index' in names and 'PKG-INFO' in names
    assert 'x' not in names


# ── manifest drift ──────────────────────────────────────────────────

def test_manifest_drift_reports_moved_components(tmp_path):
    manifest = tmp_path / 'manifest.txt'
    manifest.write_text(
        "image_version: v3.32\n"
        "\ncomponents (live):\n"
        "    hf-timestd       aaaaaaa\n"
        "    wspr-recorder    bbbbbbb\n"
    )
    live = {'hf-timestd': 'ccccccc', 'wspr-recorder': 'bbbbbbb'}
    out = manifest_drift(live, str(manifest))
    assert [d['component'] for d in out] == ['hf-timestd']
    assert out[0]['manifest'] == 'aaaaaaa'
    assert out[0]['live'] == 'ccccccc'


def test_manifest_drift_on_a_missing_manifest_is_not_an_error(tmp_path):
    """A host installed from an older image (predating this manifest
    format) has nothing to compare against — that is not drift, it is
    simply unassessable, and must not raise."""
    assert manifest_drift({'hf-timestd': 'ccccccc'},
                          str(tmp_path / 'nope.txt')) == []


def test_manifest_drift_reports_component_added_since_install(tmp_path):
    """Present live, absent from the manifest: installed after the image
    was built. Distinct from a moved SHA — there is no manifest value to
    compare against."""
    manifest = tmp_path / 'manifest.txt'
    manifest.write_text(
        "components (live):\n"
        "    hf-timestd       aaaaaaa\n"
    )
    live = {'hf-timestd': 'aaaaaaa', 'meteor-scatter': 'ddddddd'}
    out = manifest_drift(live, str(manifest))
    assert [d['component'] for d in out] == ['meteor-scatter']
    assert out[0]['manifest'] is None
    assert out[0]['live'] == 'ddddddd'


def test_manifest_drift_reports_component_missing_since_install(tmp_path):
    """Present in the manifest, absent live: removed, or failed to
    install. Distinct from a moved SHA — there is no live value to
    compare against."""
    manifest = tmp_path / 'manifest.txt'
    manifest.write_text(
        "components (live):\n"
        "    hf-timestd       aaaaaaa\n"
        "    codar-sounder    eeeeeee\n"
    )
    live = {'hf-timestd': 'aaaaaaa'}
    out = manifest_drift(live, str(manifest))
    assert [d['component'] for d in out] == ['codar-sounder']
    assert out[0]['manifest'] == 'eeeeeee'
    assert out[0]['live'] is None


def test_manifest_drift_is_silent_when_nothing_moved(tmp_path):
    manifest = tmp_path / 'manifest.txt'
    manifest.write_text(
        "components (live):\n"
        "    hf-timestd       aaaaaaa\n"
    )
    assert manifest_drift({'hf-timestd': 'aaaaaaa'}, str(manifest)) == []


def test_manifest_drift_tolerates_the_release_manifest_shape(tmp_path):
    """The Release-attached manifest carries an `image_sha256:` field the
    host copy at /etc/sigmond-appliance/manifest.txt does not. The parser
    must not depend on it, and must skip the non-component preamble and
    the trailing free-text lines below the block."""
    manifest = tmp_path / 'manifest.txt'
    manifest.write_text(
        "image_version: v3.32\n"
        "appliance_commit: 9f6d417605b78a6171b5cf81a1802c1bfcba7679\n"
        "appliance_tag: v3.32\n"
        "built_utc: 2026-08-16T14:49:08+00:00\n"
        "image_sha256: 6c11624261e3c8d123df700656486257b522268710e820b0c3a64cc78d723d53\n"
        "\n"
        "components (live):\n"
        "    hf-timestd       aaaaaaa\n"
        "    superdarn-sounder eeeeeee\n"
        "\n"
        "no recorded updates since install\n"
    )
    live = {'hf-timestd': 'ccccccc', 'superdarn-sounder': 'eeeeeee'}
    out = manifest_drift(live, str(manifest))
    assert [d['component'] for d in out] == ['hf-timestd']


# ── exec mismatch ────────────────────────────────────────────────────
# The radiod-swap incident this check exists for: a systemd drop-in
# pointed ExecStart at a DIFFERENT binary than the one that had been
# installed, and "verification" checked the installed file, never the
# running process. The lesson: verify /proc/<pid>/exe, not the file you
# installed.

def test_exec_mismatch_flags_running_wrong_binary():
    services = [
        {'name': 'radiod', 'pid': 101, 'expected': '/usr/local/sbin/radiod'},
        {'name': 'wspr-recorder', 'pid': 102, 'expected': '/opt/wspr/bin/wsprd'},
    ]
    resolve = lambda pid: {101: '/usr/local/sbin/radiod.patched',
                           102: '/opt/wspr/bin/wsprd'}[pid]
    out = exec_mismatch(services, resolve)
    assert [m['name'] for m in out] == ['radiod']
    assert out[0]['running'] == '/usr/local/sbin/radiod.patched'
    assert out[0]['expected'] == '/usr/local/sbin/radiod'


def test_exec_mismatch_skips_a_service_with_no_pid():
    """A stopped service is not a wrong binary — it must not be flagged."""
    services = [{'name': 'hf-timestd', 'pid': None,
                'expected': '/opt/timestd/bin/core'}]

    assert exec_mismatch(services, resolve=lambda pid: '/whatever') == []


def test_exec_mismatch_reports_unreadable_proc_as_unknown():
    """The process exited mid-check, or permission was denied — either
    way the check cannot see what ran. Guessing would be worse than
    admitting it, so this must be distinguishable from a real mismatch,
    not silently dropped or silently flagged."""
    def resolve(pid):
        raise OSError(2, 'No such file or directory')

    services = [{'name': 'radiod', 'pid': 999,
                'expected': '/usr/local/sbin/radiod'}]
    out = exec_mismatch(services, resolve)

    assert len(out) == 1
    assert out[0]['status'] == 'unknown'
    assert out[0]['name'] == 'radiod'


def test_exec_mismatch_is_silent_when_running_the_expected_binary():
    services = [{'name': 'radiod', 'pid': 101,
                'expected': '/usr/local/sbin/radiod'}]

    assert exec_mismatch(services, resolve=lambda pid: '/usr/local/sbin/radiod') == []


def test_exec_mismatch_does_not_flag_a_symlink_difference(tmp_path):
    """The deploy tree path and the running /proc/<pid>/exe path may
    legitimately differ by symlink — e.g. expected points through a
    'current' symlink into a versioned install dir. Flagging that is
    noise a real defect would drown in.
    """
    real = tmp_path / 'radiod-1.2.3'
    real.write_text('binary')
    link = tmp_path / 'current'
    link.symlink_to(real)

    services = [{'name': 'radiod', 'pid': 101, 'expected': str(link)}]

    assert exec_mismatch(services, resolve=lambda pid: str(real)) == []


# ── exec mismatch: review findings ──────────────────────────────────
# Review found three gaps: a `resolve` that signals failure by
# RETURNING a falsy value (matching `venv_skew`'s `probe` convention,
# not raising) crashed the whole pass with an uncaught TypeError from
# `os.path.realpath(None)`; the same falsy-return case produced a
# spurious 'mismatch' when it returned '' rather than None; and a
# deleted backing file (`/proc/<pid>/exe` resolves to the path plus the
# literal " (deleted)" suffix, raising nothing) was indistinguishable
# from a genuine wrong-binary swap.

def test_exec_mismatch_treats_a_none_returning_resolve_as_unknown():
    """A `resolve` written to match `venv_skew`'s sibling `probe`
    convention — return a falsy value, don't raise — must not crash the
    whole exec_mismatch() call for every other service."""
    services = [{'name': 'radiod', 'pid': 101,
                'expected': '/usr/local/sbin/radiod'}]

    out = exec_mismatch(services, resolve=lambda pid: None)

    assert out == [{'name': 'radiod', 'status': 'unknown',
                    'expected': '/usr/local/sbin/radiod', 'running': None}]


def test_exec_mismatch_treats_an_empty_string_resolve_as_unknown_not_mismatch():
    """A falsy non-None return ('') must not be compared against
    `expected` as if it were a real path — that produces a spurious
    'mismatch' against a service that may be running exactly right."""
    services = [{'name': 'radiod', 'pid': 101,
                'expected': '/usr/local/sbin/radiod'}]

    out = exec_mismatch(services, resolve=lambda pid: '')

    assert out == [{'name': 'radiod', 'status': 'unknown',
                    'expected': '/usr/local/sbin/radiod', 'running': None}]


def test_exec_mismatch_still_flags_a_genuine_mismatch_alongside_falsy_handling():
    """Guard against a fix for the falsy-return cases swallowing real
    mismatches too."""
    services = [{'name': 'radiod', 'pid': 101,
                'expected': '/usr/local/sbin/radiod'}]

    out = exec_mismatch(services, resolve=lambda pid: '/usr/local/sbin/radiod.patched')

    assert out == [{'name': 'radiod', 'status': 'mismatch',
                    'expected': '/usr/local/sbin/radiod',
                    'running': '/usr/local/sbin/radiod.patched'}]


def test_exec_mismatch_reports_a_deleted_backing_file_distinctly():
    """B3's own relocation-behind-symlinks operation produces exactly
    this: /proc/<pid>/exe for a process whose backing file was replaced
    while it kept running resolves to the path plus a literal
    ' (deleted)' suffix, raising nothing. That is a different situation
    from a wrong-but-present binary (restart to pick up the new file,
    vs investigate a genuine swap) and must not be reported as an
    ordinary 'mismatch'."""
    services = [{'name': 'radiod', 'pid': 101,
                'expected': '/usr/local/sbin/radiod'}]

    out = exec_mismatch(
        services, resolve=lambda pid: '/usr/local/sbin/radiod (deleted)')

    assert out == [{'name': 'radiod', 'status': 'deleted',
                    'expected': '/usr/local/sbin/radiod',
                    'running': '/usr/local/sbin/radiod (deleted)'}]


def test_exec_mismatch_reports_deleted_even_when_the_deleted_path_also_differs():
    """A deleted backing file is reported as 'deleted' regardless of
    whether the (stripped) underlying path also happens to differ from
    `expected` — the deletion itself is the anomaly worth surfacing, and
    guessing whether it's ALSO a wrong-binary swap would be worse than
    just saying 'go look'."""
    services = [{'name': 'radiod', 'pid': 101,
                'expected': '/usr/local/sbin/radiod'}]

    out = exec_mismatch(
        services,
        resolve=lambda pid: '/usr/local/sbin/radiod.patched (deleted)')

    assert out == [{'name': 'radiod', 'status': 'deleted',
                    'expected': '/usr/local/sbin/radiod',
                    'running': '/usr/local/sbin/radiod.patched (deleted)'}]
