"""`smd install --release-list PATH` installs the list's commits and pins them.

The image build will run `smd install --yes --release-list .../components.toml`
in place of tracking main.  Every refusal must come before the first change:
an unreadable list, SIGMOND_TRACK_MAIN=1, a component with no row, a native
pin the list and bin/smd disagree on, and above all a checkout someone has
edited.  A dirty checkout, or one holding commits origin does not have, must
never be reset (release-home plan, Review Focus 3).

Git work happens in tmp_path repos; nothing elevates or touches a network.
"""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from sigmond.catalog import CatalogEntry
from sigmond import release_list as rlm

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_release_list", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_release_list", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def _fake_sha(i: int) -> str:
    return f"{i:040x}"


def _components(**over) -> dict:
    comps = {name: _fake_sha(i + 1) for i, name in enumerate(rlm.ROWS)}
    comps.update(smd._native_constants())
    comps.update(over)
    return comps


def _rl(**over) -> rlm.ReleaseList:
    return rlm.ReleaseList(version="v3.94", components=_components(**over))


def _write_list(tmp_path, rl) -> str:
    p = tmp_path / "components.toml"
    p.write_text(rlm.format_list(rl))
    return str(p)


def _client_entry(name):
    return CatalogEntry(name=name, kind="client", description="test",
                        repo=f"https://github.com/test/{name}",
                        install_script=f"/opt/git/sigmond/{name}/install.sh")


def _args(**kw):
    base = dict(client=None, components=None, profile=None, client_dir=None,
                dry_run=False, yes=True, force=False, no_enable=False,
                topology=None, no_pull=False, release_list=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _identity_owner(owner, argv):
    return argv


# --- git fixtures ------------------------------------------------------------

def _git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, check=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t",
                            "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                            "GIT_COMMITTER_EMAIL": "t@t"})
    return r.stdout.strip()


def _origin_with_two_commits(tmp_path, name="ka9q-python"):
    """An origin repo with commits c1 then c2 on main; returns (origin, c1, c2)."""
    origin = tmp_path / "origin" / name
    origin.mkdir(parents=True)
    _git(origin, "init", "-q", "-b", "main")
    (origin / "f.txt").write_text("one\n")
    _git(origin, "add", "f.txt")
    _git(origin, "commit", "-q", "-m", "c1")
    c1 = _git(origin, "rev-parse", "HEAD")
    (origin / "f.txt").write_text("two\n")
    _git(origin, "commit", "-q", "-am", "c2")
    c2 = _git(origin, "rev-parse", "HEAD")
    return origin, c1, c2


def _clone(origin, base, name="ka9q-python"):
    base.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(base / name)],
                   check=True, capture_output=True)
    return base / name


class _Recorder:
    """subprocess.run that records every argv before running it."""

    def __init__(self):
        self.argvs = []

    def __call__(self, argv, *a, **kw):
        self.argvs.append(list(argv))
        return subprocess.run(argv, *a, **kw)

    def ran(self, word):
        return [a for a in self.argvs if word in a]


# --- the catalog at the release ----------------------------------------------

def test_catalog_at_release_sets_pins():
    rl = _rl()
    catalog = {"wspr-recorder": _client_entry("wspr-recorder"),
               "hs-uploader": _client_entry("hs-uploader"),
               "future-client": _client_entry("future-client")}
    out = smd._catalog_at_release(catalog, rl)
    assert out["wspr-recorder"].pin == rl.components["wspr-recorder"]
    assert out["hs-uploader"].pin == rl.components["hs-uploader"]
    assert out["future-client"].pin is None
    # a copy: the caller's catalog keeps its pins
    assert catalog["wspr-recorder"].pin is None


# --- refusals before the first change ----------------------------------------

