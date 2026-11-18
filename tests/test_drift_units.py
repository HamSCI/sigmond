import os
import stat

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


def test_install_own_units_copies_changed_only(env):
    _, repo, ud = env
    for n, c in (("sigmond-a.service", "A"), ("sigmond-b.timer", "B"),
                 ("sigmond-c.path", "C")):
        (repo / "systemd" / n).write_text(c)
    (ud / "sigmond-a.service").write_text("A")
    os.chmod(ud / "sigmond-a.service", 0o600)
    (ud / "sigmond-b.timer").write_text("old")
    wrote = units.install_own_units(repo, ud, run=lambda *a, **k: 1 / 0)
    assert wrote == ["sigmond-b.timer", "sigmond-c.path"]
    assert (ud / "sigmond-b.timer").read_text() == "B"
    assert (ud / "sigmond-c.path").read_text() == "C"
    for n in wrote:
        assert stat.S_IMODE((ud / n).stat().st_mode) == 0o644
    # identical file untouched, no temp litter
    assert stat.S_IMODE((ud / "sigmond-a.service").stat().st_mode) == 0o600
    assert sorted(p.name for p in ud.iterdir()) == [
        "sigmond-a.service", "sigmond-b.timer", "sigmond-c.path"]
    assert units.install_own_units(repo, ud) == []


def test_templated_timer_needs_no_enablement(env):
    base, _, ud = env
    _comp(base, "foo", '[systemd]\ntemplated_units = ["foo-x@.timer"]\n')
    (ud / "foo-x@.timer").write_text("x")
    assert _items(env, {"foo": True}, is_enabled=lambda n: False) == []
    assert _items(env, {"foo": False}, is_enabled=lambda n: True) == []
