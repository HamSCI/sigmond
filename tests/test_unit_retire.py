"""v3.96 Task 3: a component, or sigmond itself, retires units it no longer ships.

A retired unit is a move with no new owner (``sigmond.unit_moves``): the
same stop, disable and clear steps, the same ownership rule, the same drift
report and the same align step, so a retire that fails retries at every
align."""

import subprocess
import tomllib
from pathlib import Path

import pytest

from sigmond import unit_moves as um
from tests.test_unit_moves import NOW, FakeSystemd

TIMER = "timestd-prune.timer"
SERVICE = "timestd-prune.service"


def _setup(tmp_path, link=None, files=()):
    """A checkout root holding hf-timestd, a unit directory, an "elsewhere"
    directory.  ``link`` maps unit -> "repo" | "elsewhere" | "dangling"."""
    root = tmp_path / "git"
    repo = root / "hf-timestd"
    (repo / "systemd").mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    for unit, where in (link or {}).items():
        if where == "repo":
            (repo / "systemd" / unit).write_text("[Unit]\n")
            (unit_dir / unit).symlink_to(repo / "systemd" / unit)
        elif where == "elsewhere":
            (other / unit).write_text("[Unit]\n")
            (unit_dir / unit).symlink_to(other / unit)
        else:
            (unit_dir / unit).symlink_to(repo / "systemd" / ("gone-" + unit))
    for unit in files:
        (unit_dir / unit).write_text("[Unit]\n# a copy\n")
    return root, unit_dir


def _retire(fake, root, unit_dir, units=(TIMER,), *, apply=True, links=(), component="hf-timestd"):
    return um.apply_retire(um.UnitRetire(component, tuple(units), tuple(links)),
                           apply=apply, unit_dir=unit_dir, backup_dir=unit_dir.parent / "bak",
                           run=fake, now=NOW, checkout_root=f"{root}/")


def _verbs(fake, *verbs):
    return [c for c in fake.calls if c[0] == "systemctl" and c[1] in verbs]


# --- apply_retire ------------------------------------------------------------

def test_a_retired_timer_is_disabled_then_its_link_removed(tmp_path):
    root, unit_dir = _setup(tmp_path, link={TIMER: "repo"})
    fake = FakeSystemd({TIMER: ("enabled", "active")})
    step = _retire(fake, root, unit_dir)
    assert step.outcome == "retired"
    assert ["systemctl", "stop", TIMER] in fake.calls
    assert ["systemctl", "disable", TIMER] in fake.calls
    assert not (unit_dir / TIMER).is_symlink()
    assert _verbs(fake, "daemon-reload") == [["systemctl", "daemon-reload"]]


def test_a_timer_stops_before_its_service(tmp_path):
    # mutation check: drop the timer-first ordering
    root, unit_dir = _setup(tmp_path, link={SERVICE: "repo", TIMER: "repo"})
    fake = FakeSystemd({TIMER: ("enabled", "active"), SERVICE: ("static", "active")})
    _retire(fake, root, unit_dir, units=(SERVICE, TIMER))
    stops = [c[2] for c in _verbs(fake, "stop")]
    assert stops == [TIMER, SERVICE]


def test_a_file_outside_the_repo_is_left_alone(tmp_path):
    # mutation check: drop the foreign-link test, and the link goes
    root, unit_dir = _setup(tmp_path, link={TIMER: "elsewhere"})
    fake = FakeSystemd({TIMER: ("enabled", "active")})
    step = _retire(fake, root, unit_dir)
    assert (unit_dir / TIMER).is_symlink()
    assert not _verbs(fake, "stop", "disable", "daemon-reload")
    assert step.outcome == "ran" and step.detail.startswith("held:")
    assert str(tmp_path / "elsewhere" / TIMER) in step.detail


def test_a_dangling_link_goes(tmp_path):
    # mutation check: count only enabled/active units as live, and it stays
    root, unit_dir = _setup(tmp_path, link={TIMER: "dangling"})
    fake = FakeSystemd({})          # systemd knows nothing of it
    step = _retire(fake, root, unit_dir)
    assert step.outcome == "retired"
    assert not (unit_dir / TIMER).is_symlink()