def _full_suite(args, targets, catalog):
    """Run _install_full_suite with every privileged step patched.  Returns
    (rc, mocks) so a test can see what ran."""
    mocks = {n: mock.Mock(return_value=True) for n in (
        "_do_install_client", "_install_radiod_native",
        "_install_ka9q_web_native", "_install_gmag_webui_native")}
    mocks["_clone_repo"] = mock.Mock(side_effect=AssertionError("cloned"))
    mocks["_rekey_instances"] = mock.Mock()
    mocks["_ensure_sigmond_dirs"] = mock.Mock()
    with mock.patch.object(smd, "_need_root", return_value=False), \
         mock.patch.object(smd, "_netdiag_auto_refresh"), \
         mock.patch.object(smd, "_load_topology",
                           return_value={"smd_bin": Path("/nonexistent/smd")}), \
         mock.patch.object(smd, "load_catalog", return_value=catalog), \
         mock.patch.object(smd, "_classify_install_targets", return_value=targets), \
         mock.patch.object(smd, "_install_smd_itself"), \
         mock.patch.object(smd, "_install_timing_watchdog"), \
         mock.patch.object(smd, "_install_radiod_watchdog"), \
         mock.patch.object(smd, "_install_loopback_multicast_route"), \
         mock.patch.object(smd, "_host_has_local_radiod", return_value=False), \
         mock.patch.object(smd, "_ensure_sigmond_venv", return_value=True), \
         mock.patch.object(smd, "_build_decoders", return_value=True), \
         mock.patch.object(smd, "_post_install_fixups"), \
         mock.patch.object(smd, "_refresh_cli_reference"), \
         mock.patch.multiple(smd, **mocks):
        rc = smd._install_full_suite(args)
    return rc, mocks


def _assert_nothing_changed(mocks):
    for name in ("_do_install_client", "_install_radiod_native",
                 "_install_ka9q_web_native", "_install_gmag_webui_native",
                 "_clone_repo", "_rekey_instances", "_ensure_sigmond_dirs"):
        assert not mocks[name].called, name


def test_unlisted_component_refuses_before_change(tmp_path, capsys):
    entry = _client_entry("future-client")
    targets = smd._InstallTargets()
    targets.catalog_queue.append(entry)
    path = _write_list(tmp_path, _rl())
    with mock.patch.object(smd, "_RELEASE_BASE", tmp_path / "base"):
        rc, mocks = _full_suite(
            _args(components="future-client", release_list=path),
            targets, {"future-client": entry})
    assert rc == 1
    _assert_nothing_changed(mocks)
    assert "future-client" in capsys.readouterr().err


def test_unreadable_list_refuses_before_change(tmp_path, capsys):
    targets = smd._InstallTargets()
    rc, mocks = _full_suite(
        _args(release_list=str(tmp_path / "missing.toml")), targets, {})
    assert rc == 1
    _assert_nothing_changed(mocks)
    err = capsys.readouterr().err
    assert len([l for l in err.splitlines() if l.startswith("ERROR")]) == 1


def test_track_main_conflict_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SIGMOND_TRACK_MAIN", "1")
    path = _write_list(tmp_path, _rl())
    rc, mocks = _full_suite(_args(release_list=path), smd._InstallTargets(), {})
    assert rc == 2
    _assert_nothing_changed(mocks)
    assert ("--release-list and SIGMOND_TRACK_MAIN=1 name different commits; "
            "pick one") in capsys.readouterr().err


def test_native_disagreement_refuses(tmp_path, capsys):
    path = _write_list(tmp_path, _rl(onion="f" * 40))
    rc, mocks = _full_suite(_args(release_list=path), smd._InstallTargets(), {})
    assert rc == 1
    _assert_nothing_changed(mocks)
    assert "list and bin/smd disagree on onion" in capsys.readouterr().err


def test_dirty_checkout_refuses_before_any_change(tmp_path):
    """Review Focus 3: an edited checkout is refused, never reset."""
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    repo = _clone(origin, base)
    (repo / "f.txt").write_text("someone's work\n")
    rec = _Recorder()
    errors = smd._seat_listed_checkouts(
        _rl(**{"ka9q-python": c1}), base, run=rec, as_owner=_identity_owner)
    assert errors and "ka9q-python" in errors[0]
    assert not rec.ran("fetch") and not rec.ran("checkout")
    assert _git(repo, "rev-parse", "HEAD") == c2
    assert (repo / "f.txt").read_text() == "someone's work\n"


def test_checkout_ahead_of_origin_refuses_before_any_change(tmp_path):
    """Review Focus 3, the other half: a local commit origin lacks."""
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    repo = _clone(origin, base)
    (repo / "f.txt").write_text("local\n")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "-am", "local work")
    local = _git(repo, "rev-parse", "HEAD")
    rec = _Recorder()
    errors = smd._seat_listed_checkouts(
        _rl(**{"ka9q-python": c1}), base, run=rec, as_owner=_identity_owner)
    assert errors and "ka9q-python" in errors[0]
    assert not rec.ran("fetch") and not rec.ran("checkout")
    assert _git(repo, "rev-parse", "HEAD") == local


