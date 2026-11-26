import subprocess
from datetime import datetime, timezone
from pathlib import Path
import pytest
from sigmond import unit_moves as um

NOW = datetime(2026, 12, 1, tzinfo=timezone.utc)
MOVE = um.UnitMove("hamsci-physics", ("timestd-vtec.service",), "hamsci-physics-vtec.service",
                   ("/x/hamsci-physics", "vtec", "adopt"))
REPOS = Path(__file__).resolve().parents[2]


class FakeSystemd:
    """Answers systemctl from a dict of states and records every argv."""
    def __init__(self, states, migrate_rc=0, after=("active", "yes", "loaded"), rcs=None,
                 migrate_err=""):
        self.states, self.calls, self.migrate_rc, self.after = states, [], migrate_rc, after
        self.rcs, self.migrate_err = rcs or {}, migrate_err
    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        out, rc = "", 0
        if argv[0] != "systemctl":
            rc = self.migrate_rc
            return subprocess.CompletedProcess(argv, rc, "", self.migrate_err)
        rc = self.rcs.get(argv[1], 0)
        if argv[1] == "is-enabled":
            out = self.states.get(argv[2], ("", ""))[0]
        elif argv[1] == "is-active":
            out = self.states.get(argv[2], ("", "inactive"))[1]
        elif argv[1] == "show":
            out = "\n".join(self.after)
        return subprocess.CompletedProcess(argv, rc, out + "\n", "")


def _apply(fake, tmp_path, **kw):
    d = dict(apply=True, owner_enabled=True, unit_dir=tmp_path, backup_dir=tmp_path / "bak",
             run=fake, now=NOW)
    d.update(kw)
    return um.apply_move(MOVE, **d)


def test_stop_comes_before_start(tmp_path):
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "active")})
    step = _apply(fake, tmp_path)
    flat = [" ".join(c) for c in fake.calls]
    assert step.outcome == "moved"
    assert flat.index("systemctl stop timestd-vtec.service") < flat.index("systemctl start hamsci-physics-vtec.service")
    assert flat.index("/x/hamsci-physics vtec adopt") < flat.index("systemctl stop timestd-vtec.service")


def test_failed_migrate_keeps_the_old_unit(tmp_path):
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "active")}, migrate_rc=1)
    step = _apply(fake, tmp_path)
    assert step.outcome == "failed"
    assert not any(c[:2] == ["systemctl", "stop"] for c in fake.calls)


def test_second_run_is_unchanged(tmp_path):
    fake = FakeSystemd({})
    assert _apply(fake, tmp_path).outcome == "unchanged"
    assert not any(c[0] != "systemctl" or c[1] not in ("is-enabled", "is-active") for c in fake.calls)


def test_disabled_owner_holds_the_move(tmp_path):
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "active")})
    step = _apply(fake, tmp_path, owner_enabled=False)
    assert step.outcome == "ran" and step.detail.startswith("held:")
    assert not any(c[:2] in (["systemctl", "stop"], ["systemctl", "disable"]) for c in fake.calls)


def test_dangling_old_unit_is_cleared(tmp_path):
    wants = tmp_path / "multi-user.target.wants"; wants.mkdir()
    (wants / "timestd-vtec.service").symlink_to(tmp_path / "timestd-vtec.service")
    (tmp_path / "timestd-vtec.service").symlink_to("/opt/git/sigmond/hf-timestd/systemd/timestd-vtec.service")
    fake = FakeSystemd({"timestd-vtec.service": ("", "active")})   # is-enabled prints nothing
    assert _apply(fake, tmp_path).outcome == "moved"
    assert not (wants / "timestd-vtec.service").is_symlink()
    assert not (tmp_path / "timestd-vtec.service").is_symlink()


def test_condition_skip_counts_as_moved(tmp_path):
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "active")}, after=("inactive", "no", "loaded"))
    step = _apply(fake, tmp_path)
    assert step.outcome == "moved" and "waits for its condition" in step.detail


def test_new_unit_that_fails_fails_the_move(tmp_path):
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "active")}, after=("failed", "yes", "loaded"))
    assert _apply(fake, tmp_path).outcome == "failed"


def test_dry_run_changes_nothing(tmp_path):
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "active")})
    assert _apply(fake, tmp_path, apply=False).outcome == "would move"
    assert all(c[1] in ("is-enabled", "is-active") for c in fake.calls)


def test_parse_refuses_a_target_the_component_does_not_declare():
    deploy = {"systemd": {"units": ["a.service"], "moves": [{"from": "old.service", "to": "b.service"}]}}
    with pytest.raises(ValueError, match="x"):
        um.parse_moves("x", deploy)