def test_a_copied_unit_goes_to_the_backup_dir(tmp_path):
    # hf-timestd's install.sh copies its units (copy_unit): a regular file.
    root, unit_dir = _setup(tmp_path, files=(TIMER,))
    fake = FakeSystemd({TIMER: ("disabled", "inactive")})
    step = _retire(fake, root, unit_dir)
    assert step.outcome == "retired"
    assert not (unit_dir / TIMER).exists()
    assert [p.name for p in (tmp_path / "bak").iterdir()] == [f"{TIMER}.20261201T000000Z"]


def test_wants_links_go_with_the_unit(tmp_path):
    root, unit_dir = _setup(tmp_path, link={TIMER: "repo"})
    wants = unit_dir / "timers.target.wants"
    wants.mkdir()
    (wants / TIMER).symlink_to(unit_dir / TIMER)
    fake = FakeSystemd({TIMER: ("enabled", "active")})
    _retire(fake, root, unit_dir)
    assert not (wants / TIMER).is_symlink()


def test_dry_run_changes_nothing(tmp_path):
    root, unit_dir = _setup(tmp_path, link={TIMER: "repo"})
    fake = FakeSystemd({TIMER: ("enabled", "active")})
    step = _retire(fake, root, unit_dir, apply=False)
    assert step.outcome == "would retire"
    assert "stop and disable timestd-prune.timer" in step.detail
    assert (unit_dir / TIMER).is_symlink()
    assert not [c for c in fake.calls if c[1] not in ("is-enabled", "is-active")]


def test_second_run_is_unchanged(tmp_path):
    root, unit_dir = _setup(tmp_path)
    fake = FakeSystemd({})
    assert _retire(fake, root, unit_dir).outcome == "unchanged"
    assert not [c for c in fake.calls if c[1] not in ("is-enabled", "is-active")]


def test_a_failed_stop_fails_and_keeps_the_unit(tmp_path):
    root, unit_dir = _setup(tmp_path, link={TIMER: "repo"})
    fake = FakeSystemd({TIMER: ("enabled", "active")}, rcs={"stop": 1})
    step = _retire(fake, root, unit_dir)
    assert step.outcome == "retire failed"
    assert "systemctl stop timestd-prune.timer exit 1" in step.detail
    assert (unit_dir / TIMER).is_symlink()
    assert not _verbs(fake, "disable")


def test_a_failed_unit_is_reset(tmp_path):
    root, unit_dir = _setup(tmp_path, link={SERVICE: "repo"})
    fake = FakeSystemd({SERVICE: ("static", "failed")})
    assert _retire(fake, root, unit_dir, units=(SERVICE,)).outcome == "retired"
    assert ["systemctl", "reset-failed", SERVICE] in fake.calls


def test_a_failed_daemon_reload_fails_the_retire(tmp_path):
    root, unit_dir = _setup(tmp_path, link={TIMER: "repo"})
    fake = FakeSystemd({TIMER: ("enabled", "active")}, rcs={"daemon-reload": 1})
    assert _retire(fake, root, unit_dir).outcome == "retire failed"


def test_a_clear_that_raises_fails_the_retire(tmp_path, monkeypatch):
    root, unit_dir = _setup(tmp_path, link={TIMER: "repo"})
    fake = FakeSystemd({TIMER: ("enabled", "active")})

    def boom(*a, **k):
        raise PermissionError("read-only")
    monkeypatch.setattr(um, "_clear_unit", boom)
    step = _retire(fake, root, unit_dir)
    assert step.outcome == "retire failed" and "PermissionError" in step.detail


