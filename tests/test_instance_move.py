"""A reporter change moves an instance (ledger: reporter-rename-duplicates)."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import sigmond.site_profile as sp                              # noqa: E402
import sigmond.instance_move as IM                              # noqa: E402
from sigmond.instance_move import (Existing, Move, Retire, apply_moves,  # noqa: E402
                                   apply_retires, existing_instances,
                                   pending_moves, plan_changes, plan_moves)
from sigmond.instance_rekey import BACKUP_DIR                  # noqa: E402
from sigmond.receivers import Receiver                         # noqa: E402

RADIOD = "n8ur-status.local"


class FakeSystemctl:
    """Records every call.  ``state`` maps a unit to (enabled, active);
    unknown units stand enabled and active.  ``fail`` holds argument
    tuples that return rc 1."""

    def __init__(self):
        self.calls = []
        self.state = {}
        self.fail = set()

    def __call__(self, args):
        self.calls.append(list(args))
        if tuple(args) in self.fail:
            return 1, "boom"
        if args[0] in ("is-enabled", "is-active"):
            en, ac = self.state.get(args[1], (True, True))
            return 0, (("enabled" if en else "disabled") if args[0] == "is-enabled"
                       else ("active" if ac else "inactive"))
        return 0, ""


@pytest.fixture(autouse=True)
def _trust_this_user():
    """Tests run as a normal user; the journal must be owned by "root"."""
    with mock.patch.object(IM, "_trusted_uid", lambda: os.getuid()):
        yield


@pytest.fixture
def fake_systemctl():
    return FakeSystemctl()


@pytest.fixture
def tmp_root(tmp_path):
    (tmp_path / "etc/sigmond/clients").mkdir(parents=True)
    (tmp_path / "var").mkdir()
    return tmp_path


def make_instance(root, client, key, radiod, reporter=None):
    etc = root / "etc"
    (etc / client / "env").mkdir(parents=True, exist_ok=True)
    (etc / client / f"{key}.toml").write_text(
        f'[instance]\nreporter_id = "{reporter or key}"\n')
    (etc / client / "env" / f"{key}.env").write_text(
        f"UPLOAD=1\nWD_RECEIVER_CALL={reporter or key}\n")
    (etc / "sigmond/clients" / f"{client}@{key}.sources.toml").write_text(
        f'selected = ["radiod:{radiod}"]\n')
    st = root / "var/lib" / client / key
    st.mkdir(parents=True, exist_ok=True)
    (st / "cursor").write_text("42\n")
    dropin = etc / "systemd/system" / f"{client}@{key}.service.d"
    dropin.mkdir(parents=True, exist_ok=True)
    (dropin / "o.conf").write_text("[Service]\nNice=5\n")


def _tree(root):
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_reporter_change_moves_instance(tmp_root, fake_systemctl):
    make_instance(tmp_root, "wspr-recorder", "N8UR", RADIOD)
    moves, refused = plan_moves(
        [Receiver("N8UR-SIGMOND", RADIOD)],
        existing_instances("wspr-recorder", tmp_root / "etc", run=fake_systemctl))
    assert refused == [] and moves == [
        Move("wspr-recorder", "N8UR", "N8UR-SIGMOND", "N8UR-SIGMOND", RADIOD)]


def test_apply_leaves_one_instance(tmp_root, fake_systemctl):
    make_instance(tmp_root, "chirp-recorder", "AB4EJ", "ab4ej-status.local")
    apply_moves([Move("chirp-recorder", "AB4EJ", "AB4EJ-RX888", "AB4EJ-RX888",
                      "ab4ej-status.local")],
                etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                run=fake_systemctl)
    assert sorted(p.stem for p in (tmp_root / "etc/chirp-recorder").glob("*.toml")) \
        == ["AB4EJ-RX888"]
    assert fake_systemctl.calls[-3:] == [
        ["disable", "chirp-recorder@AB4EJ.service"],
        ["enable", "chirp-recorder@AB4EJ-RX888.service"],
        ["start", "chirp-recorder@AB4EJ-RX888.service"]]
    assert ["stop", "chirp-recorder@AB4EJ.service"] in fake_systemctl.calls


def test_apply_rewrites_reporter_copies_state_keeps_old(tmp_root, fake_systemctl):
    make_instance(tmp_root, "psk-recorder", "N8UR", RADIOD)
    apply_moves([Move("psk-recorder", "N8UR", "N8UR-SIGMOND", "N8UR-SIGMOND", RADIOD)],
                etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                run=fake_systemctl)
    new = (tmp_root / "etc/psk-recorder/N8UR-SIGMOND.toml").read_text()
    assert 'reporter_id = "N8UR-SIGMOND"' in new and "N8UR\"" not in new
    assert (tmp_root / "etc/psk-recorder/env/N8UR-SIGMOND.env").exists()
    assert (tmp_root / "etc/sigmond/clients/psk-recorder@N8UR-SIGMOND.sources.toml").exists()
    assert (tmp_root / "etc/systemd/system/psk-recorder@N8UR-SIGMOND.service.d/o.conf").exists()
    # state copied, old kept
    assert (tmp_root / "var/lib/psk-recorder/N8UR-SIGMOND/cursor").read_text() == "42\n"
    assert (tmp_root / "var/lib/psk-recorder/N8UR/cursor").read_text() == "42\n"
    # old config kept (renamed), never deleted
    assert 'reporter_id = "N8UR"' in (
        tmp_root / "etc/psk-recorder/N8UR.toml.moved").read_text()


def test_backups_land_in_backup_dir(tmp_root, fake_systemctl):
    make_instance(tmp_root, "psk-recorder", "N8UR", RADIOD)
    apply_moves([Move("psk-recorder", "N8UR", "N8UR-SIGMOND", "N8UR-SIGMOND", RADIOD)],
                etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                run=fake_systemctl)
    root = tmp_root / "var" / BACKUP_DIR[len("var/"):]
    names = {p.name for p in root.rglob("*") if p.is_file()}
    assert {"N8UR.toml", "N8UR.env", "cursor", "o.conf",
            "psk-recorder@N8UR.sources.toml"} <= names


def test_declared_plus_undeclared_on_one_radiod_retires(tmp_root, fake_systemctl):
    for k in ("N8UR", "N8UR-SIGMOND"):
        make_instance(tmp_root, "psk-recorder", k, RADIOD)
    p = plan_changes(
        [Receiver("N8UR-SIGMOND", RADIOD)],
        existing_instances("psk-recorder", tmp_root / "etc", run=fake_systemctl))
    assert p.moves == [] and p.refusals == []
    assert p.retires == [Retire("psk-recorder", "N8UR", RADIOD)]


def test_two_strays_one_target_refuses(tmp_root, fake_systemctl):
    for k in ("N8UR", "AB4EJ"):
        make_instance(tmp_root, "psk-recorder", k, RADIOD)
    moves, refused = plan_moves(
        [Receiver("N8UR-SIGMOND", RADIOD)],
        existing_instances("psk-recorder", tmp_root / "etc", run=fake_systemctl))
    assert moves == [] and "N8UR" in refused[0] and "AB4EJ" in refused[0]


def test_duplicates_on_undeclared_radiod_warn_not_refuse(tmp_root, fake_systemctl):
    for k in ("N8UR", "AB4EJ"):
        make_instance(tmp_root, "hfdl-recorder", k, "other-status.local")
    p = plan_changes(
        [Receiver("N8UR-SIGMOND", RADIOD)],
        existing_instances("hfdl-recorder", tmp_root / "etc", run=fake_systemctl))
    assert p.refusals == [] and p.moves == [] and len(p.warnings) == 1


def test_radiod_match_ignores_case():
    ex = [Existing("wspr-recorder", "N8UR", "N8UR-Status.local")]
    moves, _ = plan_moves([Receiver("N8UR-SIGMOND", RADIOD)], ex)
    assert len(moves) == 1


def test_retire_stops_backs_up_renames_keeps_state(tmp_root, fake_systemctl):
    make_instance(tmp_root, "psk-recorder", "N8UR", RADIOD)
    steps = apply_retires([Retire("psk-recorder", "N8UR", RADIOD)],
                          etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                          run=fake_systemctl)
    assert not (tmp_root / "etc/psk-recorder/N8UR.toml").exists()
    assert (tmp_root / "etc/psk-recorder/N8UR.toml.retired").exists()
    assert (tmp_root / "var/lib/psk-recorder/N8UR/cursor").exists()
    assert ["stop", "psk-recorder@N8UR.service"] in fake_systemctl.calls
    assert ["disable", "psk-recorder@N8UR.service"] in fake_systemctl.calls
    assert not [s for s in steps if s.startswith("ERROR")]
    root = tmp_root / "var" / BACKUP_DIR[len("var/"):]
    assert any(p.name == "N8UR.toml" for p in root.rglob("*"))


def test_reporter_declared_elsewhere_does_not_move():
    ex = [Existing("wspr-recorder", "N8UR", RADIOD)]
    declared = [Receiver("N8UR-SIGMOND", RADIOD),
                Receiver("N8UR", "other-status.local")]
    assert plan_moves(declared, ex) == ([], [])


# ---- receiver apply ----------------------------------------------------

def _load_smd():
    spec = importlib.util.spec_from_loader("smd_mod_move", loader=None)
    smd = importlib.util.module_from_spec(spec)
    smd.__dict__["__file__"] = "bin/smd"
    src = Path(__file__).resolve().parent.parent / "bin" / "smd"
    with mock.patch.dict(os.environ, {"SIGMOND_NO_VENV_REEXEC": "1"}):
        exec(compile(src.read_text(), str(src), "exec"), smd.__dict__)
    return smd


def _apply(smd, root, receivers, run, **kw):
    prof = root / "etc/sigmond/site-profile.toml"
    with mock.patch.object(sp, "SITE_PROFILE_PATH", prof), \
            mock.patch.object(smd, "_sources_bind", return_value=0):
        return smd._receiver_apply(
            receivers, profile_clients=frozenset({"wspr-recorder", "psk-recorder"}),
            etc_root=root / "etc", coord_path=root / "etc/sigmond/coordination.toml",
            profile_path=prof, inventory_rows=[], move_run=run,
            **{'manifest_run': lambda: True, **kw})


def test_receiver_apply_moves_before_adding(tmp_root, fake_systemctl):
    smd = _load_smd()
    make_instance(tmp_root, "wspr-recorder", "N8UR", RADIOD)
    order = []
    smd_bind = lambda *a, **k: (order.append(sorted(
        p.stem for p in (tmp_root / "etc/wspr-recorder").glob("*.toml"))) or 0)
    prof = tmp_root / "etc/sigmond/site-profile.toml"
    with mock.patch.object(sp, "SITE_PROFILE_PATH", prof), \
            mock.patch.object(smd, "_sources_bind", side_effect=smd_bind):
        rc = smd._receiver_apply(
            [Receiver("N8UR-SIGMOND", RADIOD)],
            profile_clients=frozenset({"wspr-recorder"}),
            etc_root=tmp_root / "etc", coord_path=tmp_root / "etc/sigmond/c.toml",
            profile_path=prof, inventory_rows=[], move_run=fake_systemctl)
    assert rc == 0 and order == [["N8UR-SIGMOND"]]


def test_receiver_apply_dry_run_changes_nothing(tmp_root, fake_systemctl, capsys):
    smd = _load_smd()
    make_instance(tmp_root, "wspr-recorder", "N8UR", RADIOD)
    before = _tree(tmp_root)
    rc = _apply(smd, tmp_root, [Receiver("N8UR-SIGMOND", RADIOD)],
                fake_systemctl, dry_run=True)
    assert rc == 0 and _tree(tmp_root) == before and fake_systemctl.calls == []
    out = capsys.readouterr().out
    assert "N8UR" in out and "N8UR-SIGMOND" in out
    assert out.isascii()


def test_receiver_apply_refusal_touches_nothing(tmp_root, fake_systemctl):
    smd = _load_smd()
    for k in ("N8UR", "AB4EJ"):
        make_instance(tmp_root, "psk-recorder", k, RADIOD)
    before = _tree(tmp_root)
    rc = _apply(smd, tmp_root, [Receiver("N8UR-SIGMOND", RADIOD)], fake_systemctl)
    assert rc == 1 and _tree(tmp_root) == before
    assert fake_systemctl.calls == []


# ---- fix round 1 -------------------------------------------------------

@pytest.mark.parametrize("client", ["chirp-recorder", "codar-sounder",
                                    "hfdl-recorder", "superdarn-sounder"])
def test_every_reporter_keyed_client_plans_a_move(tmp_root, fake_systemctl, client):
    smd = _load_smd()
    make_instance(tmp_root, client, "AB4EJ", RADIOD)
    moves, refused = smd._receiver_moves(
        [Receiver("AB4EJ-RX888", RADIOD)],
        tmp_root / "etc", fake_systemctl)
    assert refused == [] and [m.client for m in moves] == [client]


def test_move_never_creates_a_client_with_no_instance(tmp_root, fake_systemctl):
    smd = _load_smd()
    make_instance(tmp_root, "wspr-recorder", "AB4EJ", RADIOD)
    moves, _ = smd._receiver_moves(
        [Receiver("AB4EJ-RX888", RADIOD)],
        tmp_root / "etc", fake_systemctl)
    assert [m.client for m in moves] == ["wspr-recorder"]


def test_disabled_instance_stays_disabled(tmp_root, fake_systemctl):
    make_instance(tmp_root, "codar-sounder", "KFS-OMNI", RADIOD)
    fake_systemctl.state["codar-sounder@KFS-OMNI.service"] = (False, False)
    apply_moves([Move("codar-sounder", "KFS-OMNI", "KFS-MAIN", "KFS-MAIN", RADIOD)],
                etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                run=fake_systemctl)
    verbs = [c[0] for c in fake_systemctl.calls]
    assert "enable" not in verbs and "start" not in verbs
    assert "disable" not in verbs and "stop" not in verbs
    assert (tmp_root / "etc/codar-sounder/KFS-MAIN.toml").exists()


def test_enabled_but_inactive_is_enabled_not_started(tmp_root, fake_systemctl):
    make_instance(tmp_root, "psk-recorder", "N8UR", RADIOD)
    fake_systemctl.state["psk-recorder@N8UR.service"] = (True, False)
    apply_moves([Move("psk-recorder", "N8UR", "N8UR-SIGMOND", "N8UR-SIGMOND", RADIOD)],
                etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                run=fake_systemctl)
    assert ["enable", "psk-recorder@N8UR-SIGMOND.service"] in fake_systemctl.calls
    assert ["start", "psk-recorder@N8UR-SIGMOND.service"] not in fake_systemctl.calls
    assert ["stop", "psk-recorder@N8UR.service"] not in fake_systemctl.calls


def test_moved_env_names_the_new_id(tmp_root, fake_systemctl):
    make_instance(tmp_root, "wspr-recorder", "N8UR", RADIOD)
    env = tmp_root / "etc/wspr-recorder/env/N8UR.env"
    env.write_text("UPLOAD=1\nWD_RECEIVER_CALL=N8UR\nSTATION_REPORTER_ID=N8UR\n"
                   "X_RECEIVER_CALL=SOMEONE\n")
    apply_moves([Move("wspr-recorder", "N8UR", "N8UR-SIGMOND", "N8UR-SIGMOND", RADIOD)],
                etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                run=fake_systemctl)
    new = (tmp_root / "etc/wspr-recorder/env/N8UR-SIGMOND.env").read_text()
    assert "WD_RECEIVER_CALL=N8UR-SIGMOND\n" in new
    assert "STATION_REPORTER_ID=N8UR-SIGMOND\n" in new
    assert "X_RECEIVER_CALL=SOMEONE" in new and "UPLOAD=1" in new


def test_env_slash_form_follows(tmp_root, fake_systemctl):
    make_instance(tmp_root, "wspr-recorder", "AC0G-B1", RADIOD, reporter="AC0G/B1")
    apply_moves([Move("wspr-recorder", "AC0G-B1", "AC0G-B2", "AC0G/B2", RADIOD)],
                etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                run=fake_systemctl)
    new = (tmp_root / "etc/wspr-recorder/env/AC0G-B2.env").read_text()
    assert "WD_RECEIVER_CALL=AC0G/B2\n" in new


def test_leftover_old_id_warns(tmp_root, fake_systemctl):
    make_instance(tmp_root, "wspr-recorder", "N8UR", RADIOD)
    env = tmp_root / "etc/wspr-recorder/env/N8UR.env"
    env.write_text("CUSTOM_TAG=N8UR\n")
    steps = apply_moves(
        [Move("wspr-recorder", "N8UR", "N8UR-SIGMOND", "N8UR-SIGMOND", RADIOD)],
        etc_root=tmp_root / "etc", var_root=tmp_root / "var", run=fake_systemctl)
    assert any(s.startswith("WARN") and "N8UR-SIGMOND.env:1" in s for s in steps)


def test_failure_mid_move_rolls_back(tmp_root, fake_systemctl):
    make_instance(tmp_root, "psk-recorder", "N8UR", RADIOD)
    before = _tree(tmp_root)
    fake_systemctl.fail.add(("enable", "psk-recorder@N8UR-SIGMOND.service"))
    steps = apply_moves(
        [Move("psk-recorder", "N8UR", "N8UR-SIGMOND", "N8UR-SIGMOND", RADIOD)],
        etc_root=tmp_root / "etc", var_root=tmp_root / "var", run=fake_systemctl)
    assert any(s.startswith("ERROR") for s in steps)
    after = {p: b for p, b in _tree(tmp_root).items()
             if "instance-rekey-backup" not in str(p)}
    assert after == before                       # old files back, new copies gone
    assert fake_systemctl.calls[-1] == ["start", "psk-recorder@N8UR.service"]
    assert pending_moves(tmp_root / "var") == []


def test_oserror_mid_copy_rolls_back(tmp_root, fake_systemctl):
    make_instance(tmp_root, "psk-recorder", "N8UR", RADIOD)
    before = _tree(tmp_root)
    with mock.patch("sigmond.instance_move.shutil.copytree",
                    side_effect=OSError("disk full")):
        steps = apply_moves(
            [Move("psk-recorder", "N8UR", "N8UR-SIGMOND", "N8UR-SIGMOND", RADIOD)],
            etc_root=tmp_root / "etc", var_root=tmp_root / "var",
            run=fake_systemctl)
    assert any(s.startswith("ERROR") and "disk full" in s for s in steps)
    assert {p: b for p, b in _tree(tmp_root).items()
            if "instance-rekey-backup" not in str(p)} == before
    assert ["start", "psk-recorder@N8UR.service"] in fake_systemctl.calls


def test_rerun_after_crash_completes(tmp_root, fake_systemctl):
    make_instance(tmp_root, "psk-recorder", "N8UR", RADIOD)
    mv = Move("psk-recorder", "N8UR", "N8UR-SIGMOND", "N8UR-SIGMOND", RADIOD)
    # A crash: the process dies inside daemon-reload (KeyboardInterrupt skips
    # the rollback), leaving the journal and the renamed files behind.
    real = fake_systemctl.__call__

    def dying(args):
        if args == ["daemon-reload"]:
            raise KeyboardInterrupt
        return real(args)
    with pytest.raises(KeyboardInterrupt):
        apply_moves([mv], etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                    run=dying)
    assert pending_moves(tmp_root / "var") == [mv]
    assert (tmp_root / "etc/psk-recorder/N8UR.toml.moved").exists()
    # Re-run: finishes, with the original state (enabled, active) honoured.
    fake_systemctl.calls.clear()
    fake_systemctl.state["psk-recorder@N8UR.service"] = (False, False)  # as the crash left it
    steps = apply_moves(pending_moves(tmp_root / "var"),
                        etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                        run=fake_systemctl)
    assert not [s for s in steps if s.startswith("ERROR")]
    assert ["enable", "psk-recorder@N8UR-SIGMOND.service"] in fake_systemctl.calls
    assert ["start", "psk-recorder@N8UR-SIGMOND.service"] in fake_systemctl.calls
    assert pending_moves(tmp_root / "var") == []
    assert (tmp_root / "etc/psk-recorder/N8UR-SIGMOND.toml").exists()


def test_receiver_apply_fails_nonzero_on_failed_move(tmp_root, fake_systemctl):
    smd = _load_smd()
    make_instance(tmp_root, "wspr-recorder", "N8UR", RADIOD)
    fake_systemctl.fail.add(("enable", "wspr-recorder@N8UR-SIGMOND.service"))
    rc = _apply(smd, tmp_root, [Receiver("N8UR-SIGMOND", RADIOD)], fake_systemctl)
    assert rc == 1


def test_receiver_apply_retires_duplicate_and_shows_it_dry(tmp_root, fake_systemctl, capsys):
    smd = _load_smd()
    for k in ("N8UR", "N8UR-SIGMOND"):
        make_instance(tmp_root, "psk-recorder", k, RADIOD)
    before = _tree(tmp_root)
    assert _apply(smd, tmp_root, [Receiver("N8UR-SIGMOND", RADIOD)],
                  fake_systemctl, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "retire" in out and "psk-recorder@N8UR" in out and out.isascii()
    assert _tree(tmp_root) == before and fake_systemctl.calls == []
    assert _apply(smd, tmp_root, [Receiver("N8UR-SIGMOND", RADIOD)],
                  fake_systemctl) == 0
    assert (tmp_root / "etc/psk-recorder/N8UR.toml.retired").exists()


def test_receiver_apply_rerenders_manifest_after_a_move(tmp_root, fake_systemctl):
    smd = _load_smd()
    make_instance(tmp_root, "wspr-recorder", "N8UR", RADIOD)
    calls = []
    assert _apply(smd, tmp_root, [Receiver("N8UR-SIGMOND", RADIOD)],
                  fake_systemctl, manifest_run=lambda: calls.append(1) or True) == 0
    assert calls == [1]
    calls.clear()                                   # nothing left to move
    _apply(smd, tmp_root, [Receiver("N8UR-SIGMOND", RADIOD)], fake_systemctl,
           manifest_run=lambda: calls.append(1) or True)
    assert calls == []


# ---- fix round 2 -------------------------------------------------------

def test_failed_move_skips_bind_and_next_apply_moves_again(tmp_root, fake_systemctl):
    smd = _load_smd()
    make_instance(tmp_root, "wspr-recorder", "N8UR", RADIOD)
    fake_systemctl.fail.add(("enable", "wspr-recorder@N8UR-SIGMOND.service"))
    prof = tmp_root / "etc/sigmond/site-profile.toml"
    with mock.patch.object(sp, "SITE_PROFILE_PATH", prof), \
            mock.patch.object(smd, "_sources_bind", return_value=0) as sb:
        rc = smd._receiver_apply(
            [Receiver("N8UR-SIGMOND", RADIOD)],
            profile_clients=frozenset({"wspr-recorder"}),
            etc_root=tmp_root / "etc", coord_path=tmp_root / "etc/sigmond/c.toml",
            profile_path=prof, inventory_rows=[], move_run=fake_systemctl,
            manifest_run=lambda: True)
    assert rc == 1
    assert all("wspr-recorder" not in a[2] for a, k in sb.call_args_list)
    names = sorted(p.name for p in (tmp_root / "etc/wspr-recorder").glob("*.toml*"))
    assert names == ["N8UR.toml"]
    fake_systemctl.fail.clear()
    assert _apply(smd, tmp_root, [Receiver("N8UR-SIGMOND", RADIOD)],
                  fake_systemctl) == 0
    assert (tmp_root / "etc/wspr-recorder/N8UR-SIGMOND.toml").exists()


def test_copies_keep_owner_and_mode(tmp_root, fake_systemctl):
    make_instance(tmp_root, "chirp-recorder", "AB4EJ", RADIOD)
    st = tmp_root / "var/lib/chirp-recorder/AB4EJ"
    (st / "sub").mkdir()
    (st / "sub/f.dat").write_text("x")
    os.chmod(st / "sub/f.dat", 0o640)
    calls = []
    with mock.patch.object(IM, "_geteuid", lambda: 0), \
            mock.patch.object(IM, "_chown", lambda p, u, g: calls.append((Path(p), u, g))), \
            mock.patch.object(IM, "_fchown", lambda fd, u, g: calls.append(
                (Path(os.readlink(f"/proc/self/fd/{fd}")), u, g))):
        apply_moves([Move("chirp-recorder", "AB4EJ", "AB4EJ-RX888", "AB4EJ-RX888", RADIOD)],
                    etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                    run=fake_systemctl)
    new = tmp_root / "var/lib/chirp-recorder/AB4EJ-RX888"
    got = {p for p, u, g in calls}
    assert {new, new / "sub", new / "sub/f.dat", new / "cursor"} <= got
    for path, u, g in calls:
        if st.parent / "AB4EJ-RX888" in path.parents or path == new:
            src = st / path.relative_to(new)
            assert (u, g) == (src.stat().st_uid, src.stat().st_gid)
    assert (new / "sub/f.dat").stat().st_mode & 0o777 == 0o640


def _journal_for(tmp_root, move, **extra):
    d = tmp_root / "var" / IM.JOURNAL_DIR
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    body = {"move": move, "was_enabled": True, "was_active": True,
            "stamp": "20260101T000000Z", "fresh": []}
    body.update(extra)
    (d / "x.json").write_text(json.dumps(body))
    os.chmod(d / "x.json", 0o600)
    return d


def test_journal_with_dotdot_key_is_refused(tmp_root, fake_systemctl):
    victim = tmp_root / "victim"
    victim.mkdir()
    (victim / "keep").write_text("1")
    _journal_for(tmp_root, {"client": "psk-recorder", "old_key": "N8UR",
                            "new_key": "../../victim", "new_reporter_id": "X",
                            "radiod": RADIOD})
    errs = []
    assert pending_moves(tmp_root / "var", errors=errs) == []
    assert errs and "journal" in errs[0]
    assert (victim / "keep").exists()


def test_journal_with_unknown_client_is_refused(tmp_root):
    _journal_for(tmp_root, {"client": "sshd", "old_key": "A", "new_key": "B",
                            "new_reporter_id": "B", "radiod": RADIOD})
    errs = []
    assert pending_moves(tmp_root / "var", errors=errs) == [] and errs


def test_journal_outside_paths_are_never_deleted(tmp_root, fake_systemctl):
    make_instance(tmp_root, "psk-recorder", "N8UR", RADIOD)
    victim = tmp_root / "victim"
    victim.mkdir()
    (victim / "keep").write_text("1")
    mv = {"client": "psk-recorder", "old_key": "N8UR", "new_key": "N8UR-SIGMOND",
          "new_reporter_id": "N8UR-SIGMOND", "radiod": RADIOD}
    (tmp_root / "var" / IM.JOURNAL_DIR).mkdir(parents=True, exist_ok=True)
    d = _journal_for(tmp_root, mv, fresh=[str(victim)])
    (d / "x.json").rename(d / "psk-recorder@N8UR.json")
    fake_systemctl.fail.add(("enable", "psk-recorder@N8UR-SIGMOND.service"))
    apply_moves([Move(**mv)], etc_root=tmp_root / "etc",
                var_root=tmp_root / "var", run=fake_systemctl)
    assert (victim / "keep").exists()


def test_journal_not_owned_by_root_is_refused(tmp_root, fake_systemctl):
    make_instance(tmp_root, "psk-recorder", "N8UR", RADIOD)
    mv = {"client": "psk-recorder", "old_key": "N8UR", "new_key": "N8UR-SIGMOND",
          "new_reporter_id": "N8UR-SIGMOND", "radiod": RADIOD}
    d = _journal_for(tmp_root, mv)
    (d / "x.json").rename(d / "psk-recorder@N8UR.json")
    before = _tree(tmp_root)
    with mock.patch.object(IM, "_trusted_uid", lambda: os.getuid() + 1):
        errs = []
        assert pending_moves(tmp_root / "var", errors=errs) == [] and errs
        steps = apply_moves([Move(**mv)], etc_root=tmp_root / "etc",
                            var_root=tmp_root / "var", run=fake_systemctl)
    assert any(s.startswith("ERROR") for s in steps)
    assert _tree(tmp_root) == before and fake_systemctl.calls == []


def test_journal_dir_writable_by_group_is_refused(tmp_root):
    mv = {"client": "psk-recorder", "old_key": "N8UR", "new_key": "N8UR-SIGMOND",
          "new_reporter_id": "N8UR-SIGMOND", "radiod": RADIOD}
    d = _journal_for(tmp_root, mv)
    os.chmod(d, 0o770)
    errs = []
    assert pending_moves(tmp_root / "var", errors=errs) == [] and errs


def test_journal_dir_is_created_private(tmp_root, fake_systemctl):
    make_instance(tmp_root, "psk-recorder", "N8UR", RADIOD)
    seen = []
    real = fake_systemctl.__call__

    def spy(args):
        d = tmp_root / "var" / IM.JOURNAL_DIR
        if d.exists():
            seen.append(d.stat().st_mode & 0o777)
        return real(args)
    apply_moves([Move("psk-recorder", "N8UR", "N8UR-SIGMOND", "N8UR-SIGMOND", RADIOD)],
                etc_root=tmp_root / "etc", var_root=tmp_root / "var", run=spy)
    assert seen and set(seen) == {0o700}


def test_activating_counts_as_active(tmp_root, fake_systemctl):
    make_instance(tmp_root, "psk-recorder", "N8UR", RADIOD)
    real = fake_systemctl.__call__

    def act(args):
        if args == ["is-active", "psk-recorder@N8UR.service"]:
            fake_systemctl.calls.append(list(args))
            return 3, "activating"
        return real(args)
    apply_moves([Move("psk-recorder", "N8UR", "N8UR-SIGMOND", "N8UR-SIGMOND", RADIOD)],
                etc_root=tmp_root / "etc", var_root=tmp_root / "var", run=act)
    assert ["start", "psk-recorder@N8UR-SIGMOND.service"] in fake_systemctl.calls


def test_retire_with_nothing_to_rename_prints_no_retired_line(tmp_root, fake_systemctl):
    steps = apply_retires([Retire("psk-recorder", "GONE", RADIOD)],
                          etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                          run=fake_systemctl)
    assert not [s for s in steps if s.startswith("retired")]


def test_resumed_move_is_not_also_retired(tmp_root, fake_systemctl, capsys):
    smd = _load_smd()
    make_instance(tmp_root, "psk-recorder", "N8UR", RADIOD)
    make_instance(tmp_root, "psk-recorder", "N8UR-SIGMOND", RADIOD)
    mv = Move("psk-recorder", "N8UR", "N8UR-SIGMOND", "N8UR-SIGMOND", RADIOD)
    d = _journal_for(tmp_root, mv.__dict__)
    (d / "x.json").rename(d / "psk-recorder@N8UR.json")
    assert _apply(smd, tmp_root, [Receiver("N8UR-SIGMOND", RADIOD)],
                  fake_systemctl) == 0
    assert "retire" not in capsys.readouterr().out


# ---- fix round 3 -------------------------------------------------------

def _record_chowns():
    calls = []
    return calls, (mock.patch.object(IM, "_geteuid", lambda: 0),
                   mock.patch.object(IM, "_chown",
                                     lambda p, u, g: calls.append(Path(p))),
                   mock.patch.object(IM, "_fchown", lambda fd, u, g: calls.append(
                       Path(os.readlink(f"/proc/self/fd/{fd}")))))


def test_symlink_in_state_dir_is_copied_as_a_link(tmp_root, fake_systemctl):
    make_instance(tmp_root, "chirp-recorder", "AB4EJ", RADIOD)
    outside = tmp_root / "outside.txt"
    outside.write_text("secret\n")
    before = (outside.stat().st_uid, outside.stat().st_gid, outside.read_text())
    st = tmp_root / "var/lib/chirp-recorder/AB4EJ"
    os.symlink(outside, st / "link")
    os.symlink(tmp_root / "etc", st / "dirlink")
    calls, (a, b, c) = _record_chowns()
    with a, b, c:
        steps = apply_moves(
            [Move("chirp-recorder", "AB4EJ", "AB4EJ-RX888", "AB4EJ-RX888", RADIOD)],
            etc_root=tmp_root / "etc", var_root=tmp_root / "var",
            run=fake_systemctl)
    assert not [s for s in steps if s.startswith("ERROR")]
    new = tmp_root / "var/lib/chirp-recorder/AB4EJ-RX888"
    assert os.path.islink(new / "link") and os.readlink(new / "link") == str(outside)
    assert os.path.islink(new / "dirlink")
    assert outside not in calls and tmp_root / "etc" not in calls
    assert (outside.stat().st_uid, outside.stat().st_gid, outside.read_text()) == before
    assert not any(str(p).startswith(str(outside)) for p in calls)


def test_preexisting_symlink_at_destination_aborts(tmp_root, fake_systemctl):
    make_instance(tmp_root, "chirp-recorder", "AB4EJ", RADIOD)
    victim = tmp_root / "victim"
    victim.mkdir()
    (victim / "keep").write_text("1")
    dst = tmp_root / "var/lib/chirp-recorder/AB4EJ-RX888"
    os.symlink(victim, dst)
    before = {p: b for p, b in _tree(tmp_root).items()}
    steps = apply_moves(
        [Move("chirp-recorder", "AB4EJ", "AB4EJ-RX888", "AB4EJ-RX888", RADIOD)],
        etc_root=tmp_root / "etc", var_root=tmp_root / "var", run=fake_systemctl)
    assert any(s.startswith("ERROR") for s in steps)
    assert os.path.islink(dst) and os.readlink(dst) == str(victim)
    assert (victim / "keep").read_text() == "1"
    assert sorted(os.listdir(victim)) == ["keep"]
    assert {p: b for p, b in _tree(tmp_root).items()
            if "instance-rekey-backup" not in str(p)} == before
    assert (tmp_root / "etc/chirp-recorder/AB4EJ.toml").exists()


def test_symlink_inside_destination_tree_aborts(tmp_root, fake_systemctl):
    make_instance(tmp_root, "chirp-recorder", "AB4EJ", RADIOD)
    st = tmp_root / "var/lib/chirp-recorder/AB4EJ"
    (st / "sub").mkdir()
    (st / "sub/f").write_text("x")
    # a failing step mid-tree leaves nothing behind
    with mock.patch.object(IM, "_copy_file", side_effect=[None, FileExistsError("x")]):
        steps = apply_moves(
            [Move("chirp-recorder", "AB4EJ", "AB4EJ-RX888", "AB4EJ-RX888", RADIOD)],
            etc_root=tmp_root / "etc", var_root=tmp_root / "var", run=fake_systemctl)
    assert any(s.startswith("ERROR") for s in steps)
    assert not (tmp_root / "var/lib/chirp-recorder/AB4EJ-RX888").exists()


def test_backup_does_not_follow_symlinks(tmp_root, fake_systemctl):
    make_instance(tmp_root, "chirp-recorder", "AB4EJ", RADIOD)
    outside = tmp_root / "outside.txt"
    outside.write_text("secret\n")
    os.symlink(outside, tmp_root / "var/lib/chirp-recorder/AB4EJ/link")
    apply_moves([Move("chirp-recorder", "AB4EJ", "AB4EJ-RX888", "AB4EJ-RX888", RADIOD)],
                etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                run=fake_systemctl)
    root = tmp_root / "var" / BACKUP_DIR[len("var/"):]
    links = [p for p in root.rglob("link")]
    assert links and all(p.is_symlink() for p in links)


def test_legacy_equals_key_moves_and_uses_escaped_unit(tmp_root, fake_systemctl):
    make_instance(tmp_root, "meteor-scatter", "AC0G=B4", RADIOD)
    steps = apply_moves(
        [Move("meteor-scatter", "AC0G=B4", "AC0G-B5", "AC0G/B5", RADIOD)],
        etc_root=tmp_root / "etc", var_root=tmp_root / "var", run=fake_systemctl)
    assert not [s for s in steps if s.startswith("ERROR")]
    assert (tmp_root / "etc/meteor-scatter/AC0G-B5.toml").exists()
    assert ["stop", "meteor-scatter@AC0G\\x3dB4.service"] in fake_systemctl.calls


def test_journal_keeps_the_strict_grammar_for_equals(tmp_root):
    _journal_for(tmp_root, {"client": "meteor-scatter", "old_key": "AC0G=B4",
                            "new_key": "AC0G-B4", "new_reporter_id": "AC0G/B4",
                            "radiod": RADIOD})
    errs = []
    assert pending_moves(tmp_root / "var", errors=errs) == [] and errs


def test_refused_key_names_instance_migrate(tmp_root, fake_systemctl):
    steps = apply_moves([Move("psk-recorder", "bad/key", "N8UR", "N8UR", RADIOD)],
                        etc_root=tmp_root / "etc", var_root=tmp_root / "var",
                        run=fake_systemctl)
    assert any("instance migrate" in s for s in steps)