def test_parse_accepts_a_string_from():
    deploy = {"systemd": {"units": ["b.timer"], "moves": [{"from": "old.timer", "to": "b.timer"}]}}
    assert um.parse_moves("x", deploy) == [um.UnitMove("x", ("old.timer",), "b.timer", ())]


# --- beyond the brief's list: the rest of the interface -------------------

@pytest.mark.parametrize("move", [
    {"to": "b.timer"},                          # no from
    {"from": "old.timer"},                      # no to
    {"from": ["b.timer"], "to": "b.timer"},     # to inside from
])
def test_parse_refuses_a_malformed_move(move):
    deploy = {"systemd": {"units": ["b.timer"], "moves": [move]}}
    with pytest.raises(ValueError, match="comp"):
        um.parse_moves("comp", deploy)


def test_parse_without_moves_is_empty():
    assert um.parse_moves("x", {"systemd": {"units": ["a.service"]}}) == []
    assert um.parse_moves("x", {}) == []


def test_parse_reads_the_real_declarations():
    """The moves the gaining repos committed parse as declared."""
    names = [n for n in ("hamsci-physics", "hf-timestd") if (REPOS / n / "deploy.toml").is_file()]
    if len(names) < 2:
        pytest.skip("sibling checkouts absent")
    got = um.declared_moves(REPOS, names)
    assert um.UnitMove("hamsci-physics", ("timestd-vtec.service",), "hamsci-physics-vtec.service",
                       ("/opt/git/sigmond/hamsci-physics/venv/bin/hamsci-physics", "vtec", "adopt")) in got
    assert um.UnitMove("hf-timestd",
                       ("hamsci-physics-ionex-download.timer", "hamsci-physics-ionex-download.service"),
                       "timestd-ionex-download.timer",
                       ("/opt/git/sigmond/hf-timestd/venv/bin/hf-timestd", "data", "adopt-netrc")) in got
    assert um.UnitMove("hf-timestd",
                       ("hamsci-physics-iri-update.timer", "hamsci-physics-iri-update.service"),
                       "timestd-iri-update.timer", ()) in got


def test_declared_moves_skips_an_unreadable_file(tmp_path):
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "deploy.toml").write_text(
        '[systemd]\nunits = ["b.timer"]\n[[systemd.moves]]\nfrom = "a.timer"\nto = "b.timer"\n')
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "deploy.toml").write_text("not [ toml")
    got = um.declared_moves(tmp_path, ["missing", "bad", "good"])
    assert got == [um.UnitMove("good", ("a.timer",), "b.timer", ())]


def test_declared_moves_at_reads_the_commit(tmp_path):
    seen = []
    def run(argv, **kw):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0,
            '[systemd]\nunits = ["b.timer"]\n[[systemd.moves]]\nfrom = ["a.timer"]\nto = "b.timer"\n', "")
    got = um.declared_moves_at(tmp_path, "c", "abc123", run=run)
    assert got == [um.UnitMove("c", ("a.timer",), "b.timer", ())]
    assert seen == [["git", "-C", str(tmp_path), "show", "abc123:deploy.toml"]]


def test_declared_moves_at_a_missing_file_is_empty(tmp_path):
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 128, "", "fatal: path does not exist")
    assert um.declared_moves_at(tmp_path, "c", "abc123", run=run) == []


def test_unit_state_reads_systemctl(tmp_path):
    fake = FakeSystemd({"a.service": ("enabled-runtime", "activating")})
    assert um.unit_state("a.service", unit_dir=tmp_path, run=fake) == (True, True)
    fake = FakeSystemd({"a.service": ("disabled", "failed")})
    assert um.unit_state("a.service", unit_dir=tmp_path, run=fake) == (False, False)


def test_unit_state_counts_a_dangling_wants_link(tmp_path):
    wants = tmp_path / "timers.target.wants"; wants.mkdir()
    (wants / "a.timer").symlink_to(tmp_path / "a.timer")      # target absent
    fake = FakeSystemd({})
    assert um.unit_state("a.timer", unit_dir=tmp_path, run=fake) == (True, False)


def test_regular_old_unit_file_goes_to_backup(tmp_path):
    (tmp_path / "timestd-vtec.service").write_text("[Service]\n")
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "inactive")})
    step = _apply(fake, tmp_path)
    assert step.outcome == "moved"
    assert not (tmp_path / "timestd-vtec.service").exists()
    assert (tmp_path / "bak" / "timestd-vtec.service.20261201T000000Z").read_text() == "[Service]\n"
    # The old unit was not running, so the new one is enabled but not started.
    assert ["systemctl", "start", "hamsci-physics-vtec.service"] not in fake.calls
    assert ["systemctl", "enable", "hamsci-physics-vtec.service"] in fake.calls


