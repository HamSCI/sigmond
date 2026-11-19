import os
import stat
from datetime import datetime, timezone

import pytest

from sigmond.drift import units


def _comp(base, name, toml):
    d = base / name
    d.mkdir(parents=True)
    (d / "deploy.toml").write_text(toml)


@pytest.fixture
def env(tmp_path):
    base = tmp_path / "git"
    repo = tmp_path / "sigmond"
    (repo / "systemd").mkdir(parents=True)
    ud = tmp_path / "units"
    ud.mkdir()
    return base, repo, ud


def _items(env, enabled, is_enabled=lambda n: True):
    base, repo, ud = env
    return units.unit_items(base, enabled, sigmond_repo=repo, unit_dir=ud,
                            is_enabled=is_enabled)


def test_missing_declared_unit_is_fault(env):
    base, _, ud = env
    _comp(base, "foo", '[systemd]\nunits = ["foo.service"]\n'
                       'templated_units = ["bar@.service"]\n')
    (ud / "bar@.service").write_text("x")  # template present: fine
    got = _items(env, {"foo": True})
    assert [(i.kind, i.severity, i.subject, i.fix) for i in got] == [
        ("unit", "fault", "foo.service", "smd align --apply")]
    (ud / "foo.service").symlink_to(base / "nowhere")  # dangling symlink counts
    assert _items(env, {"foo": True}) == []


def test_declared_timer_not_enabled_is_fault(env):
    base, _, ud = env
    _comp(base, "foo", '[systemd]\nunits = ["foo.timer"]\n')
    (ud / "foo.timer").write_text("x")
    got = _items(env, {"foo": True}, is_enabled=lambda n: False)
    assert [(i.severity, i.subject, i.fix) for i in got] == [
        ("fault", "foo.timer", "smd enable foo")]
    assert _items(env, {"foo": True}) == []


def test_own_unit_missing_or_changed_is_fault(env):
    _, repo, ud = env
    for n in ("sigmond-a.service", "sigmond-b.timer", "sigmond-c.path"):
        (repo / "systemd" / n).write_text("new")
    (repo / "systemd" / "sigmond-x.conf").write_text("ignored")
    (ud / "sigmond-b.timer").write_text("old")
    (ud / "sigmond-c.path").write_text("new")
    got = _items(env, {}, is_enabled=lambda n: False)
    assert sorted((i.subject, i.severity, i.fix) for i in got) == [
        ("sigmond-a.service", "fault", "smd align --apply"),
        ("sigmond-b.timer", "fault", "smd align --apply")]


def test_unit_for_disabled_component_is_drift(env):
    base, _, ud = env
    _comp(base, "foo", '[systemd]\nunits = ["foo.service", "foo2.service"]\n')
    (ud / "foo.service").write_text("x")
    (ud / "foo2.service").write_text("x")
    got = _items(env, {"foo": False}, is_enabled=lambda n: n == "foo.service")
    assert [(i.severity, i.subject, i.fix) for i in got] == [
        ("drift", "foo.service",
         "smd disable foo (stops and disables its units)")]


NOW = datetime(2026, 11, 19, 3, 4, 5, tzinfo=timezone.utc)


def _install(env, **kw):
    _, repo, ud = env
    kw.setdefault("exceptions_path", ud.parent / "no-exceptions.toml")
    kw.setdefault("now", NOW)
    said = []
    wrote = units.install_own_units(repo, ud, say=said.append, **kw)
    return wrote, said


def test_install_own_units_copies_changed_only(env):
    _, repo, ud = env
    for n, c in (("sigmond-a.service", "A"), ("sigmond-b.timer", "B"),
                 ("sigmond-c.path", "C")):
        (repo / "systemd" / n).write_text(c)
    (ud / "sigmond-a.service").write_text("A")
    os.chmod(ud / "sigmond-a.service", 0o600)
    (ud / "sigmond-b.timer").write_text("old")
    wrote, said = _install(env, run=lambda *a, **k: 1 / 0)
    assert wrote == ["sigmond-b.timer", "sigmond-c.path"]
    assert (ud / "sigmond-b.timer").read_text() == "B"
    assert (ud / "sigmond-c.path").read_text() == "C"
    for n in wrote:
        assert stat.S_IMODE((ud / n).stat().st_mode) == 0o644
    # identical file untouched, no temp litter; the replaced one kept
    assert stat.S_IMODE((ud / "sigmond-a.service").stat().st_mode) == 0o600
    assert sorted(p.name for p in ud.iterdir()) == [
        "sigmond-a.service", "sigmond-b.timer",
        "sigmond-b.timer.bak-drift-20261119T030405Z", "sigmond-c.path"]
    assert _install(env)[0] == []


