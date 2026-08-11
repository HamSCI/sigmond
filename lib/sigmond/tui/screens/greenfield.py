"""Greenfield — the guided, CLI-free station bring-up wizard (#16 epic).

A thin Textual front-end over the ``smd bringup`` engine.  The operator
picks a station profile, enters station identity ONCE (reporter id + grid
required; callsign + PSWS station id optional), previews the plan, then
presses Begin — and the wizard streams ``smd bringup`` live in-TUI with a
plain-language verdict and fix-it actions at the end.

Design (see memory: sigmond-greenfield-tui-architecture):
  • We do NOT re-implement orchestration.  ``sigmond.bringup.build_plan``
    is pure and ``cmd_bringup`` is the executor — this screen just collects
    inputs and drives ``smd bringup --non-interactive``.
  • Run mode is CAPTURE-AND-STREAM: the bring-up runs in a worker and its
    output streams into a live modal.  Because a captured pipe has no TTY,
    we PRE-ELEVATE (``sudo -n -- env SIGMOND_ALLOW_SUDO=1 smd bringup …``)
    so smd's self-elevation never deadlocks on a password prompt.  When
    ``sudo -n`` needs a password we suspend once to cache creds, then stream.
  • Because the run is non-interactive, radiod is configured with antenna
    DEFAULTS — the verdict reminds the operator to fine-tune the antenna
    later with ``smd config edit radiod``.
  • Plan preview uses ``smd bringup … --dry-run`` which returns before any
    elevation, so it needs no sudo.
  • The Equipment panel is driven by ``sigmond.hardware.gate_checks`` — the
    SAME probe-and-decide code ``smd bringup``'s ``--require-hardware`` gate
    runs — so what the operator reads here is what bring-up will decide.  It
    is deliberately NOT a second detection implementation (there used to be
    one, ``sigmond.hardware_detect``, and it disagreed on every device).
  • The readiness panel calls ``sigmond.readiness.run_gate`` in-process (the
    library behind ``smd admin readiness``) before and after the run, so the
    operator sees whether the station is actually complete.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button, Checkbox, Input, Label, RadioButton, RadioSet, Static,
)
from textual.worker import Worker, WorkerState

from ..mutation import ConfirmModal, UpdateOutputModal, suspend_and_run_sudo


def _smd_binary() -> str:
    """Resolve the smd entry point (mirrors install._smd_binary)."""
    argv0 = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if argv0 and os.path.isfile(argv0) and os.path.basename(argv0) == 'smd':
        return argv0
    found = shutil.which('smd')
    return found or '/usr/local/bin/smd'


def _load_profiles() -> dict:
    try:
        from ...catalog import load_profiles
        return load_profiles()
    except Exception:                                   # noqa: BLE001
        return {}


# Presentation order only.  `dasi2` leads because it is also `smd bringup`'s
# own default (bin/smd: `args.profile = ... or 'dasi2'`), so the pre-selected
# radio button and a bare `smd bringup` agree.  The one-line summary next to
# each name is the catalog profile's own ``description`` field — read, never
# hardcoded, so the wizard cannot drift from etc/catalog.toml.
_PROFILE_ORDER = ["dasi2", "base", "client"]


def _profile_blurb(prof) -> str:
    return (getattr(prof, "description", "") or "").strip()


# ---------------------------------------------------------------------------
# Equipment + readiness probes
#
# Each is a module-level function taking no screen state, so tests can patch
# exactly one host-touching call site (project pattern, dc48e80) and the
# screen never reads real host state under test.
# ---------------------------------------------------------------------------

# The three tri-states bring-up prints under "required external devices".
# Word-for-word from bin/smd's _bringup_hardware_gate so the wizard and the
# CLI transcript read identically.
_PRESENCE_WORD = {"yes": "present", "no": "MISSING", "unknown": "unconfirmed",
                  "na": "n/a"}
_PRESENCE_MARK = {"yes": "[green]\u2713[/]", "no": "[red]\u2717[/]",
                  "unknown": "[yellow]?[/]", "na": "[dim]\u2014[/]"}


def _gate_checks(prof, local: bool) -> list:
    """Rows of the bring-up hardware gate as ``(presence, label, hint)``.

    Delegates to ``sigmond.hardware.gate_checks`` — the exact function
    ``smd bringup`` uses — so a device that reads MISSING here is a device
    that would hard-stop ``smd bringup --require-hardware``.
    """
    from ...hardware import gate_checks
    return [(c.presence.value, c.label, c.hint)
            for c in gate_checks(prof, local)]


def _ts1_state() -> tuple:
    """TS-1 refclock presence as ``(presence, detail, armed_note)``.

    TS-1 is a HamSCI HF BPSK time-reference TRANSMISSION, not a USB device:
    nothing on this host can detect it before hf-timestd is decoding.  So it
    is NOT part of the bring-up hardware gate, and it is reported from
    hf-timestd's published authority snapshot instead.

    "TS-1 detected" and "T6 armed" are DIFFERENT facts and are reported
    separately: shipped images set ``t6_pps.enabled = false``, so a station
    can be receiving TS-1 with the T6 fine stage disarmed.  ``T6`` appearing
    in ``t_level_available`` means the signal is witnessed; ``t_level_active
    == "T6"`` means it is actually the operating tier.
    """
    from ..format import ERR_NOT_FOUND, read_authority_snapshot
    snap, err = read_authority_snapshot()
    if snap is None:
        if err == ERR_NOT_FOUND:
            return ("unknown",
                    "hf-timestd is not publishing an authority snapshot yet "
                    "\u2014 TS-1 is decoded off the air AFTER bring-up; it is "
                    "not a bring-up gate",
                    "")
        return ("unknown",
                f"authority snapshot {err} \u2014 TS-1 presence undetermined", "")
    available = list(snap.t_level_available or [])
    active = snap.t_level_active or "\u2014"
    if "T6" in available:
        presence = "yes"
        detail = ("TS-1 witnessed \u2014 T6 is in hf-timestd's available tiers "
                  f"({', '.join(available)})")
    else:
        presence = "no"
        detail = ("TS-1 not witnessed \u2014 hf-timestd's available tiers are "
                  f"{', '.join(available) or '(none)'}")
    if active == "T6":
        armed = "T6 fine stage is ARMED and active (operating tier T6)."
    else:
        armed = (f"T6 fine stage NOT armed \u2014 operating tier is {active}. "
                 "A fresh install ships t6_pps.enabled=false; TS-1 hardware "
                 "presence does NOT by itself improve the timing tier.")
    return (presence, detail, armed)


def _readiness_report(profile: str, with_optional: bool, gate: str = "auto"):
    """``smd admin readiness`` verdict as a dict, or ``None`` on failure.

    Calls ``sigmond.readiness.run_gate`` in-process rather than parsing the
    CLI's text output; ``GateReport.as_dict()`` is the same payload
    ``smd admin readiness --json`` prints.
    """
    from ...readiness import run_gate
    try:
        return run_gate(gate=gate, profile=profile,
                        with_optional=with_optional).as_dict()
    except Exception:                                   # noqa: BLE001
        return None


class _BringupModal(UpdateOutputModal):
    """Streaming modal for the bring-up run that also remembers the exit code.

    ``UpdateOutputModal`` already streams a Popen's stdout+stderr line by line
    and shows a ✓/⚠ verdict by exit code; we subclass only to dismiss with the
    real return code so the Greenfield screen can render a verdict + fix-its.
    """

    def __init__(self, cmd: list, **kwargs) -> None:
        super().__init__("Guided bring-up — smd bringup", cmd, **kwargs)
        self._rc: int | None = None

    def on_worker_state_changed(self, event) -> None:        # noqa: D401
        super().on_worker_state_changed(event)
        if (event.worker.name == "uom-run"
                and event.state == WorkerState.SUCCESS):
            try:
                self._rc = event.worker.result[1]
            except Exception:                                # noqa: BLE001
                self._rc = None

    def _result_code(self) -> int:
        if self._rc is not None:
            return self._rc
        return 0 if self._done else 1

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "uom-dismiss":
            self.dismiss(self._result_code())

    def action_try_dismiss(self) -> None:
        if self._done:
            self.dismiss(self._result_code())


class GreenfieldScreen(Vertical):
    """Guided station bring-up — pick a profile, enter identity, go."""

    DEFAULT_CSS = """
    GreenfieldScreen { padding: 1; }
    GreenfieldScreen .gf-title { text-style: bold; margin-bottom: 1; }
    GreenfieldScreen .gf-section { text-style: bold; margin-top: 1; }
    GreenfieldScreen #gf-intro { color: $text-muted; margin-bottom: 1; }
    GreenfieldScreen RadioSet { height: auto; margin-bottom: 1; }
    GreenfieldScreen .gf-field { height: 3; }
    GreenfieldScreen .gf-field Label { width: 22; content-align: left middle; }
    GreenfieldScreen .gf-field Input { width: 36; }
    GreenfieldScreen #gf-remote-row { display: none; }
    GreenfieldScreen #gf-remote-row.show { display: block; }
    GreenfieldScreen #gf-optional { display: none; }
    GreenfieldScreen #gf-optional.show { display: block; }
    GreenfieldScreen #gf-optional-list { color: $text-muted; }
    GreenfieldScreen #gf-equip-intro { color: $text-muted; }
    GreenfieldScreen #gf-equip { margin-bottom: 1; }
    GreenfieldScreen #gf-equip-actions { height: 3; }
    GreenfieldScreen #gf-readiness { margin-top: 1; }
    GreenfieldScreen #gf-readiness-actions { height: 3; }
    GreenfieldScreen #gf-actions { height: 3; margin-top: 1; }
    GreenfieldScreen #gf-actions Button { margin-right: 1; }
    GreenfieldScreen #gf-status { margin-top: 1; }
    GreenfieldScreen #gf-fixits { height: auto; margin-top: 1; display: none; }
    GreenfieldScreen #gf-fixits.show { display: block; }
    GreenfieldScreen #gf-fixits Button { margin-right: 1; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._profiles = _load_profiles()
        self._profile_names = [p for p in _PROFILE_ORDER if p in self._profiles]
        # any catalog profiles we don't have a fixed order for, appended
        self._profile_names += [p for p in sorted(self._profiles)
                                if p not in self._profile_names]

    # ----- compose --------------------------------------------------------

    def compose(self):
        yield Static("Guided station bring-up", classes="gf-title")
        yield Static(
            "Pick a station profile and enter your identity once.  Preview the "
            "plan, then Begin — sigmond installs, configures, and starts the "
            "whole station, showing live progress.  No shell needed.",
            id="gf-intro")

        yield Static("1 · Station profile", classes="gf-section")
        with RadioSet(id="gf-profile"):
            for name in self._profile_names:
                blurb = _profile_blurb(self._profiles.get(name))
                label = f"{name}  \u2014  {blurb}" if blurb else name
                yield RadioButton(label, value=(name == self._profile_names[0]),
                                  id=f"gf-prof-{name}")

        yield Static("2 · Equipment", classes="gf-section")
        yield Static(
            "What [bold]smd bringup[/] will check before it installs anything "
            "— the same probes its --require-hardware gate acts on.",
            id="gf-equip-intro")
        yield Static("[dim]checking…[/]", id="gf-equip")
        with Horizontal(id="gf-equip-actions"):
            yield Button("Re-check equipment", id="gf-equip-refresh")

        yield Static("3 · Station identity", classes="gf-section")
        with Horizontal(classes="gf-field"):
            yield Label("Reporter id *")
            yield Input(placeholder="e.g. AC0G/S", id="gf-reporter")
        with Horizontal(classes="gf-field"):
            yield Label("Grid square *")
            yield Input(placeholder="e.g. EM38ww", id="gf-grid")
        with Horizontal(classes="gf-field"):
            yield Label("Callsign")
            yield Input(placeholder="optional — e.g. AC0G", id="gf-callsign")
        with Horizontal(classes="gf-field"):
            yield Label("PSWS station id")
            yield Input(placeholder="optional", id="gf-psws")
        with Horizontal(classes="gf-field", id="gf-remote-row"):
            yield Label("Remote radiod DNS")
            yield Input(placeholder="e.g. bee3-status.local", id="gf-remote")

        # Optional / discretionary clients — shown only for profiles that
        # declare any.  All-or-nothing (mirrors bringup's --with-optional);
        # install a single one later with `smd install <name>`.
        with Vertical(id="gf-optional"):
            yield Static("4 · Optional clients", classes="gf-section")
            yield Checkbox("Also install this profile's optional clients",
                           id="gf-with-optional")
            yield Static("", id="gf-optional-list")

        yield Static("Station readiness  (smd admin readiness)",
                     classes="gf-section")
        yield Static("[dim]checking…[/]", id="gf-readiness")
        with Horizontal(id="gf-readiness-actions"):
            yield Button("Re-check readiness", id="gf-readiness-refresh")

        with Horizontal(id="gf-actions"):
            yield Button("Preview plan", id="gf-preview", variant="default")
            yield Button("Begin bring-up", id="gf-begin", variant="success")

        yield Static("", id="gf-status")
        with Horizontal(id="gf-fixits"):
            yield Button("Edit antenna", id="gf-fix-antenna", variant="primary")
            yield Button("Open Validate", id="gf-fix-validate", variant="default")
            yield Button("Re-run bring-up", id="gf-fix-rerun", variant="default")

    def on_mount(self) -> None:
        self._sync_required_hints()
        self._refresh_equipment()
        self._refresh_readiness()

    # ----- equipment panel -------------------------------------------------

    def _refresh_equipment(self) -> None:
        """Probe the bring-up hardware gate + TS-1 off the UI thread.

        The probes shell out (lsusb, each client's `inventory --json`, a
        5 s GPSDO NMEA sample, the RM3100 bus poke) so they must not run on
        the event loop — several seconds is normal on a cold host.
        """
        name = self._selected_profile()
        prof = self._profiles.get(name)
        local = self._profile_is_local(name)
        self.query_one("#gf-equip", Static).update("[dim]checking…[/]")

        def _probe():
            try:
                rows = _gate_checks(prof, local)
            except Exception as exc:                     # noqa: BLE001
                rows = exc
            try:
                ts1 = _ts1_state()
            except Exception:                            # noqa: BLE001
                ts1 = ("unknown", "TS-1 presence undetermined", "")
            return {"rows": rows, "ts1": ts1}

        self.run_worker(_probe, thread=True, name="gf-equip")

    def _render_equipment(self, data: dict) -> None:
        rows = data.get("rows")
        lines = []
        if isinstance(rows, Exception):
            lines.append(f"[red]hardware probe failed: {rows}[/]")
        elif not rows:
            lines.append(
                "[dim]This profile needs no external devices attached.[/]")
        else:
            for presence, label, hint in rows:
                mark = _PRESENCE_MARK.get(presence, "[yellow]?[/]")
                word = _PRESENCE_WORD.get(presence, presence)
                lines.append(f"  {mark} {label}  —  {word}")
                if presence != "yes":
                    lines.append(f"      [dim]{hint}[/]")
        presence, detail, armed = data.get(
            "ts1", ("unknown", "TS-1 presence undetermined", ""))
        mark = _PRESENCE_MARK.get(presence, "[yellow]?[/]")
        word = _PRESENCE_WORD.get(presence, presence)
        lines.append(f"  {mark} TS-1 refclock  —  {word}")
        lines.append(f"      [dim]{detail}[/]")
        if armed:
            lines.append(f"      [dim]{armed}[/]")
        self.query_one("#gf-equip", Static).update("\n".join(lines))

    # ----- readiness panel -------------------------------------------------

    def _refresh_readiness(self, *, label: str = "now") -> None:
        name = self._selected_profile()
        with_optional = self._with_optional_checked()
        self.query_one("#gf-readiness", Static).update("[dim]checking…[/]")

        def _probe():
            return {"label": label,
                    "report": _readiness_report(name, with_optional)}

        self.run_worker(_probe, thread=True, name="gf-readiness")

    def _render_readiness(self, data: dict) -> None:
        rep = data.get("report")
        when = data.get("label") or "now"
        widget = self.query_one("#gf-readiness", Static)
        if not rep:
            widget.update(
                "[yellow]readiness gate could not run — check "
                "[bold]smd admin readiness[/] from a shell.[/]")
            return
        counts = rep.get("counts") or {}
        head = (f"gate [bold]{rep.get('gate')}[/] · profile "
                f"[bold]{rep.get('profile')}[/] ({when}): ")
        summary = (f"{counts.get('pass', 0)} pass, {counts.get('warn', 0)} "
                   f"warn, {counts.get('fail', 0)} fail")
        if rep.get("ready"):
            lines = [head + f"[green]READY[/] — {summary}"]
        else:
            lines = [head + f"[red]NOT READY[/] — {summary}"]
        for r in rep.get("results") or []:
            if r.get("status") in ("fail", "warn"):
                colour = "red" if r["status"] == "fail" else "yellow"
                lines.append(
                    f"  [{colour}]{r['status']}[/] {r.get('name')} — "
                    f"{r.get('detail', '')}")
        widget.update("\n".join(lines))

    def _with_optional_checked(self) -> bool:
        try:
            return bool(self.query_one("#gf-with-optional", Checkbox).value)
        except Exception:                                # noqa: BLE001
            return False

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.state != WorkerState.SUCCESS:
            return
        result = event.worker.result
        if not isinstance(result, dict):
            return
        if event.worker.name == "gf-equip":
            self._render_equipment(result)
        elif event.worker.name == "gf-readiness":
            self._render_readiness(result)

    # ----- profile-dependent UI ------------------------------------------

    def _selected_profile(self) -> str:
        rs = self.query_one("#gf-profile", RadioSet)
        btn = rs.pressed_button
        if btn is not None and btn.id and btn.id.startswith("gf-prof-"):
            return btn.id[len("gf-prof-"):]
        return self._profile_names[0] if self._profile_names else "dasi2"

    def _profile_clients(self, name: str) -> list:
        prof = self._profiles.get(name)
        return list(getattr(prof, "clients", []) or [])

    def _profile_optional(self, name: str) -> list:
        prof = self._profiles.get(name)
        return list(getattr(prof, "optional", []) or [])

    def _profile_is_local(self, name: str) -> bool:
        prof = self._profiles.get(name)
        return bool(getattr(prof, "local_radiod_infra", []) or [])

    def _requirements(self, name: str) -> tuple[bool, bool]:
        """(needs_reporter, needs_grid) — mirrors cmd_bringup's logic."""
        clients = self._profile_clients(name)
        needs_reporter = any(c in clients
                             for c in ("wspr-recorder", "psk-recorder"))
        needs_grid = ("hf-timestd" in clients) or needs_reporter
        return needs_reporter, needs_grid

    def _sync_required_hints(self) -> None:
        name = self._selected_profile()
        needs_reporter, needs_grid = self._requirements(name)
        is_local = self._profile_is_local(name)

        # Star the truly-required fields for the chosen profile.
        rep_label = self.query_one("#gf-reporter").parent.query_one(Label)
        grid_label = self.query_one("#gf-grid").parent.query_one(Label)
        rep_label.update("Reporter id *" if needs_reporter else "Reporter id")
        grid_label.update("Grid square *" if needs_grid else "Grid square")

        # Remote-radiod DNS only matters for decode-only (no local infra).
        row = self.query_one("#gf-remote-row")
        row.set_class(not is_local, "show")

        # Optional clients section: only for profiles that declare any.
        optional = self._profile_optional(name)
        opt_box = self.query_one("#gf-optional")
        opt_box.set_class(bool(optional), "show")
        if optional:
            self.query_one("#gf-optional-list", Static).update(
                "Adds: " + ", ".join(optional))
            self.query_one("#gf-with-optional", Checkbox).value = False

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        # --with-optional changes the component set the readiness gate
        # enumerates, so the verdict has to follow the checkbox.
        if event.checkbox.id == "gf-with-optional":
            self._refresh_readiness()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        self._sync_required_hints()
        self.query_one("#gf-status", Static).update("")
        # Both panels are profile-dependent: the gate rows follow the
        # profile's clients/local_radiod_infra, the readiness gate follows
        # its component list.
        self._refresh_equipment()
        self._refresh_readiness()

    # ----- input gathering / validation ----------------------------------

    def _gather(self) -> dict:
        def val(wid: str) -> str:
            return self.query_one(wid, Input).value.strip()
        profile = self._selected_profile()
        with_optional = bool(self._profile_optional(profile)) and \
            self.query_one("#gf-with-optional", Checkbox).value
        return {
            "profile": profile,
            "reporter": val("#gf-reporter"),
            "grid": val("#gf-grid"),
            "callsign": val("#gf-callsign"),
            "psws": val("#gf-psws"),
            "remote": val("#gf-remote"),
            "with_optional": with_optional,
        }

    def _missing_required(self, g: dict) -> list:
        needs_reporter, needs_grid = self._requirements(g["profile"])
        missing = []
        if needs_reporter and not g["reporter"]:
            missing.append("Reporter id")
        if needs_grid and not g["grid"]:
            missing.append("Grid square")
        if not self._profile_is_local(g["profile"]) and not g["remote"]:
            missing.append("Remote radiod DNS")
        return missing

    def _build_argv(self, g: dict, *, dry_run: bool) -> list:
        argv = [_smd_binary(), "bringup", g["profile"], "--non-interactive"]
        if g["reporter"]:
            argv += ["--reporter", g["reporter"]]
        if g["grid"]:
            argv += ["--grid", g["grid"]]
        if g["callsign"]:
            argv += ["--callsign", g["callsign"]]
        if g["psws"]:
            argv += ["--psws-station-id", g["psws"]]
        if not self._profile_is_local(g["profile"]) and g["remote"]:
            argv += ["--remote-radiod", g["remote"]]
        if g.get("with_optional"):
            argv.append("--with-optional")
        if dry_run:
            argv.append("--dry-run")
        return argv

    # ----- button handlers ------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "gf-equip-refresh":
            self._refresh_equipment()
        elif bid == "gf-readiness-refresh":
            self._refresh_readiness()
        elif bid == "gf-preview":
            self._preview()
        elif bid == "gf-begin":
            self._begin()
        elif bid == "gf-fix-antenna":
            self._edit_antenna()
        elif bid == "gf-fix-validate":
            self.app.action_show_validate()
        elif bid == "gf-fix-rerun":
            self.query_one("#gf-fixits").remove_class("show")
            self.query_one("#gf-status", Static).update("")

    def _preview(self) -> None:
        g = self._gather()
        # --dry-run makes no changes and needs no sudo; it prints the staged
        # plan + any environment blockers.  Stream it in the same modal.
        # PYTHONUNBUFFERED=1: smd's progress (_heading/_info/_ok via print) is
        # the PARENT process's stdout, which Python block-buffers on a non-TTY
        # pipe — so without this the stage scaffolding would dump all at once
        # at the end instead of streaming live.
        argv = ["env", "PYTHONUNBUFFERED=1", *self._build_argv(g, dry_run=True)]
        self.app.push_screen(_BringupModal(argv))

    def _begin(self) -> None:
        g = self._gather()
        missing = self._missing_required(g)
        status = self.query_one("#gf-status", Static)
        if missing:
            status.update(
                f"[yellow]Fill in: {', '.join(missing)} "
                f"(required for the {g['profile']} profile)[/]")
            return

        is_local = self._profile_is_local(g["profile"])
        clients = ", ".join(self._profile_clients(g["profile"])) or "(none)"
        opt_line = ""
        if g.get("with_optional"):
            opt = ", ".join(self._profile_optional(g["profile"])) or "(none)"
            opt_line = f"  optional: {opt}\n"
        body = (
            f"Install, configure, and start the [bold]{g['profile']}[/] station "
            f"({'LOCAL' if is_local else 'REMOTE'} radiod).\n\n"
            f"  clients:  {clients}\n"
            f"{opt_line}"
            f"  reporter: {g['reporter'] or '—'}\n"
            f"  grid:     {g['grid'] or '—'}\n"
            f"  callsign: {g['callsign'] or '—'}\n\n"
            "This runs the full bring-up and may take a while (FFT wisdom can "
            "take minutes — or hours on a cold first build).  radiod is "
            "configured with [bold]antenna defaults[/]; fine-tune the antenna "
            "afterwards with the verdict's [bold]Edit antenna[/] action.")

        def _after_confirm(ok: bool) -> None:
            if ok:
                self._run_bringup(g)

        self.app.push_screen(
            ConfirmModal(title=f"Begin {g['profile']} bring-up?", body=body,
                         yes_label="Begin", yes_variant="success"),
            _after_confirm,
        )

    def _ensure_sudo(self) -> bool:
        """Make sure ``sudo -n`` will succeed for the streamed run.

        A streamed (captured) child can't field a password prompt, so we
        pre-cache credentials here on the main thread: try ``sudo -n true``;
        if it needs a password, suspend once and run ``sudo -v`` so the
        operator authenticates in the real terminal.  Returns True when the
        run can proceed passwordless from here on.
        """
        fast = subprocess.run(["sudo", "-n", "true"],
                              capture_output=True, text=True)
        if fast.returncode == 0:
            return True
        with self.app.suspend():
            print("\nsigmond needs administrator rights to bring up the "
                  "station.\n")
            r = subprocess.run(["sudo", "-v"])
        if r.returncode != 0:
            self.query_one("#gf-status", Static).update(
                "[red]Could not get administrator rights — bring-up "
                "cancelled.[/]")
            return False
        return True

    def _run_bringup(self, g: dict) -> None:
        if not self._ensure_sudo():
            return
        # Pre-elevate: when euid==0 smd's _need_root returns without prompting,
        # and the SIGMOND_ALLOW_SUDO marker satisfies its top-of-main guard.
        # PYTHONUNBUFFERED=1 forces smd's own progress lines (the parent
        # process's stdout) to flush per-line; without it Python block-buffers
        # them on the captured pipe and the stage/checkpoint scaffolding only
        # appears in one delayed dump at the end (confirmed on a live run).
        argv = ["sudo", "-n", "--", "env", "SIGMOND_ALLOW_SUDO=1",
                "PYTHONUNBUFFERED=1", *self._build_argv(g, dry_run=False)]

        def _after_run(rc) -> None:
            self._render_verdict(rc, g)

        self.app.push_screen(_BringupModal(argv), _after_run)

    def _render_verdict(self, rc, g: dict) -> None:
        status = self.query_one("#gf-status", Static)
        fixits = self.query_one("#gf-fixits")
        if rc == 0:
            status.update(
                f"[green]✔ {g['profile']} station brought up.[/]  "
                "Open [bold]Validate[/] for the full health report.  "
                "radiod uses antenna defaults — set your real antenna with "
                "[bold]Edit antenna[/].")
        else:
            status.update(
                f"[yellow]⚠ bring-up finished with issues (exit {rc}).[/]  "
                "Review the log above, then try [bold]Open Validate[/] or "
                "[bold]Logs[/] to see what needs attention.")
        fixits.add_class("show")
        # Re-run the readiness gate: the operator needs the AFTER verdict —
        # bring-up exiting 0 is not the same claim as "the station is
        # complete", and the gate auto-flips from capture to site once
        # identity has been seeded.
        self._refresh_equipment()
        self._refresh_readiness(label="after bring-up")
        # Refresh the app's system view so the tree / Overview reflect the
        # now-running station.
        try:
            self.app._load_system_view()
        except Exception:                                    # noqa: BLE001
            pass

    def _edit_antenna(self) -> None:
        # config edit is interactive (radiod's own wizard / $EDITOR), so this
        # one suspends to the real terminal — unlike the streamed bring-up.
        suspend_and_run_sudo(self.app, [_smd_binary(), "config", "edit", "radiod"])
