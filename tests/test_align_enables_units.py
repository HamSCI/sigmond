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
import re
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


def _exceptions_text(units):
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).date()
    return "".join(
        f'[[exception]]\nkey = "unit:{u}"\nreason = "A/B"\nby = "mjh"\n'
        f'kind = "test"\nstart = {today}\n'
        f'until = {today + timedelta(days=3)}\n' for u in units)


def _drive(*, dry=False, active=True, enabled=True, unit_file=True,
           timer_state="disabled", exceptions=None, no_restart=False,
           quiet=False, own_timers=()):
    """Run cmd_align against a tmp checkout of component `x`.  Returns the
    systemctl argv list and the printed output.  ``exceptions`` is the
    drift-exceptions file text (None: no file); ``quiet`` makes --apply
    find the station aligned with nothing to do; ``own_timers`` are sigmond
    timers installed (and disabled) in the unit dir."""
    calls = []

    def fake_run(argv, **kw):
        argv = list(argv)
        calls.append(argv)
        ok = subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["systemctl", "is-active"]:
            return subprocess.CompletedProcess(argv, 0 if active else 3, "", "")
        if argv[:2] == ["systemctl", "is-enabled"]:
            state = ("enabled" if argv[-1] == "x.service"
                     else timer_state if argv[-1] == "x-events.timer" else "disabled")
            return subprocess.CompletedProcess(argv, 0 if state == "enabled" else 1,
                                               state + "\n", "")
        if argv[:2] == ["systemctl", "cat"]:
            if argv[-1] == "x-events.timer" and timer_state != "not-found":
                return subprocess.CompletedProcess(argv, 0, _TIMER, "")
            return subprocess.CompletedProcess(argv, 1, "", "No files found")
        return ok

    def fake_align_run(argv, *, run, timeout, action, **kw):
        # The bring-up's child smd runs (config render, manifest, doctor)
        # succeed; systemctl answers as fake_run does.
        if argv[0] == "systemctl":
            return fake_run(argv)
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
        for t in own_timers:
            (unit_dir / t).write_text(_TIMER)
        exc = base / "drift-exceptions.toml"
        if exceptions is not None:
            exc.write_text(exceptions)
        st.enter_context(mock.patch("sigmond.drift.exceptions.DEFAULT_PATH", exc))
        st.enter_context(mock.patch("sigmond.archive.CONFIG", base / "archive.toml"))
        real_bringup = smd._align_bringup
        _apply_patches(st, rel=REL, live={"sigmond": "daba1f6"})
        st.enter_context(mock.patch.object(smd, "_align_bringup", real_bringup))
        st.enter_context(mock.patch.object(smd, "_align_run", fake_align_run))
        st.enter_context(mock.patch.object(smd, "_align_own_units",
                                           lambda *a, **k: []))
        if not own_timers:
            st.enter_context(mock.patch.object(smd, "_align_start_timers",
                                               lambda **k: None))
        if quiet:
            st.enter_context(mock.patch.object(smd, "_align_aligned_release",
                                               lambda: smd._align_record_key(REL)))
            st.enter_context(mock.patch.object(smd, "_align_live_passed",
                                               lambda live: True))
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
            smd.cmd_align(_apply_args(tmp, apply=not dry, no_restart=no_restart))
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



def test_drift_exception_keeps_declared_timer_as_is():
    # B4 suspends HPPS by hand: hf-timestd's watchdog timers restart the
    # recorder, so an exception must keep align from re-enabling them.
    calls, out = _drive(exceptions=_exceptions_text(["x-events.timer"]))
    assert _mutating(calls) == []
    assert "drift exception covers unit:x-events.timer" in out


def test_unreadable_exceptions_enable_no_declared_unit():
    calls, out = _drive(exceptions="this is [not toml\n")
    assert _mutating(calls) == []


def test_no_restart_enables_declared_timer_without_starting():
    calls, out = _drive(no_restart=True)
    assert ["systemctl", "enable", "x-events.timer"] in calls
    assert ["systemctl", "start", "x-events.timer"] not in calls
    assert "not started (--no-restart): x-events.timer" in out


def test_quiet_apply_enables_declared_timer():
    calls, out = _drive(quiet=True)
    assert "nothing to do" in out
    assert ["systemctl", "enable", "x-events.timer"] in calls


def test_dry_run_lists_own_timers():
    calls, out = _drive(dry=True, own_timers=("sigmond-storage.timer",))
    assert _mutating(calls) == []
    assert "would enable sigmond-storage.timer (sigmond)" in out


def test_not_found_declared_unit_adds_no_step():
    # A declared unit systemd does not know is a drift fault; align says
    # nothing about it each run.
    calls, out = _drive(timer_state="not-found")
    assert "declared units" not in out
    assert "not-found" not in out

# --- sigmond's own timers (ruling (a), 2026-11-20) ---------------------------


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


def _timers(*, archive=False, enabled=(), no_restart=False, rc=0, excepted=(),
            masked=(), out=None):
    """Run the real _align_start_timers against a tmp unit dir holding every
    sigmond timer the repo ships; return the systemctl argv it ran."""
    calls = []

    def fake_run(argv, **kw):
        argv = list(argv)
        calls.append(argv)
        if argv[:2] == ["systemctl", "is-enabled"]:
            if argv[-1] in masked:
                return subprocess.CompletedProcess(argv, 1, "masked\n", "")
            if argv[-1] in enabled:
                return subprocess.CompletedProcess(argv, 0, "enabled\n", "")
            return subprocess.CompletedProcess(argv, 1, "disabled\n", "")
        if argv[:2] == ["systemctl", "enable"] and argv[-1] in masked:
            return subprocess.CompletedProcess(argv, 1, "", "Unit is masked.")
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
            exc.write_text(_exceptions_text(excepted))
        st.enter_context(mock.patch("sigmond.archive.CONFIG", cfg))
        st.enter_context(mock.patch.object(
            smd, "_align_run", lambda argv, *, run, timeout, action, **kw: run(argv)))
        with contextlib.redirect_stdout(out if out is not None else io.StringIO()):
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


def test_masked_timer_silent():
    out = io.StringIO()
    calls = _timers(masked={"sigmond-storage.timer"}, out=out)
    assert "sigmond-storage.timer" not in _enabled_by(calls)
    assert "could not enable" not in out.getvalue()
