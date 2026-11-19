import contextlib
import io
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from sigmond import align, align_apply
from tests.test_align_cli import _apply_args, _apply_patches, smd

REL = align.Release(tag="v3.53", manifest_text="m", appliance_commit="a" * 40,
                    components={"sigmond": "daba1f6"})


def _drive(wrote, dry=False, outcome="current", order=None, **ns):
    calls = []

    def fake_run(argv, **kw):
        calls.append(("run", list(argv)))
        return subprocess.CompletedProcess(argv, 0, "", "")

    def fake_install(repo, unit_dir, *, run=None, **kw):
        calls.append(("install", str(repo)))
        return wrote

    out = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
        _apply_patches(st, rel=REL, live={"sigmond": "daba1f6"})
        st.enter_context(mock.patch("sigmond.drift.units.install_own_units",
                                    fake_install))
        st.enter_context(mock.patch("subprocess.run", fake_run))
        st.enter_context(mock.patch.object(
            smd, "_align_run",
            lambda argv, *, run, timeout, action, **kw: run(argv)))
        st.enter_context(mock.patch("sigmond.align_apply.apply_plan",
                                    return_value=[align_apply.Step("sigmond", outcome)]))
        if order is not None:
            real_live, real_timers = smd._align_make_live, smd._align_start_timers

            def live(*a, **k):
                order.append("make_live")
                return real_live(*a, **k)

            def timers(**k):
                order.append(("timers", k["rc"], k["no_restart"]))
            st.enter_context(mock.patch.object(smd, "_align_make_live", live))
            st.enter_context(mock.patch.object(smd, "_align_start_timers", timers))
            st.enter_context(mock.patch.object(smd, "_align_refresh_drift",
                                               lambda a: None))
        with contextlib.redirect_stdout(out):
            smd.cmd_align(_apply_args(tmp, apply=not dry, **ns))
    return calls, out.getvalue(), tmp


def test_align_apply_installs_own_units():
    calls, out, tmp = _drive(["sigmond-archive-health.timer"])
    kinds = [c for c in calls if c[0] == "install"
             or c[1][:2] == ["systemctl", "daemon-reload"]]
    assert [k[0] for k in kinds] == ["install", "run"]
    assert kinds[0][1] == str(Path(tmp) / "sigmond")
    assert "sigmond-archive-health.timer" in out


def test_align_apply_no_reload_when_nothing_written():
    calls, out, _ = _drive([])
    assert [c[0] for c in calls if c[0] == "install"] == ["install"]
    assert not any(c[1][:2] == ["systemctl", "daemon-reload"]
                   for c in calls if c[0] == "run")


def test_dry_run_installs_nothing():
    calls, _, _ = _drive(["x.timer"], dry=True)
    assert not [c for c in calls if c[0] == "install"]


def test_blocked_sigmond_step_installs_nothing():
    calls, _, _ = _drive(["x.timer"], outcome="failed")
    assert not [c for c in calls if c[0] == "install"]


def test_timers_start_after_make_live():
    # Final review I1: the location-check timer's first tick must not land
    # inside align's bring-up.
    order = []
    _drive([], order=order)
    assert order[0] == "make_live" and order[1][0] == "timers"


def test_timers_hear_no_restart():
    order = []
    _drive([], order=order, no_restart=True)
    assert order[-1][0] == "timers" and order[-1][2] is True


def test_blocked_sigmond_step_starts_no_timers():
    order = []
    _drive(["x.timer"], outcome="failed", order=order)
    assert order == ["make_live"]
