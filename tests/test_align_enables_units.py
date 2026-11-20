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
        st.enter_context(mock.patch("sigmond.drift.build_report.topology_enabled",
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


# --- sigmond's own timers (ruling (a), 2026-11-20) ---------------------------

import re  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent


def _install_sh_enabled_timers() -> set:
    """Every sigmond-*.timer install.sh enables: literal `systemctl enable`
    lines, and the names of a `for _t in ...` loop whose body enables "$_t"."""
    text = (_REPO / "install.sh").read_text()
    out = set()
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    for i, ln in enumerate(lines):
        if "systemctl enable" in ln:
            out.update(re.findall(r"\bsigmond-[\w.@-]+\.timer\b", ln))
        m = re.match(r"\s*for (\w+) in (.*); do\s*$", ln)
        if m and i + 1 < len(lines) and re.search(
                rf'systemctl enable\b.*"\${m.group(1)}"', lines[i + 1]):
            out.update(t for t in m.group(2).split() if t.endswith(".timer"))
    return out


def test_python_list_matches_install_sh():
    from sigmond.drift import units as drift_units
    assert set(drift_units.INSTALL_ENABLED_TIMERS) == _install_sh_enabled_timers()
    assert len(drift_units.INSTALL_ENABLED_TIMERS) == len(
        set(drift_units.INSTALL_ENABLED_TIMERS))


def _timers(*, archive=False, enabled=(), no_restart=False, rc=0, excepted=()):
    """Run the real _align_start_timers against a tmp unit dir holding every
    sigmond timer the repo ships; return the systemctl argv it ran."""
    calls = []

    def fake_run(argv, **kw):
        argv = list(argv)
        calls.append(argv)
        if argv[:2] == ["systemctl", "is-enabled"]:
            return subprocess.CompletedProcess(argv, 0 if argv[-1] in enabled else 1, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
        tmp = Path(tmp)
        unit_dir = tmp / "units"
        unit_dir.mkdir()
        for src in (_REPO / "systemd").glob("sigmond-*.timer"):
            (unit_dir / src.name).write_text(src.read_text())
        cfg = tmp / "archive.toml"
        if archive:
            cfg.write_text("uuid = 'x'\n")
        exc = tmp / "drift-exceptions.toml"
        if excepted:
            from datetime import datetime, timedelta, timezone
            today = datetime.now(timezone.utc).date()
            exc.write_text("".join(
                f'[[exception]]\nkey = "unit:{u}"\nreason = "A/B"\nby = "mjh"\n'
                f'kind = "test"\nstart = {today}\n'
                f'until = {today + timedelta(days=3)}\n' for u in excepted))
        st.enter_context(mock.patch("sigmond.archive.CONFIG", cfg))
        st.enter_context(mock.patch.object(
            smd, "_align_run", lambda argv, *, run, timeout, action, **kw: run(argv)))
        with contextlib.redirect_stdout(io.StringIO()):
            smd._align_start_timers(rc=rc, no_restart=no_restart, unit_dir=unit_dir,
                                    run=fake_run, exceptions_path=exc)
    return calls


def _enabled_by(calls):
    return {c[-1] for c in calls if c[:2] == ["systemctl", "enable"]}


def test_own_timers_install_set_enabled():
    from sigmond.drift import units as drift_units
    calls = _timers()
    assert _enabled_by(calls) == set(drift_units.INSTALL_ENABLED_TIMERS)
    # A clean run starts what it enables, as install.sh's `enable --now`.
    assert ["systemctl", "enable", "--now", "sigmond-storage.timer"] in calls


def test_already_enabled_timer_untouched():
    calls = _timers(enabled={"sigmond-storage.timer"})
    assert "sigmond-storage.timer" not in _enabled_by(calls)


def test_no_restart_enables_without_starting():
    calls = _timers(no_restart=True)
    assert ["systemctl", "enable", "sigmond-storage.timer"] in calls
    assert not any("--now" in c and c[-1] == "sigmond-storage.timer" for c in calls)


def test_archive_health_only_with_archive_toml():
    assert "sigmond-archive-health.timer" not in _enabled_by(_timers())
    assert "sigmond-archive-health.timer" in _enabled_by(_timers(archive=True))


def test_off_by_design_timers_untouched():
    touched = {c[-1] for c in _timers(archive=True) if c[:2] == ["systemctl", "enable"]}
    for t in ("sigmond-heartbeat.timer", "sigmond-timing-watchdog.timer",
              "sigmond-radiod-watchdog.timer", "sigmond-netguard.timer",
              "sigmond-decode-health-collect.timer",
              "sigmond-storage-trim-wspr.timer"):
        assert t not in touched


def test_drift_exception_keeps_timer_as_is():
    calls = _timers(excepted=("sigmond-timing-reconcile.timer",))
    assert "sigmond-timing-reconcile.timer" not in _enabled_by(calls)
