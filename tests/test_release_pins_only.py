"""Task 9 (v3.94): every pin comes from the release, never from the station.

Michael, 2026-11-20: "when we align a station with the newest blessed
sigmond version it should bring the code and any compiled binary up to the
latest version.  Any pinning or version specifications should reside in the
base repo and not vary per station."  SIGMA, KFS-MAIN and AC1SN carried
/etc/sigmond/component-pins.toml with ubersdr_wsprd = 8719788..., and
_native_pin let that file win over sigmond's constant, so the 5eeeb53 pin
move would never have reached them.

These tests run the real _native_pin, the real ubersdr builder (compile
stubbed), the real `smd update --latest` and the real `smd align`, dry run
and --apply."""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from sigmond import catalog_prune
from sigmond.drift import builds, overrides
from tests.test_align_enables_units import _drive
from tests.test_align_cli import smd  # the module _drive runs

OLD = "8719788" + "c" * 33          # what the image build wrote on SIGMA


def _today() -> str:
    # Computed at assertion time: a run that crosses UTC midnight must not
    # compare against yesterday's name.
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _retired() -> str:
    return "component-pins.toml.retired-" + _today()


@pytest.fixture
def pin_file(tmp_path, monkeypatch):
    p = tmp_path / "etc" / "component-pins.toml"
    p.parent.mkdir()
    p.write_text(f'[pins]\n"ubersdr_wsprd" = "{OLD}"\n"onion" = "{OLD}"\n'
                 f'"wsjtx" = "{OLD}"\n')
    monkeypatch.setattr(smd, "_COMPONENT_PINS_FILE", p)
    monkeypatch.setattr(smd, "_REMOTE_HEADS", {})
    monkeypatch.delenv("SIGMOND_TRACK_MAIN", raising=False)
    return p


# -- rule 1: the station file never decides a version ----------------------

def test_station_pin_file_never_wins(pin_file, tmp_path):
    assert smd._native_pin("ubersdr_wsprd") == smd._UBERSDR_COMMIT
    assert smd._UBERSDR_COMMIT.startswith("5eeeb53")
    assert smd._native_build_pins() == {
        "onion": smd._ONION_COMMIT, "wsjtx": smd._WSJTX_COMMIT,
        "ubersdr_wsprd": smd._UBERSDR_COMMIT}
    assert smd._expected_build_pins()["ubersdr-decoders"] == {
        "ubersdr_wsprd": smd._UBERSDR_COMMIT}
    # The drift decoders item compares the build with sigmond's pin.
    dec = tmp_path / "dec"
    (dec / "ubersdr").mkdir(parents=True)
    (dec / "ubersdr" / "wsprd").write_text("")
    (dec / "ubersdr" / ".provenance").write_text(f"{OLD} parity=verified\n")
    items = builds.build_items(
        tmp_path, [], enabled={}, decoder_dir=dec,
        native_pins={"ubersdr_wsprd": smd._native_pin("ubersdr_wsprd")})
    assert [(i.subject, i.detail) for i in items] == [
        ("ubersdr-decoders", "UberSDR decoders built from 8719788, pin 5eeeb53")]


def test_track_main_reads_the_remote_never_the_file(pin_file):
    head = "e" * 40

    def fake_run(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, f"{head}\tHEAD\n", "")
    os.environ["SIGMOND_TRACK_MAIN"] = "1"
    try:
        with mock.patch.object(smd, "_run", side_effect=fake_run):
            assert smd._native_pin("ubersdr_wsprd") == head
        smd._REMOTE_HEADS.clear()
        with mock.patch.object(smd, "_run", return_value=subprocess.CompletedProcess(
                [], 128, "", "fatal")), mock.patch.object(smd, "_warn"):
            assert smd._native_pin("ubersdr_wsprd") == smd._UBERSDR_COMMIT
    finally:
        os.environ.pop("SIGMOND_TRACK_MAIN", None)


def test_no_reader_or_writer_of_the_station_file_remains():
    src = (Path(smd.__file__)).read_text()
    for name in ("_read_component_pins", "_record_component_pins",
                 "_pin_if_tracking", "_latest_record_pins"):
        assert name not in src, name


def test_station_pin_file_is_one_drift_item(tmp_path):
    p = tmp_path / "component-pins.toml"
    p.write_text(f'[pins]\n"ubersdr_wsprd" = "{OLD}"\n')
    got = overrides.override_items(
        client_configs={}, catalog_override=tmp_path / "none.toml",
        pins_file=p, coordination=tmp_path / "co.toml")
    assert [(i.kind, i.severity, i.subject) for i in got] == [
        ("override", "drift", "pins:station-file")]
    assert "smd align --apply" in got[0].fix
    assert "release" in got[0].detail


