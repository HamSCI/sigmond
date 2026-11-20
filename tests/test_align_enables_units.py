"""Task 5 (v3.94): `smd align` enables the declared units of running
components.  A unit a component adds after its first install
(timestd-events.timer, hamsci-physics-iri-update.timer) stayed disabled on
several stations on 2026-11-19, because nothing re-ran the enable step.

The real `cmd_align` runs, --apply and dry run, with the real
`_align_bringup` and the real installer enable step; only systemctl (via
subprocess.run), the bring-up's child smd runs, topology and the catalog
are stubbed."""

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

_DEPLOY = '[systemd]\nunits = ["x.service", "x-events.timer"]\n'
_TIMER = "[Timer]\nOnCalendar=hourly\n\n[Install]\nWantedBy=timers.target\n"


def _drive(*, dry=False, active=True, enabled=True, unit_file=True):
    """Run cmd_align against a tmp checkout of component `x`.  Returns the
    systemctl argv list and the printed output."""
    calls = []

    def fake_run(argv, **kw):
        argv = list(argv)
        calls.append(argv)
        ok = subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(argv, 0 if active else 3, "", "")
        if argv[:2] == ["systemctl", "is-enabled"]:
            state = "enabled" if argv[-1] == "x.service" else "disabled"
            return subprocess.CompletedProcess(argv, 0 if state == "enabled" else 1,
                                               state + "\n", "")
        if argv[:2] == ["systemctl", "cat"]:
            if argv[-1] == "x-events.timer":
                return subprocess.CompletedProcess(argv, 0, _TIMER, "")
            return subprocess.CompletedProcess(argv, 1, "", "No files found")
        return ok

    def fake_align_run(argv, *, run, timeout, action, **kw):
        # The bring-up's child smd runs (config render, manifest, doctor).
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    out = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
        base = Path(tmp)
        (base / "x").mkdir()
        (base / "x" / "deploy.toml").write_text(_DEPLOY)
        unit_dir = base / "etc-systemd"
        unit_dir.mkdir()
        if unit_file:
            (unit_dir / "x.service").write_text("[Service]\nExecStart=/bin/true\n")
            (unit_dir / "x-events.timer").write_text(_TIMER)
        real_bringup = smd._align_bringup
        _apply_patches(st, rel=REL, live={"sigmond": "daba1f6"})
        st.enter_context(mock.patch.object(smd, "_align_bringup", real_bringup))
        st.enter_context(mock.patch.object(smd, "_align_run", fake_align_run))
        st.enter_context(mock.patch.object(smd, "_align_own_units",
                                           lambda *a, **k: []))
        st.enter_context(mock.patch.object(smd, "_align_start_timers",
                                           lambda **k: None))
        st.enter_context(mock.patch.object(smd, "_ALIGN_UNIT_DIR", unit_dir))
        st.enter_context(mock.patch("sigmond.catalog.load_catalog",
                                    return_value={"x": object()}))
        st.enter_context(mock.patch("sigmond.drift.build_report._topology_enabled",
                                    return_value={"x": enabled}))
        st.enter_context(mock.patch("sigmond.uploader_manifest.MANIFEST_PATH",
                                    base / "pipelines.toml"))
        st.enter_context(mock.patch("subprocess.run", fake_run))
        st.enter_context(mock.patch("sigmond.align_apply.apply_plan",
                                    return_value=[align_apply.Step("sigmond", "current")]))
        with contextlib.redirect_stdout(out):
            smd.cmd_align(_apply_args(tmp, apply=not dry))
    return calls, out.getvalue()


def _mutating(calls):
    return [c for c in calls if c[:2] in (["systemctl", "enable"],
                                          ["systemctl", "start"])]


def test_enables_declared_timer_of_running_component():
    calls, out = _drive()
    muts = _mutating(calls)
    assert ["systemctl", "enable", "x-events.timer"] in muts
    # A timer only schedules; the component's service is never started.
    assert not any(c[-1] == "x.service" for c in muts)
    assert "x: enabled x-events.timer" in out


def test_skips_component_with_stopped_service():
    # Review Focus 2: KFS-OMNI stops hf-timestd on purpose.
    calls, out = _drive(active=False)
    assert _mutating(calls) == []
    assert "x-events.timer" not in out


def test_skips_topology_disabled():
    calls, _ = _drive(enabled=False)
    assert _mutating(calls) == []
    assert not [c for c in calls if c[:2] == ["systemctl", "is-active"]]


def test_skips_component_whose_main_service_is_missing():
    # A missing main service is a failed install: drift faults it, and
    # align's install stages own the repair.
    calls, _ = _drive(unit_file=False)
    assert _mutating(calls) == []


def test_dry_run_lists_enables_only():
    calls, out = _drive(dry=True)
    assert _mutating(calls) == []
    assert "would enable x-events.timer (x)" in out
    assert "would enable x.service" not in out