def test_one_dirty_checkout_stops_every_move(tmp_path):
    """The refusal covers the whole run: a clean checkout listed beside a
    dirty one is not moved either."""
    o1, a1, _ = _origin_with_two_commits(tmp_path, "ka9q-python")
    o2, b1, b2 = _origin_with_two_commits(tmp_path, "hs-uploader")
    base = tmp_path / "base"
    _clone(o1, base, "ka9q-python")
    dirty = _clone(o2, base, "hs-uploader")
    (dirty / "f.txt").write_text("edit\n")
    rec = _Recorder()
    errors = smd._seat_listed_checkouts(
        _rl(**{"ka9q-python": a1, "hs-uploader": b1}), base, run=rec,
        as_owner=_identity_owner)
    assert errors
    assert not rec.ran("fetch") and not rec.ran("checkout")


def test_full_suite_with_dirty_checkout_refuses_before_any_change(tmp_path):
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    repo = _clone(origin, base)
    (repo / "f.txt").write_text("someone's work\n")
    entry = _client_entry("hs-uploader")
    targets = smd._InstallTargets()
    targets.catalog_queue.append(entry)
    path = _write_list(tmp_path, _rl(**{"ka9q-python": c1}))
    with mock.patch.object(smd, "_RELEASE_BASE", base):
        rc, mocks = _full_suite(_args(components="hs-uploader", release_list=path),
                                targets, {"hs-uploader": entry})
    assert rc == 1
    _assert_nothing_changed(mocks)
    assert _git(repo, "rev-parse", "HEAD") == c2


def test_seat_moves_clean_existing_checkout(tmp_path):
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    repo = _clone(origin, base)
    assert _git(repo, "rev-parse", "HEAD") == c2
    errors = smd._seat_listed_checkouts(
        _rl(**{"ka9q-python": c1}), base, as_owner=_identity_owner)
    assert errors == []
    assert _git(repo, "rev-parse", "HEAD") == c1
    r = subprocess.run(["git", "-C", str(repo), "symbolic-ref", "-q", "HEAD"],
                       capture_output=True)
    assert r.returncode != 0          # detached


def test_seat_with_no_pull_refuses_a_row_the_checkout_lacks(tmp_path):
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    repo = _clone(origin, base)
    rec = _Recorder()
    errors = smd._seat_listed_checkouts(
        _rl(**{"ka9q-python": "e" * 40}), base, pull=False, run=rec,
        as_owner=_identity_owner)
    assert errors and "ka9q-python" in errors[0]
    assert not rec.ran("fetch") and not rec.ran("checkout")
    assert _git(repo, "rev-parse", "HEAD") == c2


def test_seat_repoints_a_native_checkout_to_the_fork_its_row_lives_in(tmp_path):
    """Review I-1 (2026-11-25): ubersdr_wsprd's row is a commit only Rob's
    fork holds, and an old host's checkout names madpsy as origin.  Seating
    must point origin at sigmond's _UBERSDR_REPO before the fetch, or
    resolve refuses the whole install / bring-up step."""
    madpsy, c1, _ = _origin_with_two_commits(tmp_path, "madpsy")
    fork = tmp_path / "fork"
    subprocess.run(["git", "clone", "-q", str(madpsy), str(fork)],
                   check=True, capture_output=True)
    (fork / "g.txt").write_text("carrier iq\n")
    _git(fork, "add", "g.txt")
    _git(fork, "commit", "-q", "-m", "fork only")
    fork_only = _git(fork, "rev-parse", "HEAD")
    base = tmp_path / "base"
    repo = _clone(madpsy, base, "ubersdr_wsprd")
    _git(repo, "checkout", "-q", c1)
    with mock.patch.object(smd, "_UBERSDR_REPO", str(fork)):
        errors = smd._seat_listed_checkouts(
            _rl(ubersdr_wsprd=fork_only), base, as_owner=_identity_owner)
        assert errors == []
        assert _git(repo, "remote", "get-url", "origin") == str(fork)
        assert _git(repo, "rev-parse", "HEAD") == fork_only
        # Idempotent: a second seat neither repoints nor refuses.
        assert smd._seat_listed_checkouts(
            _rl(ubersdr_wsprd=fork_only), base, as_owner=_identity_owner) == []
    assert _git(repo, "remote", "get-url", "origin") == str(fork)