# -- rule 2: nothing writes the file on a station --------------------------

def test_track_main_writes_no_station_file(pin_file, tmp_path):
    pin_file.unlink()
    head = "e" * 40
    src = tmp_path / "ubersdr_wsprd"
    src.mkdir()
    dest = tmp_path / "decoders"
    run_calls = []

    def fake_run(cmd, **kw):
        run_calls.append(list(cmd))
        out = f"{head}\tHEAD\n" if cmd[:2] == ["git", "ls-remote"] else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    with contextlib.ExitStack() as st:
        for p in (
            mock.patch.dict(os.environ, {"SIGMOND_TRACK_MAIN": "1"}),
            mock.patch.object(smd, "_UBERSDR_SRC", src),
            mock.patch.object(smd, "_DECODER_DIR", dest),
            mock.patch.object(smd, "_run", side_effect=fake_run),
            mock.patch.object(smd, "_native_git",
                              return_value=subprocess.CompletedProcess([], 0, "", "")),
            mock.patch.object(smd, "_git_head", return_value=head),
            mock.patch.object(smd, "_ensure_native_build_deps", return_value=True),
            mock.patch.object(smd, "_apt",
                              return_value=subprocess.CompletedProcess([], 0, "", "")),
            mock.patch.object(smd, "_write_build_manifest"),
            mock.patch.object(smd, "_install_root_text"),
            # As root, as the image build runs it: a pin write would land
            # directly, not through _install_root_text.
            mock.patch.object(smd.os, "geteuid", return_value=0),
        ):
            st.enter_context(p)
        st.enter_context(contextlib.redirect_stdout(io.StringIO()))
        assert smd._build_ubersdr_decoders(check=False) is True
    assert not pin_file.exists()
    assert not [c for c in run_calls if str(pin_file) in " ".join(c)]


def _row(name, head, tip, kind="component", **kw):
    r = {"name": name, "head": head, "tip": tip, "behind": 3, "kind": kind,
         "builder": smd._NATIVE_BUILD_OF.get(name, name),
         "installer": False, "build": False, "dirty": False,
         "dir": Path("/nonexistent") / name, "ref": "origin/main",
         "owner": "sigmond", "detached": False}
    r.update(kw)
    return r


@pytest.mark.parametrize("moving", [True, False])
def test_update_latest_writes_no_station_file(pin_file, moving):
    pin_file.unlink()
    a, b = "a" * 40, "b" * 40
    rows = [_row("hf-timestd", a, b if moving else a),
            _row("ubersdr_wsprd", a, b if moving else a, "native")]
    plan = smd._plan_latest(rows)
    assert "pins" not in [k for k, _t, _d in plan]
    args = argparse.Namespace(apply=True, no_fetch=True)
    out = io.StringIO()
    with mock.patch.object(smd, "_refresh_upstreams"), \
         mock.patch.object(smd, "_latest_rows", return_value=rows), \
         mock.patch.object(smd, "_latest_move", return_value=True), \
         mock.patch.object(smd, "_run_native_build_step", return_value=0), \
         mock.patch.object(smd, "_run_smd_child", return_value=0), \
         mock.patch.object(smd, "_record_update_step"), \
         mock.patch.object(smd, "_install_root_text") as root_write, \
         mock.patch.object(smd, "_git_head", return_value="f" * 40), \
         mock.patch.dict(os.environ, {}, clear=False), \
         contextlib.redirect_stdout(out):
        rc = smd._cmd_update_latest(args, Path("/nonexistent"), [])
    assert rc == 0
    assert not pin_file.exists()
    root_write.assert_not_called()
    assert "component-pins" not in out.getvalue()


# -- rule 3: align retires an existing file once ---------------------------

def test_align_retires_station_pin_file(pin_file):
    with mock.patch.object(smd, "_COMPONENT_PINS_FILE", pin_file):
        _calls, dry = _drive(dry=True)
        assert (f"would retire {pin_file}: pins come from the release"
                in dry)
        assert pin_file.exists()

        _calls, out = _drive()
        retired = pin_file.with_name(_retired())
        assert not pin_file.exists()
        assert retired.read_text().startswith("[pins]")
        lines = [ln for ln in out.splitlines() if "component-pins" in ln]
        assert len(lines) == 1 and "retired" in lines[0]

        _calls, again = _drive()
        assert "component-pins" not in again
        _calls, dry2 = _drive(dry=True)
        assert "would retire" not in dry2