def test_replaced_unit_leaves_a_backup_and_one_line(env):
    _, repo, ud = env
    (repo / "systemd" / "sigmond-w.timer").write_text("release")
    (ud / "sigmond-w.timer").write_text("hand edit")
    wrote, said = _install(env)
    assert wrote == ["sigmond-w.timer"]
    bak = ud / "sigmond-w.timer.bak-drift-20261119T030405Z"
    assert bak.read_text() == "hand edit"
    assert said == [f"  sigmond-units: replaced sigmond-w.timer; the old "
                    f"copy is {bak}"]
    assert all(line.isascii() for line in said)


def _exc_file(ud, key, kind, until):
    p = ud.parent / "exc.toml"
    p.write_text(f'[[exception]]\nkey = "{key}"\nreason = "B4 test"\n'
                 f'by = "rob"\nkind = "{kind}"\nstart = 2026-11-18\n'
                 f'until = {until}\n')
    return p


@pytest.mark.parametrize("kind", ["test", "standing"])
def test_excepted_hand_edited_unit_is_untouched(env, kind):
    _, repo, ud = env
    (repo / "systemd" / "sigmond-radiod-watchdog.timer").write_text("release")
    (repo / "systemd" / "sigmond-new.service").write_text("N")
    (ud / "sigmond-radiod-watchdog.timer").write_text("rob's edit")
    exc = _exc_file(ud, "unit:sigmond-radiod-watchdog.timer", kind,
                    "2026-11-20")
    wrote, said = _install(env, exceptions_path=exc)
    assert wrote == ["sigmond-new.service"]
    assert (ud / "sigmond-radiod-watchdog.timer").read_text() == "rob's edit"
    assert not list(ud.glob("*.bak-drift-*"))
    assert said == ["  sigmond-units: left sigmond-radiod-watchdog.timer as "
                    "it is: a drift exception covers "
                    "unit:sigmond-radiod-watchdog.timer"]


def test_excepted_missing_unit_is_not_installed(env):
    _, repo, ud = env
    (repo / "systemd" / "sigmond-x.service").write_text("X")
    exc = _exc_file(ud, "unit:sigmond-x.service", "standing", "2026-12-01")
    assert _install(env, exceptions_path=exc)[0] == []
    assert not (ud / "sigmond-x.service").exists()


def test_an_ended_test_is_restored(env):
    _, repo, ud = env
    (repo / "systemd" / "sigmond-w.timer").write_text("release")
    (ud / "sigmond-w.timer").write_text("hand edit")
    exc = _exc_file(ud, "unit:sigmond-w.timer", "test", "2026-11-18")
    assert _install(env, exceptions_path=exc)[0] == ["sigmond-w.timer"]
    assert (ud / "sigmond-w.timer").read_text() == "release"


def test_unreadable_exceptions_file_replaces_nothing(env):
    _, repo, ud = env
    (repo / "systemd" / "sigmond-w.timer").write_text("release")
    (repo / "systemd" / "sigmond-new.service").write_text("N")
    (ud / "sigmond-w.timer").write_text("hand edit")
    exc = ud.parent / "exc.toml"
    exc.write_bytes(b"\xff\xfe not utf-8")
    wrote, said = _install(env, exceptions_path=exc)
    assert wrote == ["sigmond-new.service"]       # missing ones still land
    assert (ud / "sigmond-w.timer").read_text() == "hand edit"
    assert len(said) == 1 and "no installed unit is replaced" in said[0]


def test_templated_timer_needs_no_enablement(env):
    base, _, ud = env
    _comp(base, "foo", '[systemd]\ntemplated_units = ["foo-x@.timer"]\n')
    (ud / "foo-x@.timer").write_text("x")
    assert _items(env, {"foo": True}, is_enabled=lambda n: False) == []
    assert _items(env, {"foo": False}, is_enabled=lambda n: True) == []