# --- pins after the install --------------------------------------------------

def test_write_pins_after_install(tmp_path):
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    _clone(origin, base, "ka9q-python")
    _clone(origin, base, "onion")
    rl = _rl(**{"ka9q-python": c2})
    with mock.patch.object(smd, "_native_build_pins",
                           return_value={"onion": c2, "wsjtx": "x",
                                         "ubersdr_wsprd": "y"}):
        errors = smd._write_release_pins(rl, base)
    assert errors == []
    assert (base / "ka9q-python" / ".pin").read_text() == c2 + "\n"
    assert ".pin" in (base / "ka9q-python" / ".git" / "info" / "exclude").read_text()
    assert not (base / "onion" / ".pin").exists()


def test_write_pins_reports_head_mismatch(tmp_path):
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    _clone(origin, base, "ka9q-python")
    errors = smd._write_release_pins(_rl(**{"ka9q-python": c1}), base)
    assert len(errors) == 1 and "ka9q-python" in errors[0]
    assert not (base / "ka9q-python" / ".pin").exists()


# --- radiod at the list's row ------------------------------------------------

def test_radiod_builds_at_list_row(tmp_path):
    row = "c" * 40
    path = _write_list(tmp_path, _rl(**{"ka9q-radio": row}))
    entry = CatalogEntry(name="ka9q-radio", kind="infra", description="t",
                         repo="https://github.com/ka9q/ka9q-radio")
    targets = smd._InstallTargets()
    targets.natives.add("ka9q-radio")
    clone = mock.Mock(side_effect=RuntimeError("stop after clone"))
    with mock.patch.object(smd, "_RELEASE_BASE", tmp_path / "base"), \
         mock.patch.object(smd, "_need_root", return_value=False), \
         mock.patch.object(smd, "_rekey_instances"), \
         mock.patch.object(smd, "_netdiag_auto_refresh"), \
         mock.patch.object(smd, "_load_topology",
                           return_value={"smd_bin": Path("/nonexistent/smd")}), \
         mock.patch.object(smd, "load_catalog", return_value={"ka9q-radio": entry}), \
         mock.patch.object(smd, "_classify_install_targets", return_value=targets), \
         mock.patch.object(smd, "_ensure_sigmond_dirs"), \
         mock.patch.object(smd, "_install_smd_itself"), \
         mock.patch.object(smd, "_install_timing_watchdog"), \
         mock.patch.object(smd, "_install_radiod_watchdog"), \
         mock.patch.object(smd, "_install_loopback_multicast_route"), \
         mock.patch.object(smd, "_host_has_local_radiod", return_value=True), \
         mock.patch.object(smd, "_ensure_sigmond_venv", return_value=True), \
         mock.patch.object(smd, "_install_radiod_deps", return_value=True), \
         mock.patch.object(smd, "_ensure_radio_user", return_value=True), \
         mock.patch.object(smd, "_ensure_fftw_dir", return_value=True), \
         mock.patch.object(smd, "_ka9q_radio_pin", return_value="d" * 40), \
         mock.patch.object(smd, "_refresh_cli_reference"), \
         mock.patch.object(smd, "_clone_repo", clone):
        rc = smd._install_full_suite(_args(components="ka9q-radio",
                                           release_list=path))
    assert rc == 1                    # the clone stopped it
    clone.assert_called_once()
    assert clone.call_args.kwargs["ref"] == row


def test_radiod_without_a_list_keeps_the_compat_pin():
    entry = CatalogEntry(name="ka9q-radio", kind="infra", description="t",
                         repo="https://github.com/ka9q/ka9q-radio")
    clone = mock.Mock(side_effect=RuntimeError("stop after clone"))
    with mock.patch.object(smd, "get_entry", return_value=entry), \
         mock.patch.object(smd, "_install_radiod_deps", return_value=True), \
         mock.patch.object(smd, "_ensure_radio_user", return_value=True), \
         mock.patch.object(smd, "_ensure_fftw_dir", return_value=True), \
         mock.patch.object(smd, "_ka9q_radio_pin", return_value="d" * 40), \
         mock.patch.object(smd, "_clone_repo", clone):
        assert smd._install_radiod_native({"ka9q-radio": entry}) is False
    assert clone.call_args.kwargs["ref"] == "d" * 40


# --- the whole run, in order -------------------------------------------------