def test_align_retires_a_symlink_without_following_it(pin_file, tmp_path):
    target = tmp_path / "elsewhere.toml"
    target.write_text("keep me\n")
    pin_file.unlink()
    pin_file.symlink_to(target)
    with mock.patch.object(smd, "_COMPONENT_PINS_FILE", pin_file):
        _drive()
    assert not os.path.lexists(pin_file)
    assert pin_file.with_name(_retired()).is_symlink()
    assert target.read_text() == "keep me\n"


# -- rule 4: catalog overrides are drift; align prunes the copies ----------

REPO_CAT = ('[client.a]\nrepo = "https://github.com/HamSCI/a"\n'
            '[client.b]\nrepo = "https://github.com/HamSCI/b"\n'
            'description = "b"\n')
OP_CAT = ('[client.a]\nrepo = "https://github.com/HamSCI/a"\n'
          '[client.b]\nrepo = "https://github.com/someone/b-staging"\n'
          'description = "b"\n')


def _cat_items(op, repo):
    return [i for i in overrides.override_items(
        client_configs={}, catalog_override=op, repo_catalog=repo,
        pins_file=op.parent / "none-pins.toml",
        coordination=op.parent / "co.toml") if i.subject.startswith("catalog:")]


def test_catalog_override_is_drift_and_align_prunes(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    op, repo = etc / "catalog.toml", tmp_path / "repo-catalog.toml"
    op.write_text(OP_CAT)
    repo.write_text(REPO_CAT)

    got = _cat_items(op, repo)
    assert [(i.kind, i.severity, i.subject) for i in got] == [
        ("override", "drift", "catalog:a.repo"),
        ("override", "drift", "catalog:b.description"),
        ("override", "drift", "catalog:b.repo")]
    # A copy of the repo default: the prune drops it.
    assert got[0].fix == "smd config catalog-prune"
    assert "copies the repo catalog" in got[0].detail
    # A field that differs: the prune keeps it, so the fix names the
    # operator's own removal.
    assert "someone/b-staging" in got[2].detail
    assert "remove repo for b" in got[2].fix
    assert "catalog-prune keeps" in got[2].fix

    with mock.patch.object(catalog_prune, "OPERATOR_CATALOG", op), \
         mock.patch.object(catalog_prune, "REPO_CATALOG", repo):
        _calls, dry = _drive(dry=True)
        assert f"would prune 2 field(s) that copy the repo catalog from {op}" in dry
        assert op.read_text() == OP_CAT
        _calls, out = _drive()
    assert "pruned 2 field(s)" in out
    # align's prune keeps a dated backup and never overwrites an older one.
    assert (etc / f"catalog.toml.bak-{_today()}").read_text() == OP_CAT
    # The differing repo stays, and stays a drift item; it is never
    # deleted silently.
    assert "someone/b-staging" in op.read_text()
    assert [i.subject for i in _cat_items(op, repo)] == ["catalog:b.repo"]


def test_catalog_prune_never_writes_through_a_symlink(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    real = tmp_path / "real.toml"
    real.write_text(OP_CAT)
    op, repo = etc / "catalog.toml", tmp_path / "repo-catalog.toml"
    op.symlink_to(real)
    repo.write_text(REPO_CAT)
    with pytest.raises(OSError):
        catalog_prune.prune_operator_catalog(op, repo)
    assert real.read_text() == OP_CAT
    # A planted .bak link is replaced, never written through.
    op.unlink()
    op.write_text(OP_CAT)
    victim = tmp_path / "victim"
    victim.write_text("untouched\n")
    (etc / "catalog.toml.bak").symlink_to(victim)
    catalog_prune.prune_operator_catalog(op, repo)
    assert victim.read_text() == "untouched\n"
    assert (etc / "catalog.toml.bak").read_text() == OP_CAT


def test_locally_managed_is_no_catalog_override(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    op, repo = etc / "catalog.toml", tmp_path / "repo-catalog.toml"
    op.write_text('[components]\nlocally_managed = ["ka9q-radio"]\n')
    repo.write_text(REPO_CAT)
    assert _cat_items(op, repo) == []


# -- fix round 1 -------------------------------------------------------------

def test_onion_pin_is_the_onion_the_v393_image_built():
    # Ruling 2026-11-20: the blessed v3.93 image built and tested onion
    # 124c28c; a constant left at de8ea93 would roll SIGMA, KFS-MAIN and
    # AC1SN back once align retires their pin file.
    assert smd._ONION_COMMIT == "124c28c8f74fa73ffa2cc5a3b0273633563be366"


PROFILE_REPO = REPO_CAT + ('[profile.dasi2]\ndescription = "core"\n'
                           'clients = ["a", "b"]\n')
PROFILE_OP = ('[client.a]\nrepo = "https://github.com/HamSCI/a"\n'
              '[profile.dasi2]\ndescription = "core"\n'
              '[profile.mine]\ndescription = "my bundle"\n'
              'clients = ["a"]\n'
              '[components]\nlocally_managed = ["ka9q-radio"]\n')


def test_align_prune_keeps_profiles_and_other_tables(tmp_path):
    import tomllib
    etc = tmp_path / "etc"
    etc.mkdir()
    op, repo = etc / "catalog.toml", tmp_path / "repo-catalog.toml"
    op.write_text(PROFILE_OP)
    repo.write_text(PROFILE_REPO)

    got = _cat_items(op, repo)
    by = {i.subject: i for i in got}
    # A profile field that copies the repo is pruned like a client copy; an
    # operator's own profile is an override item.
    assert by["catalog:profile.dasi2.description"].fix == "smd config catalog-prune"
    assert "replacing the release's catalog" in by["catalog:profile.mine.clients"].detail
    assert not [s for s in by if "components" in s]

    with mock.patch.object(catalog_prune, "OPERATOR_CATALOG", op), \
         mock.patch.object(catalog_prune, "REPO_CATALOG", repo):
        _calls, out = _drive()
    assert "pruned 2 field(s)" in out
    # Every client block was a copy, yet the file stays: other tables remain.
    data = tomllib.loads(op.read_text())
    assert "client" not in data
    assert data["profile"] == {"mine": {"description": "my bundle",
                                        "clients": ["a"]}}
    assert data["components"] == {"locally_managed": ["ka9q-radio"]}


def test_prune_never_unlinks_while_other_tables_remain(tmp_path):
    op, repo = tmp_path / "catalog.toml", tmp_path / "repo-catalog.toml"
    op.write_text('[client.a]\nrepo = "https://github.com/HamSCI/a"\n'
                  '[components]\nlocally_managed = ["ka9q-radio"]\n')
    repo.write_text(REPO_CAT)
    report = catalog_prune.prune_operator_catalog(op, repo)
    assert not report.removed_file
    assert "locally_managed" in op.read_text()


def test_align_prune_backup_is_dated_and_never_overwritten(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    op, repo = etc / "catalog.toml", tmp_path / "repo-catalog.toml"
    op.write_text(OP_CAT)
    repo.write_text(REPO_CAT)
    older = etc / f"catalog.toml.bak-{_today()}"
    older.write_text("an older backup\n")
    with mock.patch.object(catalog_prune, "OPERATOR_CATALOG", op), \
         mock.patch.object(catalog_prune, "REPO_CATALOG", repo):
        _drive()
    assert older.read_text() == "an older backup\n"
    assert (etc / f"catalog.toml.bak-{_today()}.1").read_text() == OP_CAT


def test_missing_repo_catalog_is_one_fault(tmp_path):
    op = tmp_path / "catalog.toml"
    op.write_text(OP_CAT)
    got = overrides.override_items(
        client_configs={}, catalog_override=op,
        repo_catalog=tmp_path / "no-such-repo-catalog.toml",
        pins_file=tmp_path / "none-pins.toml", coordination=tmp_path / "co.toml")
    assert [(i.severity, i.subject) for i in got] == [
        ("fault", str(tmp_path / "no-such-repo-catalog.toml"))]


def test_retire_never_replaces_an_existing_name(tmp_path, monkeypatch):
    from sigmond import safe_write
    src = tmp_path / "component-pins.toml"
    src.write_text("new\n")
    (tmp_path / "x.retired").write_text("old\n")
    # Even when the free-name probe is fooled, the link refuses to replace.
    real_stat = os.stat

    def blind(path, *a, **kw):
        if path == "x.retired":
            raise FileNotFoundError(path)
        return real_stat(path, *a, **kw)
    monkeypatch.setattr(safe_write.os, "stat", blind)
    got = safe_write.retire_nofollow(src, "x.retired")
    assert got == "x.retired.1"
    assert (tmp_path / "x.retired").read_text() == "old\n"
    assert (tmp_path / "x.retired.1").read_text() == "new\n"
    assert not src.exists()
