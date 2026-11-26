"""v3.96 Task 10: `smd align` carries out `[[systemd.moves]]`.

The dry run lists each move a component declares at the commit align would
move it to; --apply runs the moves after the checkouts moved and before
bring-up, and a failed move holds the restarts back."""

import contextlib
import io
import subprocess
from pathlib import Path
from unittest import mock

from sigmond import align, align_apply
import tests.test_align_cli as tac
from tests.test_align_cli import smd

# hamsci-physics's shape: no unit link steps; the unit ships in systemd/.
_NO_MOVE = '[systemd]\nunits = ["hamsci-physics-fusion.service", "hamsci-physics-vtec.service"]\n'
_MOVE = (_NO_MOVE + '[[systemd.moves]]\nfrom = ["timestd-vtec.service"]\n'
         'to = "hamsci-physics-vtec.service"\n')


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _physics_repo(base: Path):
    """A hamsci-physics checkout whose working tree declares no move, and a
    later commit that declares one.  Returns (old, new) commits."""
    repo = base / "hamsci-physics"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "deploy.toml").write_text(_NO_MOVE)
    (repo / "systemd").mkdir()
    (repo / "systemd" / "hamsci-physics-vtec.service").write_text("[Service]\n")
    _git(repo, "add", "deploy.toml", "systemd")
    _git(repo, "commit", "-q", "-m", "old")
    old = _git(repo, "rev-parse", "HEAD")
    (repo / "deploy.toml").write_text(_MOVE)
    _git(repo, "commit", "-q", "-am", "new")
    new = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", old)
    return old, new