def test_sigmond_own_retire_removes_its_sbin_link(tmp_path):
    # Ruling P3: the helper link goes by its path, named in sigmond's list,
    # not by its source file (Task 4 deletes the source).
    root, unit_dir = _setup(tmp_path, files=("sigmond-x.timer", "sigmond-x.service"))
    sbin = tmp_path / "sbin"
    sbin.mkdir()
    (sbin / "sigmond-x").symlink_to(root / "sigmond" / "bin" / "sigmond-x")   # dangling
    fake = FakeSystemd({"sigmond-x.timer": ("enabled", "active")})
    step = _retire(fake, root, unit_dir, units=("sigmond-x.timer", "sigmond-x.service"),
                   links=(str(sbin / "sigmond-x"),), component="sigmond")
    assert step.outcome == "retired"
    assert not (sbin / "sigmond-x").is_symlink()
    assert not (unit_dir / "sigmond-x.timer").exists()


def test_sigmond_own_retire_keeps_an_operators_file_at_the_link_path(tmp_path):
    root, unit_dir = _setup(tmp_path)
    sbin = tmp_path / "sbin"
    sbin.mkdir()
    (sbin / "sigmond-x").write_text("#!/bin/sh\n")
    fake = FakeSystemd({})
    step = _retire(fake, root, unit_dir, units=("sigmond-x.timer",),
                   links=(str(sbin / "sigmond-x"),), component="sigmond")
    assert step.outcome == "unchanged"
    assert (sbin / "sigmond-x").is_file()


def test_processes_run_as_argv_lists(tmp_path):
    root, unit_dir = _setup(tmp_path, link={TIMER: "repo"})
    seen = []

    def run(argv, **kw):
        assert isinstance(argv, list) and not kw.get("shell")
        seen.append(argv)
        return FakeSystemd({TIMER: ("enabled", "active")})(argv, **kw)
    _retire(run, root, unit_dir)
    assert seen


# --- declarations ------------------------------------------------------------

def test_retired_from_deploy_reads_the_key(tmp_path):
    (tmp_path / "hf-timestd").mkdir()
    (tmp_path / "hf-timestd" / "deploy.toml").write_text(
        '[systemd]\nunits = ["timestd-core-recorder.service"]\n'
        f'retired_units = ["{TIMER}", "{SERVICE}"]\n')
    assert um.declared_retired(tmp_path, ["hf-timestd"]) == [
        um.UnitRetire("hf-timestd", (TIMER, SERVICE))]


def test_parse_without_retired_units_is_none():
    assert um.parse_retired("x", {"systemd": {"units": ["a.service"]}}) is None
    assert um.parse_retired("x", {}) is None


@pytest.mark.parametrize("bad", ["../etc/passwd.service", "-x.service", "x.socket",
                                 "x@.service", ""])
def test_parse_refuses_a_name_that_cannot_be_retired(bad):
    with pytest.raises(ValueError, match="comp"):
        um.parse_retired("comp", {"systemd": {"retired_units": [bad]}})


def test_parse_refuses_a_string_for_the_list():
    with pytest.raises(ValueError, match="list"):
        um.parse_retired("comp", {"systemd": {"retired_units": TIMER}})


def test_parse_refuses_retiring_a_unit_it_still_ships():
    with pytest.raises(ValueError, match="still lists"):
        um.parse_retired("comp", {"systemd": {"units": [TIMER], "retired_units": [TIMER]}})


_BEFORE = (f'[systemd]\nunits = ["a.timer"]\nretired_units = ["{TIMER}"]\n'
           '[[systemd.moves]]\nfrom = ["b.timer"]\nto = "a.timer"\n')
_AFTER = ('[systemd]\nunits = ["a.timer"]\n'
          '[[systemd.moves]]\nfrom = ["b.timer"]\nto = "a.timer"\n'
          f'retired_units = ["{TIMER}"]\n')


def test_retired_units_before_the_moves_lands_in_systemd():
    # Ruling P2: placed before the first [[systemd.moves]] it sits in [systemd].
    deploy = tomllib.loads(_BEFORE)
    assert deploy["systemd"]["retired_units"] == [TIMER]
    assert um.parse_retired("comp", deploy) == um.UnitRetire("comp", (TIMER,))
    assert len(um.parse_moves("comp", deploy)) == 1