def test_migrate_runs_as_argv_without_a_shell(tmp_path):
    seen = []
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "active")})
    def run(argv, **kw):
        seen.append(kw)
        return fake(argv, **kw)
    _apply(fake, tmp_path, run=run)
    assert all(not kw.get("shell") for kw in seen)
    assert ["/x/hamsci-physics", "vtec", "adopt"] in fake.calls


def test_timer_move_clears_the_companion_service_link(tmp_path):
    """The timer runs; its .service sits idle, yet its link leaves too."""
    move = um.UnitMove("hf-timestd", ("old.timer", "old.service"), "new.timer", ())
    (tmp_path / "old.service").symlink_to("/opt/git/sigmond/hamsci-physics/systemd/old.service")
    fake = FakeSystemd({"old.timer": ("enabled", "active")})
    step = um.apply_move(move, apply=True, owner_enabled=True, unit_dir=tmp_path,
                         backup_dir=tmp_path / "bak", run=fake, now=NOW)
    assert step.outcome == "moved"
    assert not (tmp_path / "old.service").is_symlink()
    assert ["systemctl", "stop", "old.service"] not in fake.calls


def test_enabled_but_idle_old_unit_moves_without_a_start(tmp_path):
    """Nothing ran before, so an inactive new unit is the goal, not a failure."""
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "inactive")}, after=("inactive", "yes", "loaded"))
    step = _apply(fake, tmp_path)
    assert step.outcome == "moved"
    assert ["systemctl", "start", "hamsci-physics-vtec.service"] not in fake.calls


def test_a_failed_stop_starts_nothing(tmp_path):
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "active")})
    def run(argv, **kw):
        cp = fake(argv, **kw)
        if argv[:2] == ["systemctl", "stop"]:
            return subprocess.CompletedProcess(argv, 1, "", "")
        return cp
    step = _apply(fake, tmp_path, run=run)
    assert step.outcome == "failed"
    assert not any(c[:2] in (["systemctl", "start"], ["systemctl", "enable"]) for c in fake.calls)


# --- fix round 1 -----------------------------------------------------------

def test_a_start_that_fails_is_no_condition_wait(tmp_path):
    """A missing unit reads inactive / ConditionResult=no too; start's exit tells."""
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "active")},
                       after=("inactive", "no", "loaded"), rcs={"start": 5})
    step = _apply(fake, tmp_path)
    assert step.outcome == "failed"
    assert "timestd-vtec.service" in step.detail and "hamsci-physics-vtec.service" in step.detail
    assert "systemctl status hamsci-physics-vtec.service" in step.detail


def test_an_unloaded_new_unit_fails_the_move(tmp_path):
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "active")},
                       after=("inactive", "no", "not-found"))
    step = _apply(fake, tmp_path)
    assert step.outcome == "failed" and "not-found" in step.detail


def test_a_failed_enable_fails_the_move(tmp_path):
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "inactive")},
                       after=("inactive", "yes", "loaded"), rcs={"enable": 1})
    step = _apply(fake, tmp_path)
    assert step.outcome == "failed" and "enable" in step.detail


def test_a_failed_daemon_reload_fails_the_move(tmp_path):
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "active")}, rcs={"daemon-reload": 1})
    step = _apply(fake, tmp_path)
    assert step.outcome == "failed" and "daemon-reload" in step.detail
    assert not any(c[:2] == ["systemctl", "start"] for c in fake.calls)


@pytest.mark.parametrize("bad", ["/etc/sigmond/topology.toml", "/etc/x.service",
                                 "../x.service", "-x.service", "foo.conf"])
@pytest.mark.parametrize("side", ["from", "to"])
def test_parse_refuses_a_name_that_is_not_a_unit(bad, side):
    move = {"from": "old.service", "to": "b.service"}
    move[side] = bad
    deploy = {"systemd": {"units": ["b.service", bad], "moves": [move]}}
    with pytest.raises(ValueError, match="comp") as err:
        um.parse_moves("comp", deploy)
    assert bad in str(err.value)


def test_a_failed_migrate_says_why(tmp_path):
    fake = FakeSystemd({"timestd-vtec.service": ("enabled", "active")}, migrate_rc=1,
                       migrate_err="reading config\nrefused: no gnss-vtec section\n\n")
    step = _apply(fake, tmp_path)
    assert step.outcome == "failed"
    assert "refused: no gnss-vtec section" in step.detail
    assert "reading config" not in step.detail


# --- Task 10: drift reports a half-done move -------------------------------

_PHYSICS_DEPLOY = ('[systemd]\nunits = ["hamsci-physics-vtec.service"]\n'
                   '[[systemd.moves]]\nfrom = ["timestd-vtec.service"]\n'
                   'to = "hamsci-physics-vtec.service"\n')