def _systemd(states):
    """subprocess.run stand-in: systemctl answers from ``states``
    ({unit: (is-enabled, is-active)}), git runs for real."""
    calls = []

    def run(argv, **kw):
        argv = list(argv)
        calls.append(argv)
        if argv[0] == "git":
            return subprocess.run(argv, **kw)
        if argv[:2] == ["systemctl", "is-enabled"]:
            return subprocess.CompletedProcess(argv, 0, states.get(argv[2], ("", ""))[0] + "\n", "")
        if argv[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(
                argv, 0, states.get(argv[2], ("", "inactive"))[1] + "\n", "")
        if argv[:2] == ["systemctl", "show"]:
            return subprocess.CompletedProcess(argv, 0, "active\nyes\nloaded\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")
    run.calls = calls
    return run


_LIVE = {"timestd-vtec.service": ("enabled", "active"),
         "hamsci-physics-fusion.service": ("enabled", "active")}


def _moves(apply, base, *, enabled=True, plan=None, states=_LIVE, topology=None,
           local=(), **kw):
    lines = []
    run = _systemd(states)
    topology = {"hamsci-physics": enabled} if topology is None else topology
    with mock.patch("sigmond.drift.build_report.topology_enabled", return_value=topology), \
            mock.patch.object(smd, "_locally_managed", lambda: set(local)), \
            mock.patch.object(smd, "_ALIGN_UNIT_DIR", base / "units"):
        (base / "units").mkdir(exist_ok=True)
        steps = smd._align_unit_moves(apply, base=base, plan=plan, run=run,
                                      say=lines.append, **kw)
    return steps, lines, run.calls


def _stops(calls):
    return [c for c in calls if c[:2] in (["systemctl", "stop"], ["systemctl", "disable"],
                                          ["systemctl", "start"])]


def test_dry_run_lists_a_move_declared_at_the_target_commit(tmp_path):
    old, new = _physics_repo(tmp_path)
    plan = [align.Item("hamsci-physics", "move", old, new)]
    steps, lines, calls = _moves(False, tmp_path, plan=plan)
    assert ("  would move timestd-vtec.service -> hamsci-physics-vtec.service "
            "(hamsci-physics)") in lines
    assert not [c for c in calls if c[:2] in (["systemctl", "stop"], ["systemctl", "start"])]


def test_dry_run_reads_the_working_tree_when_the_plan_leaves_it(tmp_path):
    old, new = _physics_repo(tmp_path)
    plan = [align.Item("hamsci-physics", "current", old, old)]
    _, lines, _ = _moves(False, tmp_path, plan=plan)
    assert not [ln for ln in lines if "would move" in ln]


def test_dry_run_says_nothing_where_the_old_unit_never_ran(tmp_path):
    old, new = _physics_repo(tmp_path)
    _, lines, _ = _moves(False, tmp_path, plan=[align.Item("hamsci-physics", "move", old, new)],
                         states={})
    assert lines == []


def test_apply_moves_from_the_working_tree(tmp_path):
    old, new = _physics_repo(tmp_path)
    _git(tmp_path / "hamsci-physics", "checkout", "-q", new)   # the checkout moved
    steps, _, calls = _moves(True, tmp_path)
    assert [s.outcome for s in steps] == ["moved"]
    flat = [" ".join(c) for c in calls]
    assert flat.index("systemctl stop timestd-vtec.service") < \
        flat.index("systemctl start hamsci-physics-vtec.service")


def test_apply_holds_a_move_whose_owner_topology_disables(tmp_path):
    old, new = _physics_repo(tmp_path)
    _git(tmp_path / "hamsci-physics", "checkout", "-q", new)
    steps, _, calls = _moves(True, tmp_path, enabled=False)
    assert [s.outcome for s in steps] == ["ran"]
    assert steps[0].detail.startswith("held:")
    assert not [c for c in calls if c[:2] == ["systemctl", "stop"]]


def test_a_malformed_move_fails_on_apply(tmp_path):
    (tmp_path / "hamsci-physics").mkdir()
    (tmp_path / "hamsci-physics" / "deploy.toml").write_text(
        _NO_MOVE + '[[systemd.moves]]\nfrom = "a.service"\nto = "elsewhere.service"\n')
    steps, _, _ = _moves(True, tmp_path)
    assert [s.outcome for s in steps] == ["failed"]
    assert "elsewhere.service" in steps[0].detail


def _order_run(unit_move_steps):
    order = []
    m_moves = mock.Mock(side_effect=lambda *a, **k: order.append("unit moves")
                        or list(unit_move_steps))
    m_bringup = mock.Mock(side_effect=lambda **kw: order.append("bringup") or [])
    t = tac.AlignMakeLiveApplyTests("test_stage_order_moves_images_bringup_record_restart_checks")
    rc, out, m = t._run(extra_patches=[mock.patch.object(smd, "_align_unit_moves", m_moves),
                                       mock.patch.object(smd, "_align_bringup", m_bringup)])
    return rc, out, m, order, m_moves


def test_apply_runs_moves_before_bringup():
    rc, out, m, order, m_moves = _order_run([])
    assert order == ["unit moves", "bringup"]
    assert m_moves.call_args.args[0] is True
    assert rc == 0


def test_a_failed_move_holds_the_restarts():
    failed = align_apply.Step("hamsci-physics: timestd-vtec.service -> hamsci-physics-vtec.service",
                              "failed", "migrate exit 1; timestd-vtec.service left running")
    rc, out, m, order, _ = _order_run([failed])
    assert rc == 1
    m["restart"].assert_not_called()
    m["record"].assert_not_called()
    assert "failed — migrate exit 1" in out


def test_dry_run_calls_the_moves_with_the_plan():
    seen = []
    orig = tac._quiet_live_stages

    @contextlib.contextmanager
    def quiet():
        # run_apply stubs the moves with the other live stages; watch them here.
        with orig(), mock.patch.object(
                smd, "_align_unit_moves",
                side_effect=lambda apply, **k: seen.append((apply, k)) or []):
            yield

    with mock.patch.object(tac, "_quiet_live_stages", quiet):
        tac.run_apply(apply=False)
    assert seen and seen[0][0] is False
    assert any(it.component == "sigmond" for it in seen[0][1]["plan"])


def _dry_rc(steps):
    orig = tac._quiet_live_stages

    @contextlib.contextmanager
    def quiet():
        with orig(), mock.patch.object(smd, "_align_unit_moves",
                                       lambda apply, **k: list(steps)):
            yield

    live = dict(tac.APPLY_REL.components)
    with mock.patch.object(tac, "_quiet_live_stages", quiet):
        rc, out, _ = tac.run_apply(apply=False, live=live)
    return rc


def test_dry_run_counts_a_due_move_as_pending():
    assert _dry_rc([]) == 0
    assert _dry_rc([align_apply.Step("x: a.service -> b.service", "unchanged")]) == 0
    assert _dry_rc([align_apply.Step("x: a.service -> b.service", "would move", "...")]) == 1


def test_a_quiet_station_still_finishes_a_held_move():
    # The drift fault's fix for a held move reads `smd enable <owner>`, then
    # align: on a station already aligned, that align takes the quiet path.
    moved = align_apply.Step("hamsci-physics: timestd-vtec.service -> hamsci-physics-vtec.service",
                             "moved", "timestd-vtec.service stopped")
    seen = []
    t = tac.AlignMakeLiveApplyTests("test_stage_order_moves_images_bringup_record_restart_checks")
    rc, out, m = t._run(
        moves=[align_apply.Step("sigmond", "current")],
        extra_patches=[
            mock.patch.object(smd, "_align_staleness", lambda *a, **k: tac._staleness()),
            mock.patch.object(smd, "_align_device_steps", lambda **k: []),
            mock.patch.object(smd, "_align_declared_units_steps", lambda *a, **k: []),
            mock.patch.object(smd, "_align_unit_moves",
                              lambda apply, **k: seen.append(apply) or [moved])])
    assert "aligned — nothing to do" in out
    assert seen == [True]
    # A move made on the quiet path still reaches the history file.
    assert any(moved in c.args[1] for c in m["history"].call_args_list)
    assert "timestd-vtec.service stopped" in out
    m["bringup"].assert_not_called()
    assert rc == 0


# --- Fix round 1 -------------------------------------------------------------

def _moved_tree(tmp_path):
    old, new = _physics_repo(tmp_path)
    _git(tmp_path / "hamsci-physics", "checkout", "-q", new)
    return old, new


def test_no_restart_holds_the_move(tmp_path):
    _moved_tree(tmp_path)
    held = {}
    steps, _, calls = _moves(True, tmp_path, no_restart=True, held_out=held)
    assert [s.outcome for s in steps] == ["ran"]
    assert "waits for an align without --no-restart" in steps[0].detail
    assert _stops(calls) == []
    assert held == {"hamsci-physics-vtec.service": "timestd-vtec.service"}


def test_apply_passes_no_restart_to_the_moves():
    seen = []
    t = tac.AlignMakeLiveApplyTests("test_stage_order_moves_images_bringup_record_restart_checks")
    t._run(no_restart=True, extra_patches=[mock.patch.object(
        smd, "_align_unit_moves", lambda apply, **k: seen.append(k.get("no_restart")) or [])])
    assert seen == [True]


def test_dry_run_names_a_target_commit_not_fetched(tmp_path):
    old, _ = _physics_repo(tmp_path)
    steps, lines, _ = _moves(False, tmp_path,
                             plan=[align.Item("hamsci-physics", "move", old, "f" * 40)])
    assert f"  hamsci-physics: moves at {'f' * 12} not read (commit not fetched)" in lines
    assert [s.outcome for s in steps] == ["unread"]


def test_apply_links_the_new_unit_first(tmp_path):
    _moved_tree(tmp_path)
    steps, _, _ = _moves(True, tmp_path)
    link = tmp_path / "units" / "hamsci-physics-vtec.service"
    assert link.is_symlink()
    assert link.resolve() == (tmp_path / "hamsci-physics" / "systemd" /
                              "hamsci-physics-vtec.service").resolve()
    assert [s.outcome for s in steps] == ["moved"]


def test_apply_holds_a_move_whose_new_unit_is_not_installed(tmp_path):
    _moved_tree(tmp_path)
    (tmp_path / "hamsci-physics" / "systemd" / "hamsci-physics-vtec.service").unlink()
    steps, _, calls = _moves(True, tmp_path)
    assert [s.outcome for s in steps] == ["ran"]
    assert steps[0].detail == ("held: hamsci-physics-vtec.service not installed yet; "
                               "timestd-vtec.service keeps running")
    assert _stops(calls) == []


def test_a_locally_managed_owner_holds_the_move(tmp_path):
    _moved_tree(tmp_path)
    steps, _, calls = _moves(True, tmp_path, local={"hamsci-physics"})
    assert "manages hamsci-physics by hand" in steps[0].detail
    assert _stops(calls) == []


def test_an_owner_the_operator_stopped_holds_the_move(tmp_path):
    _moved_tree(tmp_path)
    steps, _, calls = _moves(True, tmp_path,
                             states={"timestd-vtec.service": ("enabled", "active")})
    assert steps[0].outcome == "ran"
    assert "hamsci-physics-fusion.service is stopped" in steps[0].detail
    assert _stops(calls) == []


def test_an_owner_topology_does_not_name_says_so(tmp_path):
    _moved_tree(tmp_path)
    steps, _, calls = _moves(True, tmp_path, topology={})
    assert steps[0].detail.startswith("held: topology does not name hamsci-physics")
    assert _stops(calls) == []


def test_dry_run_counts_a_failed_declaration_as_pending(tmp_path):
    (tmp_path / "hamsci-physics").mkdir()
    (tmp_path / "hamsci-physics" / "deploy.toml").write_text(
        _NO_MOVE + '[[systemd.moves]]\nfrom = "a.service"\nto = "elsewhere.service"\n')
    steps, lines, _ = _moves(False, tmp_path)
    assert [s.outcome for s in steps] == ["failed"]
    assert any("elsewhere.service" in ln for ln in lines)
    assert _dry_rc(steps) == 1
    assert _dry_rc([align_apply.Step("hamsci-physics", "unread", "x")]) == 1


def test_declared_units_leave_a_held_move_target_disabled(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "deploy.toml").write_text(
        '[systemd]\nunits = ["x.service", "x-new.timer", "x-other.timer"]\n')
    unit_dir = tmp_path / "units"; unit_dir.mkdir()
    timer = "[Timer]\nOnCalendar=hourly\n\n[Install]\nWantedBy=timers.target\n"
    for u in ("x.service", "x-new.timer", "x-other.timer"):
        (unit_dir / u).write_text(timer)
    calls, lines = [], []

    def fake_run(argv, **kw):
        argv = list(argv)
        calls.append(argv)
        if argv[:2] == ["systemctl", "is-enabled"]:
            return subprocess.CompletedProcess(argv, 1, "disabled\n", "")
        if argv[:2] == ["systemctl", "cat"]:
            return subprocess.CompletedProcess(argv, 0, timer, "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    with mock.patch("sigmond.catalog.load_catalog", return_value={"x": object()}), \
            mock.patch("sigmond.drift.build_report.topology_enabled", return_value={"x": True}), \
            mock.patch("sigmond.drift.build_report.systemctl_active", lambda u: True), \
            mock.patch("sigmond.drift.exceptions.DEFAULT_PATH", tmp_path / "none.toml"), \
            mock.patch.object(smd, "_locally_managed", lambda: set()), \
            mock.patch.object(smd, "_ALIGN_UNIT_DIR", unit_dir), \
            mock.patch("subprocess.run", fake_run):
        smd._align_enable_declared_units(True, base=tmp_path, say=lines.append,
                                         held_moves={"x-new.timer": "old-new.timer"})
    enables = [c[-1] for c in calls if c[:2] == ["systemctl", "enable"]]
    assert "x-new.timer" not in enables and "x-other.timer" in enables
    assert "  x: left x-new.timer disabled: its move from old-new.timer did not finish" in lines


def test_bringup_gets_the_moves_left_undone():
    def moves(apply, **k):
        k["held_out"]["timestd-iri-update.timer"] = "hamsci-physics-iri-update.timer"
        return []
    got = []
    t = tac.AlignMakeLiveApplyTests("test_stage_order_moves_images_bringup_record_restart_checks")
    t._run(extra_patches=[
        mock.patch.object(smd, "_align_unit_moves", moves),
        mock.patch.object(smd, "_align_bringup",
                          lambda **kw: got.append(kw.get("held_moves")) or [])])
    assert got == [{"timestd-iri-update.timer": "hamsci-physics-iri-update.timer"}]


# --- Fix round 2 -------------------------------------------------------------

def test_dry_run_holds_a_move_whose_new_unit_is_not_shipped(tmp_path):
    old, new = _physics_repo(tmp_path)
    repo = tmp_path / "hamsci-physics"
    _git(repo, "checkout", "-q", new)
    _git(repo, "rm", "-q", "systemd/hamsci-physics-vtec.service")
    _git(repo, "commit", "-q", "-m", "unshipped")
    unshipped = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", old)
    held = {}
    steps, lines, calls = _moves(False, tmp_path, held_out=held,
                                 plan=[align.Item("hamsci-physics", "move", old, unshipped)])
    assert ("  would hold: hamsci-physics-vtec.service not shipped "
            "(hamsci-physics; timestd-vtec.service keeps running)") in lines
    assert not [ln for ln in lines if "would move" in ln]
    assert held == {"hamsci-physics-vtec.service": "timestd-vtec.service"}
    # The working tree ships it; the target commit does not: the target decides.
    assert (repo / "systemd" / "hamsci-physics-vtec.service").is_file()


# I1 (v3.96 final review): an exception inside one owner's moves becomes a
# "failed" Step; it never escapes _align_unit_moves, so align still records
# history and reports, and _ALIGN_BLOCKING holds the restarts back.
_LABEL = "hamsci-physics: timestd-vtec.service -> hamsci-physics-vtec.service"


def test_link_units_that_raises_fails_the_move(tmp_path):
    _moved_tree(tmp_path)
    held = {}
    with mock.patch("sigmond.unit_moves.link_units",
                    side_effect=RuntimeError("Symlink loop from 'x'")):
        steps, _, calls = _moves(True, tmp_path, held_out=held)
    assert [(s.component, s.outcome) for s in steps] == [(_LABEL, "failed")]
    assert "Symlink loop" in steps[0].detail
    assert _stops(calls) == []
    assert held == {"hamsci-physics-vtec.service": "timestd-vtec.service"}


def test_a_clear_that_raises_in_align_fails_but_starts_the_new_unit(tmp_path):
    _moved_tree(tmp_path)
    with mock.patch("sigmond.unit_moves._clear_unit",
                    side_effect=PermissionError(13, "Permission denied")):
        steps, _, calls = _moves(True, tmp_path)
    assert [s.outcome for s in steps] == ["failed"]
    flat = [" ".join(c) for c in calls]
    assert flat.index("systemctl stop timestd-vtec.service") < \
        flat.index("systemctl start hamsci-physics-vtec.service")


def test_an_owner_check_that_raises_fails_the_move(tmp_path):
    _moved_tree(tmp_path)
    with mock.patch("sigmond.drift.units.declared_units", side_effect=OSError("EIO")):
        steps, lines, calls = _moves(True, tmp_path)
    assert [(s.component, s.outcome) for s in steps] == [(_LABEL, "failed")]
    assert "EIO" in steps[0].detail
    assert _stops(calls) == []


def test_dry_run_reports_a_move_that_raises(tmp_path):
    _moved_tree(tmp_path)
    with mock.patch("sigmond.drift.units.declared_units", side_effect=OSError("EIO")):
        steps, lines, _ = _moves(False, tmp_path)
    assert [s.outcome for s in steps] == ["failed"]
    assert any("failed" in ln and "EIO" in ln for ln in lines)


def test_a_move_that_raises_still_records_and_exits_1():
    """The make-live path with a raised-then-failed move: history recorded,
    nothing restarted, exit 1 (the existing _ALIGN_BLOCKING path)."""
    failed = align_apply.Step(_LABEL, "failed", "RuntimeError: Symlink loop")
    rc, out, m, order, _ = _order_run([failed])
    assert rc == 1
    m["restart"].assert_not_called()
    assert "failed — RuntimeError: Symlink loop" in out