def test_retired_units_after_a_moves_table_is_refused():
    # Ruling P2: after a moves table TOML puts it inside that move.
    # mutation check: drop the refusal in parse_moves, and it passes silently
    deploy = tomllib.loads(_AFTER)
    assert "retired_units" not in deploy["systemd"]
    with pytest.raises(ValueError, match=r"retired_units.*\[systemd\]"):
        um.parse_moves("comp", deploy)


def test_declared_retired_at_reads_the_commit(tmp_path):
    def run(argv, **kw):
        assert argv[:3] == ["git", "-C", str(tmp_path)] and argv[4] == "abc:deploy.toml"
        return subprocess.CompletedProcess(argv, 0, f'[systemd]\nretired_units = ["{TIMER}"]\n', "")
    assert um.declared_retired_at(tmp_path, "hf-timestd", "abc", run=run) == [
        um.UnitRetire("hf-timestd", (TIMER,))]


def test_the_own_list_parses():
    for r in um.RETIRED_OWN:
        assert r.component == "sigmond"
        assert all(um._UNIT_NAME.fullmatch(u) and "@." not in u for u in r.units)
        assert all(Path(p).is_absolute() for p in r.links)


# --- drift -------------------------------------------------------------------

def _drift_base(tmp_path, retired=(TIMER,)):
    (tmp_path / "hf-timestd").mkdir()
    names = ", ".join(f'"{u}"' for u in retired)
    (tmp_path / "hf-timestd" / "deploy.toml").write_text(
        f'[systemd]\nunits = ["timestd-core-recorder.service"]\nretired_units = [{names}]\n')
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    return tmp_path, unit_dir


def test_drift_names_a_retired_unit_still_enabled(tmp_path):
    # mutation check: drop the retire loop from drift_items
    base, unit_dir = _drift_base(tmp_path)
    items = um.drift_items(base, {"hf-timestd": True}, unit_dir=unit_dir,
                           is_enabled=lambda u: u == TIMER, service_active=lambda u: False,
                           own=())
    assert [(i.kind, i.severity, i.subject, i.fix) for i in items] == [
        ("unit", "fault", TIMER, "smd align --apply")]
    assert items[0].detail == f"{TIMER} retired by hf-timestd, still enabled"


def test_drift_names_a_retired_unit_whose_file_stayed(tmp_path):
    base, unit_dir = _drift_base(tmp_path)
    (unit_dir / TIMER).write_text("[Unit]\n")
    items = um.drift_items(base, {}, unit_dir=unit_dir, is_enabled=lambda u: False,
                           service_active=lambda u: False, own=())
    assert [i.subject for i in items] == [TIMER]
    assert "installed" in items[0].detail


def test_drift_quiet_once_retired(tmp_path):
    base, unit_dir = _drift_base(tmp_path)
    assert um.drift_items(base, {"hf-timestd": True}, unit_dir=unit_dir,
                          is_enabled=lambda u: False, service_active=lambda u: False,
                          own=()) == []


def test_drift_names_a_foreign_link_with_a_hand_fix(tmp_path):
    base, unit_dir = _drift_base(tmp_path)
    (tmp_path / "mine.timer").write_text("[Unit]\n")
    (unit_dir / TIMER).symlink_to(tmp_path / "mine.timer")
    items = um.drift_items(base, {"hf-timestd": True}, unit_dir=unit_dir,
                           is_enabled=lambda u: True, service_active=lambda u: False, own=())
    assert len(items) == 1 and "smd align" not in items[0].fix
    assert str(unit_dir / TIMER) in items[0].fix


def test_drift_names_sigmonds_own_retired_unit_and_link(tmp_path):
    base, unit_dir = _drift_base(tmp_path, retired=())
    sbin = tmp_path / "sbin"
    sbin.mkdir()
    (sbin / "sigmond-x").symlink_to("/opt/git/sigmond/sigmond/bin/sigmond-x")
    own = (um.UnitRetire("sigmond", ("sigmond-x.timer",), (str(sbin / "sigmond-x"),)),)
    items = um.drift_items(base, {}, unit_dir=unit_dir, is_enabled=lambda u: u == "sigmond-x.timer",
                           service_active=lambda u: False, own=own)
    assert sorted(i.subject for i in items) == sorted(["sigmond-x.timer", str(sbin / "sigmond-x")])
    assert all(i.fix == "smd align --apply" for i in items)