def test_listed_install_seats_installs_then_pins(tmp_path):
    """With a list: catalog clients get the row as their pin, the existing
    ka9q-python moves to its row, and after the installs each checkout gets
    its .pin."""
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    repo = _clone(origin, base)
    entry = _client_entry("hs-uploader")
    targets = smd._InstallTargets()
    targets.catalog_queue.append(entry)
    rl = _rl(**{"ka9q-python": c1})
    path = _write_list(tmp_path, rl)
    real_seat = smd._seat_listed_checkouts
    with mock.patch.object(smd, "_RELEASE_BASE", base), \
         mock.patch.object(smd, "_seat_listed_checkouts",
                           lambda rl_, base_, **kw: real_seat(
                               rl_, base_, as_owner=_identity_owner, **kw)):
        rc, mocks = _full_suite(_args(components="hs-uploader", release_list=path),
                                targets, {"hs-uploader": entry})
    call = mocks["_do_install_client"].call_args
    assert call.args[0].pin == rl.components["hs-uploader"]
    assert rc == 0
    assert _git(repo, "rev-parse", "HEAD") == c1
    assert (repo / ".pin").read_text() == c1 + "\n"


# --- `smd install <client> --release-list` -----------------------------------

def _single(args, entry, base):
    install = mock.Mock(return_value=True)
    root = mock.Mock(return_value=False)
    with mock.patch.object(smd, "_RELEASE_BASE", base), \
         mock.patch.object(smd, "load_catalog", return_value={entry.name: entry}), \
         mock.patch("sigmond.preflight.check_requires", return_value=True), \
         mock.patch.object(smd, "_need_root", root), \
         mock.patch.object(smd, "_netdiag_auto_refresh"), \
         mock.patch.object(smd, "_build_decoders", return_value=True), \
         mock.patch.object(smd, "_post_install_fixups"), \
         mock.patch.object(smd, "_enable_after_install"), \
         mock.patch.object(smd, "_refresh_cli_reference"), \
         mock.patch.object(smd, "_do_install_client", install):
        rc = smd._install_single_client(args)
    return rc, install, root


def test_single_client_unlisted_refuses_before_change(tmp_path):
    entry = _client_entry("future-client")
    path = _write_list(tmp_path, _rl())
    rc, install, root = _single(_args(client=entry.name, release_list=path),
                                entry, tmp_path / "base")
    assert rc == 1
    assert not install.called and not root.called


def test_single_client_dirty_checkout_refuses_before_change(tmp_path):
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    repo = _clone(origin, base)
    (repo / "f.txt").write_text("edit\n")
    entry = _client_entry("hs-uploader")
    path = _write_list(tmp_path, _rl(**{"ka9q-python": c1}))
    rc, install, root = _single(_args(client=entry.name, release_list=path),
                                entry, base)
    assert rc == 1
    assert not install.called      # refused after elevation, before any change
    assert _git(repo, "rev-parse", "HEAD") == c2


def test_single_client_installs_at_its_row(tmp_path):
    entry = _client_entry("hs-uploader")
    rl = _rl()
    path = _write_list(tmp_path, rl)
    rc, install, _ = _single(_args(client=entry.name, release_list=path),
                             entry, tmp_path / "base")
    assert rc == 0
    assert install.call_args.args[0].pin == rl.components["hs-uploader"]


# --- the CLI surface ---------------------------------------------------------

def _run_smd(*args):
    env = dict(os.environ, PYTHONPATH=str(REPO / "lib"),
               SIGMOND_NO_VENV_REEXEC="1")
    env.pop("SIGMOND_TRACK_MAIN", None)
    return subprocess.run([sys.executable, str(REPO / "bin" / "smd"), *args],
                          capture_output=True, text=True, env=env, timeout=60)


@pytest.mark.parametrize("verb", [("install",), ("component", "install"),
                                  ("bringup",)])
def test_help_offers_release_list(verb):
    assert "--release-list" in _run_smd(*verb, "--help").stdout


def test_bringup_refuses_an_unreadable_list_before_planning(tmp_path):
    r = _run_smd("bringup", "--dry-run", "--release-list",
                 str(tmp_path / "missing.toml"))
    assert r.returncode == 1
    assert [l for l in r.stderr.splitlines() if l.startswith("ERROR")]
    assert "bringup:" not in r.stdout


