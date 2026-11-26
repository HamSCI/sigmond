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

_NO_MOVE = '[systemd]\nunits = ["hamsci-physics-vtec.service"]\n'
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
    _git(repo, "add", "deploy.toml")
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


_LIVE = {"timestd-vtec.service": ("enabled", "active")}


def _moves(apply, base, *, enabled=True, plan=None, states=_LIVE):
    lines = []
    run = _systemd(states)
    with mock.patch("sigmond.drift.build_report.topology_enabled",
                    return_value={"hamsci-physics": enabled}), \
            mock.patch.object(smd, "_ALIGN_UNIT_DIR", base / "units"):
        (base / "units").mkdir(exist_ok=True)
        steps = smd._align_unit_moves(apply, base=base, plan=plan, run=run, say=lines.append)
    return steps, lines, run.calls


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
    assert "timestd-vtec.service stopped" in out
    m["bringup"].assert_not_called()
    assert rc == 0