def test_drift_report_runs_the_retire_collector(tmp_path):
    from sigmond.drift import build_report
    base, unit_dir = _drift_base(tmp_path)
    readers = build_report.Readers(
        base=base, enabled={"hf-timestd": False}, unit_dir=unit_dir,
        is_enabled=lambda u: u == TIMER, service_active=lambda u: False,
        template_instances=lambda u: 0, exceptions_path=tmp_path / "none.toml",
        run=lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    report = build_report.build("TEST", readers=readers)
    assert any(i.subject == TIMER and "retired by hf-timestd" in i.detail for i in report.items)


# --- align -------------------------------------------------------------------

from unittest import mock                                    # noqa: E402

from tests.test_align_unit_moves import _moves as _align_moves, _dry_rc   # noqa: E402
from tests.test_align_cli import smd                         # noqa: E402
from sigmond import align_apply                              # noqa: E402


def _moves(apply, base, **kw):
    """test_align_unit_moves._moves, with the backup directory in tmp."""
    with mock.patch.object(smd, "_ALIGN_UNIT_MOVE_BACKUPS", base / "bak"):
        return _align_moves(apply, base, **kw)


def _timestd_tree(base, retired=(TIMER, SERVICE), units=("timestd-core-recorder.service",)):
    repo = base / "hf-timestd"
    repo.mkdir()
    names = ", ".join(f'"{u}"' for u in retired)
    shipped = ", ".join(f'"{u}"' for u in units)
    (repo / "deploy.toml").write_text(f'[systemd]\nunits = [{shipped}]\nretired_units = [{names}]\n')
    (base / "units").mkdir()
    for u in retired:
        (base / "units" / u).write_text("[Unit]\n")     # install.sh copied it


_PRUNE_LIVE = {TIMER: ("enabled", "active"), SERVICE: ("static", "inactive"),
               "timestd-core-recorder.service": ("enabled", "active")}


def test_align_retires_a_unit_the_component_dropped(tmp_path):
    _timestd_tree(tmp_path)
    steps, _, calls = _moves(True, tmp_path, topology={"hf-timestd": True}, states=_PRUNE_LIVE)
    assert [s.outcome for s in steps] == ["retired"]
    assert ["systemctl", "stop", TIMER] in calls
    assert not (tmp_path / "units" / TIMER).exists()


def test_align_retires_for_a_component_topology_disables(tmp_path):
    _timestd_tree(tmp_path)
    steps, _, _ = _moves(True, tmp_path, topology={"hf-timestd": False}, states=_PRUNE_LIVE)
    assert [s.outcome for s in steps] == ["retired"]


def test_align_dry_run_lists_the_retire(tmp_path):
    _timestd_tree(tmp_path)
    steps, lines, calls = _moves(False, tmp_path, topology={"hf-timestd": True},
                                 states=_PRUNE_LIVE)
    assert [s.outcome for s in steps] == ["would retire"]
    assert f"  would retire {TIMER}, {SERVICE} (hf-timestd)" in lines
    assert not [c for c in calls if c[:2] in (["systemctl", "stop"], ["systemctl", "disable"])]
    assert (tmp_path / "units" / TIMER).exists()


def test_align_holds_a_retire_the_site_manages_by_hand(tmp_path):
    _timestd_tree(tmp_path)
    steps, _, calls = _moves(True, tmp_path, topology={"hf-timestd": True},
                             states=_PRUNE_LIVE, local={"hf-timestd"})
    assert steps[0].outcome == "ran" and "locally_managed" in steps[0].detail
    assert ["systemctl", "stop", TIMER] not in calls


def test_align_holds_a_unit_another_component_still_ships(tmp_path):
    # mutation check: drop the "still shipped" hold, and the unit stops
    _timestd_tree(tmp_path)
    other = tmp_path / "hamsci-physics"
    other.mkdir()
    (other / "deploy.toml").write_text(f'[systemd]\nunits = ["{TIMER}"]\n')
    steps, _, calls = _moves(True, tmp_path, topology={"hf-timestd": True},
                             states=_PRUNE_LIVE)
    retire = [s for s in steps if "retire" in s.component]
    assert retire[0].outcome == "ran" and "hamsci-physics still ships" in retire[0].detail
    assert ["systemctl", "stop", TIMER] not in calls


def test_align_retires_sigmonds_own_units(tmp_path):
    (tmp_path / "units").mkdir()
    (tmp_path / "units" / "sigmond-x.timer").write_text("[Unit]\n")
    own = (um.UnitRetire("sigmond", ("sigmond-x.timer",)),)
    with mock.patch.object(um, "RETIRED_OWN", own):
        steps, _, calls = _moves(True, tmp_path, topology={},
                                 states={"sigmond-x.timer": ("enabled", "active")})
    assert [(s.component, s.outcome) for s in steps] == [("sigmond: retire sigmond-x.timer",
                                                          "retired")]
    assert ["systemctl", "stop", "sigmond-x.timer"] in calls


def test_align_reports_a_malformed_retire(tmp_path):
    _timestd_tree(tmp_path, retired=("x@.service",))
    steps, _, _ = _moves(True, tmp_path, topology={"hf-timestd": True}, states={})
    assert [s.outcome for s in steps] == ["retire failed"]


def test_a_second_align_is_unchanged(tmp_path):
    _timestd_tree(tmp_path)
    _moves(True, tmp_path, topology={"hf-timestd": True}, states=_PRUNE_LIVE)
    steps, _, _ = _moves(True, tmp_path, topology={"hf-timestd": True},
                         states={"timestd-core-recorder.service": ("enabled", "active")})
    assert [s.outcome for s in steps] == ["unchanged"]


def test_dry_run_counts_a_due_retire_as_pending():
    # mutation check: drop "would retire" from moves_due
    assert _dry_rc([align_apply.Step("x: retire a.timer", "would retire", "...")]) == 1


# --- fix round 1 ---------------------------------------------------------------

from tests.test_align_unit_moves import _order_run          # noqa: E402
import tests.test_align_cli as tac                           # noqa: E402

_RETIRE_FAILED = align_apply.Step("hf-timestd: retire timestd-prune.timer", "retire failed",
                                  "systemctl stop timestd-prune.timer exit 1")


def test_a_failed_retire_still_restarts_and_records_but_exits_1():
    # I1: a retire starts nothing, so it holds nothing back.
    # mutation check: put "retire failed" in _ALIGN_BLOCKING, and nothing restarts
    rc, out, m, order, _ = _order_run([_RETIRE_FAILED])
    m["restart"].assert_called()
    m["record"].assert_called()
    assert rc == 1
    assert "ERROR: hf-timestd: retire timestd-prune.timer did not finish" in out


def test_a_failed_retire_on_the_quiet_path_exits_1():
    t = tac.AlignMakeLiveApplyTests("test_stage_order_moves_images_bringup_record_restart_checks")
    rc, out, m = t._run(
        moves=[align_apply.Step("sigmond", "current")],
        extra_patches=[
            mock.patch.object(smd, "_align_staleness", lambda *a, **k: tac._staleness()),
            mock.patch.object(smd, "_align_device_steps", lambda **k: []),
            mock.patch.object(smd, "_align_declared_units_steps", lambda *a, **k: []),
            mock.patch.object(smd, "_align_unit_moves", lambda apply, **k: [_RETIRE_FAILED])])
    assert "aligned — nothing to do" in out
    assert rc == 1


def test_a_failed_reset_fails_the_retire(tmp_path):
    # mutation check: ignore reset-failed's exit code, and it reads "retired"
    root, unit_dir = _setup(tmp_path, link={SERVICE: "repo"})
    fake = FakeSystemd({SERVICE: ("static", "failed")}, rcs={"reset-failed": 1})
    step = _retire(fake, root, unit_dir, units=(SERVICE,))
    assert step.outcome == "retire failed"
    assert "systemctl reset-failed timestd-prune.service exit 1" in step.detail


def test_a_timer_that_will_not_stop_keeps_its_service(tmp_path):
    root, unit_dir = _setup(tmp_path, link={SERVICE: "repo", TIMER: "repo"})
    fake = FakeSystemd({TIMER: ("enabled", "active"), SERVICE: ("static", "inactive")},
                       rcs={"stop": 1})
    step = _retire(fake, root, unit_dir, units=(SERVICE, TIMER))
    assert step.outcome == "retire failed"
    assert (unit_dir / SERVICE).is_symlink()
    assert f"{SERVICE} left as it is: {TIMER} did not stop" in step.detail


def test_an_operators_timer_holds_its_service_too(tmp_path):
    root, unit_dir = _setup(tmp_path, link={TIMER: "elsewhere", SERVICE: "repo"})
    fake = FakeSystemd({TIMER: ("enabled", "active"), SERVICE: ("static", "inactive")})
    step = _retire(fake, root, unit_dir, units=(SERVICE, TIMER))
    assert step.outcome == "ran" and (unit_dir / SERVICE).is_symlink()
    assert not _verbs(fake, "stop", "disable")


def test_a_failed_and_disabled_retired_unit_with_no_file_is_unchanged(tmp_path):
    # One definition of live (in_service), shared with moves.
    root, unit_dir = _setup(tmp_path)
    fake = FakeSystemd({TIMER: ("disabled", "failed")})
    assert _retire(fake, root, unit_dir).outcome == "unchanged"


def test_a_malformed_retire_keeps_the_components_moves(tmp_path):
    from tests.test_align_unit_moves import _moved_tree
    _moved_tree(tmp_path)
    deploy = tmp_path / "hamsci-physics" / "deploy.toml"
    deploy.write_text(deploy.read_text().replace(
        "[systemd]\n", '[systemd]\nretired_units = ["x@.service"]\n', 1))
    steps, _, _ = _moves(True, tmp_path, topology={"hamsci-physics": True})
    assert sorted(s.outcome for s in steps) == ["moved", "retire failed"]


def test_drift_reports_a_locally_managed_retire_as_held(tmp_path):
    # I2. mutation check: drop the hold in drift, and it reads as a fault
    base, unit_dir = _drift_base(tmp_path)
    items = um.drift_items(base, {"hf-timestd": True}, unit_dir=unit_dir,
                           is_enabled=lambda u: u == TIMER, service_active=lambda u: False,
                           own=(), local={"hf-timestd"})
    assert [(i.severity, i.subject) for i in items] == [("drift", TIMER)]
    assert "locally_managed" in items[0].detail and "smd align" not in items[0].fix


def test_drift_reports_a_unit_another_checkout_ships_as_held(tmp_path):
    base, unit_dir = _drift_base(tmp_path)
    (base / "hamsci-physics").mkdir()
    (base / "hamsci-physics" / "deploy.toml").write_text(f'[systemd]\nunits = ["{TIMER}"]\n')
    items = um.drift_items(base, {"hf-timestd": True}, unit_dir=unit_dir,
                           is_enabled=lambda u: u == TIMER, service_active=lambda u: False,
                           own=(), local=())
    assert [(i.severity, i.subject) for i in items] == [("drift", TIMER)]
    assert "hamsci-physics still ships" in items[0].detail
    assert "smd align" not in items[0].fix


def test_drift_names_a_malformed_retire_and_keeps_the_rest(tmp_path):
    base, unit_dir = _drift_base(tmp_path)
    (base / "bad").mkdir()
    (base / "bad" / "deploy.toml").write_text('[systemd]\nretired_units = ["x@.service"]\n')
    items = um.drift_items(base, {"hf-timestd": True}, unit_dir=unit_dir,
                           is_enabled=lambda u: u == TIMER, service_active=lambda u: False,
                           own=(), local=())
    assert sorted((i.severity, i.subject) for i in items) == [
        ("fault", "bad: retired_units"), ("fault", TIMER)]