def _drift_base(tmp_path):
    (tmp_path / "hamsci-physics").mkdir()
    (tmp_path / "hamsci-physics" / "deploy.toml").write_text(_PHYSICS_DEPLOY)
    unit_dir = tmp_path / "units"; unit_dir.mkdir()
    return tmp_path, unit_dir


def test_drift_faults_a_held_move(tmp_path):
    base, unit_dir = _drift_base(tmp_path)
    items = um.drift_items(base, {"hamsci-physics": False}, unit_dir=unit_dir,
                           is_enabled=lambda u: False,
                           service_active=lambda u: u == "timestd-vtec.service")
    assert [(i.kind, i.severity, i.subject, i.fix) for i in items] == [
        ("unit", "fault", "timestd-vtec.service", "smd enable hamsci-physics")]
    assert items[0].detail == ("timestd-vtec.service moved to hamsci-physics-vtec.service "
                               "in hamsci-physics, which topology disables here")


def test_drift_faults_an_old_unit_that_still_runs(tmp_path):
    base, unit_dir = _drift_base(tmp_path)
    items = um.drift_items(base, {"hamsci-physics": True}, unit_dir=unit_dir,
                           is_enabled=lambda u: u == "timestd-vtec.service",
                           service_active=lambda u: False)
    assert [(i.severity, i.subject, i.detail, i.fix) for i in items] == [
        ("fault", "timestd-vtec.service",
         "timestd-vtec.service still runs; hamsci-physics took it over as "
         "hamsci-physics-vtec.service", "smd align --apply")]


def test_drift_counts_a_dangling_wants_link(tmp_path):
    base, unit_dir = _drift_base(tmp_path)
    wants = unit_dir / "multi-user.target.wants"; wants.mkdir()
    (wants / "timestd-vtec.service").symlink_to(unit_dir / "timestd-vtec.service")
    items = um.drift_items(base, {"hamsci-physics": True}, unit_dir=unit_dir,
                           is_enabled=lambda u: False, service_active=lambda u: False)
    assert [i.subject for i in items] == ["timestd-vtec.service"]


def test_drift_quiet_after_the_move(tmp_path):
    base, unit_dir = _drift_base(tmp_path)
    assert um.drift_items(base, {"hamsci-physics": True}, unit_dir=unit_dir,
                          is_enabled=lambda u: False, service_active=lambda u: False) == []


def test_drift_report_runs_the_moves_collector(tmp_path):
    from sigmond.drift import build_report
    base, unit_dir = _drift_base(tmp_path)
    readers = build_report.Readers(
        base=base, enabled={"hamsci-physics": False}, unit_dir=unit_dir,
        is_enabled=lambda u: False,
        service_active=lambda u: u == "timestd-vtec.service",
        template_instances=lambda u: 0, exceptions_path=tmp_path / "none.toml",
        run=lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""))
    report = build_report.build("TEST", readers=readers)
    assert any(i.subject == "timestd-vtec.service" and i.fix == "smd enable hamsci-physics"
               for i in report.items)


def test_drift_faults_a_move_whose_owner_topology_does_not_name(tmp_path):
    base, unit_dir = _drift_base(tmp_path)
    items = um.drift_items(base, {"hf-timestd": True}, unit_dir=unit_dir,
                           is_enabled=lambda u: u == "timestd-vtec.service",
                           service_active=lambda u: False)
    assert [(i.subject, i.detail, i.fix) for i in items] == [
        ("timestd-vtec.service",
         "timestd-vtec.service moved to hamsci-physics-vtec.service in hamsci-physics, "
         "which topology does not name here", "smd enable hamsci-physics")]


def test_link_units_leaves_a_regular_file_alone(tmp_path):
    repo = tmp_path / "c"; (repo / "systemd").mkdir(parents=True)
    (repo / "systemd" / "n.timer").write_text("x")
    (repo / "systemd" / "n.service").write_text("x")
    (repo / "deploy.toml").write_text(
        '[[install.steps]]\nkind = "link"\nsrc = "systemd/n.timer"\n'
        'dst = "/etc/systemd/system/n.timer"\n'
        '[[install.steps]]\nkind = "link"\nsrc = "systemd/n.service"\n'
        'dst = "/etc/systemd/system/n.service"\n')
    unit_dir = tmp_path / "u"; unit_dir.mkdir()
    (unit_dir / "n.service").write_text("operator's")
    move = um.UnitMove("c", ("o.timer",), "n.timer")
    assert um.link_units(repo, um.move_units(move), unit_dir=unit_dir) == ["n.timer"]
    assert (unit_dir / "n.service").read_text() == "operator's"
