"""A reporter change moves an instance (ledger: reporter-rename-duplicates)."""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import sigmond.site_profile as sp                              # noqa: E402
from sigmond.instance_move import (Existing, Move, apply_moves,  # noqa: E402
                                   existing_instances, plan_moves)
from sigmond.instance_rekey import BACKUP_DIR                  # noqa: E402
from sigmond.receivers import Receiver                         # noqa: E402

RADIOD = "n8ur-status.local"


class FakeSystemctl:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        return 0, ""


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
    (etc / client / "env" / f"{key}.env").write_text("UPLOAD=1\n")
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
    assert fake_systemctl.calls[-2:] == [
        ["disable", "--now", "chirp-recorder@AB4EJ.service"],
        ["enable", "--now", "chirp-recorder@AB4EJ-RX888.service"]]


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


def test_two_on_one_radiod_refuses(tmp_root, fake_systemctl):
    for k in ("N8UR", "N8UR-SIGMOND"):
        make_instance(tmp_root, "psk-recorder", k, RADIOD)
    moves, refused = plan_moves(
        [Receiver("N8UR-SIGMOND", RADIOD)],
        existing_instances("psk-recorder", tmp_root / "etc", run=fake_systemctl))
    assert moves == [] and "N8UR" in refused[0] and "N8UR-SIGMOND" in refused[0]


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
            profile_path=prof, inventory_rows=[], move_run=run, **kw)


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
    for k in ("N8UR", "N8UR-SIGMOND"):
        make_instance(tmp_root, "psk-recorder", k, RADIOD)
    before = _tree(tmp_root)
    with mock.patch.object(smd, "_sources_bind", return_value=0) as sb:
        rc = _apply(smd, tmp_root, [Receiver("N8UR-SIGMOND", RADIOD)],
                    fake_systemctl)
    assert rc == 1 and _tree(tmp_root) == before
    assert fake_systemctl.calls == []