def _origin_with_uvlock(tmp_path, name="wspr-recorder"):
    origin = tmp_path / "origin" / name
    origin.mkdir(parents=True)
    _git(origin, "init", "-q", "-b", "main")
    (origin / "uv.lock").write_text("lock\n")
    _git(origin, "add", "uv.lock")
    _git(origin, "commit", "-q", "-m", "c1")
    c1 = _git(origin, "rev-parse", "HEAD")
    (origin / "uv.lock").write_text("lock2\n")
    _git(origin, "commit", "-q", "-am", "c2")
    return origin, c1, _git(origin, "rev-parse", "HEAD")


def test_uvlock_churn_at_the_row_is_not_refused(tmp_path):
    """An install's `uv sync` rewrites uv.lock; the next bring-up install
    step must not refuse that checkout, which needs no move."""
    origin, c1, c2 = _origin_with_uvlock(tmp_path)
    base = tmp_path / "base"
    repo = _clone(origin, base, "wspr-recorder")
    (repo / "uv.lock").write_text("rewritten by uv sync\n")
    rec = _Recorder()
    errors = smd._seat_listed_checkouts(
        _rl(**{"wspr-recorder": c2}), base, run=rec, as_owner=_identity_owner)
    assert errors == []
    assert not rec.ran("fetch") and not rec.ran("checkout")
    assert (repo / "uv.lock").read_text() == "rewritten by uv sync\n"


def test_uvlock_change_on_a_checkout_that_must_move_is_refused(tmp_path):
    origin, c1, c2 = _origin_with_uvlock(tmp_path)
    base = tmp_path / "base"
    repo = _clone(origin, base, "wspr-recorder")
    (repo / "uv.lock").write_text("edited\n")
    rec = _Recorder()
    errors = smd._seat_listed_checkouts(
        _rl(**{"wspr-recorder": c1}), base, run=rec, as_owner=_identity_owner)
    assert errors
    assert not rec.ran("fetch") and not rec.ran("checkout")
    assert (repo / "uv.lock").read_text() == "edited\n"


# --- security round 1: S1 (no link followed as root), S2 (git as owner) ------

@pytest.mark.parametrize("planted", [".pin", ".git/info", ".git/info/exclude"])
def test_symlink_in_the_pin_path_refuses_and_writes_nothing(tmp_path, planted):
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    repo = _clone(origin, base)
    victim_dir = tmp_path / "victim"
    victim_dir.mkdir()
    victim = victim_dir / "exclude"
    victim.write_text("root's file\n")
    target = repo / planted
    if target.is_dir():
        import shutil
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    target.symlink_to(victim_dir if planted == ".git/info" else victim)
    errors = smd._write_release_pins(_rl(**{"ka9q-python": c2}), base)
    assert len(errors) == 1 and "symlink" in errors[0]
    assert victim.read_text() == "root's file\n"
    assert sorted(p.name for p in victim_dir.iterdir()) == ["exclude"]
    assert not (repo / ".pin").is_file() or (repo / ".pin").is_symlink()


def test_pin_written_with_info_missing(tmp_path):
    import shutil
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    repo = _clone(origin, base)
    shutil.rmtree(repo / ".git" / "info")
    assert smd._write_release_pins(_rl(**{"ka9q-python": c2}), base) == []
    assert (repo / ".pin").read_text() == c2 + "\n"
    assert (repo / ".git" / "info" / "exclude").read_text() == ".pin\n"


def test_read_only_queries_run_through_the_owner_path(tmp_path):
    """Every git command in the seat and the pin pass, the reads included,
    goes through as_owner: root never runs repo config (fsmonitor, hooks)
    in a checkout another account owns."""
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    _clone(origin, base)
    through_owner = []

    def as_owner(owner, argv):
        through_owner.append(list(argv))
        return argv

    rec = _Recorder()
    rl = _rl(**{"ka9q-python": c1})
    assert smd._seat_listed_checkouts(rl, base, run=rec, as_owner=as_owner) == []
    assert smd._write_release_pins(rl, base, run=rec, as_owner=as_owner) == []
    git_calls = [a for a in rec.argvs if a and a[0] == "git"]
    assert git_calls and git_calls == through_owner
    for verb in ("status", "rev-parse", "for-each-ref", "fetch", "checkout"):
        assert any(verb in a for a in through_owner), verb


def test_root_refuses_git_in_a_checkout_whose_owner_it_cannot_name(tmp_path):
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    _clone(origin, base)
    rec = _Recorder()
    with mock.patch.object(smd.os, "geteuid", return_value=4242424):
        errors = smd._seat_listed_checkouts(_rl(**{"ka9q-python": c1}), base,
                                            run=rec)
    assert errors and "only root or that owner" in errors[0]
    assert rec.argvs == []


def test_root_runs_git_as_the_owner_by_default(tmp_path):
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    _clone(origin, base)
    seen = []

    def run(argv, *a, **kw):
        seen.append(list(argv))
        return subprocess.run(argv[argv.index("--") + 1:], *a, **kw)

    with mock.patch.object(smd.os, "geteuid", return_value=0):
        smd._seat_listed_checkouts(_rl(**{"ka9q-python": c1}), base,
                                   run=run, check_only=True)
    assert seen and all(a[:2] == ["runuser", "-u"] for a in seen)


# --- security round 2: F1 (owner of the git folder), F2 (no path re-resolve) --

def _stat_with_uid(target: Path, uid: int):
    real = os.stat

    def fake(p, *a, **kw):
        st = real(p, *a, **kw)
        if not a and not kw and Path(p) == target:
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                                   uid, st.st_gid, st.st_size, int(st.st_atime),
                                   int(st.st_mtime), int(st.st_ctime)))
        return st
    return fake


def test_checkout_and_git_folder_with_different_owners_refuse(tmp_path):
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    repo = _clone(origin, base)
    rec = _Recorder()
    with mock.patch.object(smd.os, "stat",
                           _stat_with_uid(repo / ".git", 4242424)):
        errors = smd._seat_listed_checkouts(_rl(**{"ka9q-python": c1}), base,
                                            run=rec, as_owner=_identity_owner)
    assert errors and "different owners" in errors[0]
    assert rec.argvs == []


def _separate_gitdir_clone(tmp_path, origin, base, name="ka9q-python"):
    base.mkdir(parents=True, exist_ok=True)
    gd = tmp_path / "gitdirs" / name
    gd.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", f"--separate-git-dir={gd}",
                    str(origin), str(base / name)], check=True,
                   capture_output=True)
    return base / name, gd


def test_gitdir_file_is_followed_for_the_owner(tmp_path):
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    repo, gd = _separate_gitdir_clone(tmp_path, origin, base)
    assert (repo / ".git").is_file()
    assert smd._release_git_dirs(repo) == [gd]
    rec = _Recorder()
    # The gitdir target, not the .git file, decides: give it a stranger.
    with mock.patch.object(smd.os, "stat", _stat_with_uid(gd, 4242424)):
        errors = smd._seat_listed_checkouts(_rl(**{"ka9q-python": c1}), base,
                                            run=rec, as_owner=_identity_owner)
    assert errors and "different owners" in errors[0] and str(gd) in errors[0]
    assert rec.argvs == []


def test_gitdir_checkout_with_one_owner_seats(tmp_path):
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    repo, _gd = _separate_gitdir_clone(tmp_path, origin, base)
    assert smd._seat_listed_checkouts(_rl(**{"ka9q-python": c1}), base,
                                      as_owner=_identity_owner) == []
    assert _git(repo, "rev-parse", "HEAD") == c1


def test_pin_writes_go_through_the_open_directory_descriptors(tmp_path):
    """F2: exclude and .pin are written with dir_fd=, never by path again."""
    origin, c1, c2 = _origin_with_two_commits(tmp_path)
    base = tmp_path / "base"
    repo = _clone(origin, base)
    from sigmond import safe_write
    seen = []
    real_w, real_r = safe_write.write_owned, safe_write.rewrite_owned

    def w(path, data, **kw):
        seen.append(("write", Path(path).name, kw.get("dir_fd")))
        return real_w(path, data, **kw)

    def r(path, transform, **kw):
        seen.append(("rewrite", Path(path).name, kw.get("dir_fd")))
        return real_r(path, transform, **kw)

    with mock.patch.object(safe_write, "write_owned", w), \
         mock.patch.object(safe_write, "rewrite_owned", r):
        assert smd._write_release_pins(_rl(**{"ka9q-python": c2}), base) == []
    assert [(k, n) for k, n, _ in seen] == [("rewrite", "exclude"), ("write", ".pin")]
    assert all(isinstance(fd, int) for _, _, fd in seen)
    assert (repo / ".pin").read_text() == c2 + "\n"
