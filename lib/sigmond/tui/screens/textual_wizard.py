"""In-TUI Textual renderer for a client's `config show/apply` contract.

A second renderer of the same JSON contract the whiptail wizard drives:

  read  <- ``<client> config show --json --defaults``
  write -> ``<client> config apply --json -``   (over stdin)

Operators launching ``smd tui`` get an in-process Textual form for the
selected client's scalar config keys; the whiptail wizard remains the
standalone-CLI renderer (``<client> config init`` outside the TUI).
The client's ``configurator.py`` stays the schema-of-truth: this screen
only walks the JSON tree it gets back from ``config show`` and pipes
the dirty subset to ``config apply``.

Pilot scope (psk-recorder):
  - top-level scalar tables ([station], [paths], [processing], [timing])
    render as editable forms.  Each scalar leaf is a Textual widget
    (Switch for bool, Input for str/int/float).
  - top-level array-of-tables ([[radiod]]) — each block renders as its
    own sub-section labeled by its ``id``, with the block's scalar
    keys (id, radiod_status, ...) editable in place.  Nested sub-tables
    within a block (ft4/ft8 with freqs_hz lists) stay out of scope —
    operators who need them still use the whiptail wizard's $EDITOR
    escape.
  - When the client's repo ships ``config/help.toml`` (the same sidecar
    the whiptail wizard reads), per-key ``title`` strings replace the
    raw key name as the field label.  Missing help.toml degrades to
    bare keys — both renderers share the schema-of-truth.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ModuleNotFoundError:  # py<3.11
    import tomli as tomllib  # type: ignore[no-redef]

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, Switch
from textual.worker import Worker, WorkerState

from ..mutation import run_with_stdin


# ---------------------------------------------------------------------------
# Subprocess helpers (kept module-level so tests can monkeypatch them).
# ---------------------------------------------------------------------------

def load_config_via_show(
    client_bin: str,
    config_path: Optional[str] = None,
) -> tuple[Optional[dict], str]:
    """Run ``<client_bin> config show --json --defaults`` and parse JSON.

    When ``config_path`` is given, ``--config <path>`` is appended so
    the client reads the per-instance file at
    ``/etc/<client>/<reporter-id>.toml`` instead of its legacy shared
    config (MULTI-INSTANCE-ARCHITECTURE.md §4).  When ``config_path`` is
    None the client picks its own default.

    Returns ``(data, error)``.  ``data`` is the parsed dict on success,
    None on failure; ``error`` is a human-readable string for display.
    """
    argv = [client_bin, 'config', 'show', '--json', '--defaults']
    if config_path:
        argv.extend(['--config', config_path])
    try:
        proc = subprocess.run(
            argv, capture_output=True, check=False, text=True,
        )
    except OSError as exc:
        return None, f"failed to exec {client_bin}: {exc}"
    if proc.returncode != 0:
        return None, (
            f"`{client_bin} config show` exited {proc.returncode}: "
            f"{proc.stderr.strip() or '(no stderr)'}"
        )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return None, f"`{client_bin} config show` stdout was not JSON: {exc}"
    if not isinstance(data, dict):
        return None, (
            f"`{client_bin} config show` returned "
            f"{type(data).__name__}, expected object"
        )
    return data, ""


# ---------------------------------------------------------------------------
# help.toml — per-client operator-help sidecar.  Same file the whiptail
# wizard reads; we share the schema-of-truth across both renderers.
# ---------------------------------------------------------------------------

def _help_toml_candidates(client_name: str) -> list[Path]:
    """Where to look for ``<client>/config/help.toml``.

    Sigmond's editable-install path first, then the packaged-install
    fallback.  Both psk-recorder and mag-recorder live at the first
    path on production hosts; the second covers operators who installed
    from a package.
    """
    return [
        Path(f"/opt/git/sigmond/{client_name}/config/help.toml"),
        Path(f"/usr/local/share/{client_name}/help.toml"),
    ]


def load_help_toml(client_name: str) -> dict:
    """Read the client's ``config/help.toml`` if present.

    Returns a dict shaped like::

        {"station": {"callsign": {"title": "Amateur callsign", "help": "...",
                                  "example": "AC0G", "validator_hint": "...",
                                  "required": True},
                     "grid_square": {...}, ...},
         "radiod":  {"id":            {...},
                     "radiod_status": {...}}, ...}

    Returns ``{}`` when the file is absent or unreadable — the renderer
    just falls back to bare key names.  Errors are swallowed because
    operator help is a UX nicety, not a contract dependency.
    """
    for path in _help_toml_candidates(client_name):
        if not path.is_file():
            continue
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def help_label(help_data: dict, section: str, key: str) -> str:
    """Look up the operator-facing label for one (section, key).

    Returns the help.toml ``title`` when present, the bare key name
    otherwise.  Used so the form's left-column label is human-readable
    on clients with a help sidecar and still legible on clients without.
    """
    block = help_data.get(section, {})
    if isinstance(block, dict):
        entry = block.get(key, {})
        if isinstance(entry, dict):
            title = entry.get("title")
            if isinstance(title, str) and title.strip():
                return title
    return key


# ---------------------------------------------------------------------------
# Widget identifiers.  Per-leaf widgets get a deterministic id so we can
# query them back on Save without holding references to every widget.
# ---------------------------------------------------------------------------

def _field_id(section: str, key: str) -> str:
    """Stable widget id for a (section, key) scalar at the top level.
    Safe for Textual selectors (alphanum + dashes / underscores).
    Sections / keys with awkward characters are deferred to follow-up;
    psk-recorder's keys don't trip the constraint."""
    return f"tw-fld-{section}-{key}"


def _array_field_id(section: str, index: int, key: str) -> str:
    """Stable widget id for a scalar inside ``section[index]`` (e.g.
    ``radiod[0].radiod_status``).  Distinct namespace from top-level
    field ids so a top-level ``[radiod]`` table couldn't collide with
    an array's keys (psk-recorder never has both, but the wizard is
    generic)."""
    return f"tw-arr-{section}-{index}-{key}"


# ---------------------------------------------------------------------------
# The screen.
# ---------------------------------------------------------------------------

@dataclass
class _Leaf:
    """Tracked metadata for one editable scalar.

    ``array_index`` is None for top-level scalars (``[section].key``)
    and an int for keys inside a ``[[section]]`` array block
    (``[[section]][index].key``).  The two flavours land at different
    widget ids and unwind differently in :meth:`_collect_payload`.
    """
    section: str
    key: str
    original: Any
    kind: str   # "str" | "int" | "float" | "bool"
    array_index: Optional[int] = None


class TextualConfigWizardScreen(ModalScreen[bool]):
    """In-TUI form bound to ``<client> config show/apply``.

    Dismisses with True when a save succeeded (caller should refresh
    its view), False on cancel or unsaved close.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    TextualConfigWizardScreen {
        align: center middle;
    }
    TextualConfigWizardScreen > Vertical {
        width: 88%;
        height: 88%;
        padding: 1 2;
        background: $panel;
        border: thick $primary;
    }
    TextualConfigWizardScreen #tw-title {
        text-style: bold;
        margin-bottom: 0;
    }
    TextualConfigWizardScreen #tw-subtitle {
        color: $text-muted;
        margin-bottom: 1;
    }
    TextualConfigWizardScreen #tw-scroll {
        height: 1fr;
        border: solid $surface;
        padding: 0 1;
        background: $background;
    }
    TextualConfigWizardScreen .tw-section {
        text-style: bold;
        color: $primary;
        margin-top: 1;
    }
    TextualConfigWizardScreen .tw-readonly {
        color: $text-muted;
        margin-top: 1;
    }
    TextualConfigWizardScreen .tw-row {
        height: 3;
        margin-bottom: 0;
    }
    TextualConfigWizardScreen .tw-label {
        width: 32;
        padding-top: 1;
    }
    TextualConfigWizardScreen .tw-input {
        width: 1fr;
    }
    TextualConfigWizardScreen #tw-status {
        margin-top: 1;
        color: $text-muted;
    }
    TextualConfigWizardScreen #tw-buttons {
        height: 3;
        margin-top: 1;
        align: right middle;
    }
    TextualConfigWizardScreen #tw-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        client_name: str,
        client_bin: str,
        config_path: Optional[str] = None,
        **kwargs,
    ) -> None:
        """``config_path`` overrides the client's default config-file
        path so the wizard can drive a per-instance file
        (``/etc/<client>/<reporter-id>.toml`` per
        MULTI-INSTANCE-ARCHITECTURE.md §4).  When None the client picks
        its own default — useful for clients still on the legacy shared
        config (mag-recorder during the migration window)."""
        super().__init__(**kwargs)
        self._client_name = client_name
        self._client_bin = client_bin
        self._config_path = config_path
        self._leaves: list[_Leaf] = []
        self._loaded: bool = False
        # Per-client help sidecar; degrades gracefully when absent.
        self._help: dict = load_help_toml(client_name)
        # Cached array-of-tables loaded from `config show` so we can
        # rebuild the full list on save (apply uses overlay-wins for
        # arrays, so we MUST send the whole list — partial would drop
        # any block the operator didn't touch).
        self._original_arrays: dict[str, list[dict]] = {}

    # ------------------------------------------------------------------
    # compose / mount
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            title = f"Edit config — {self._client_name}"
            if self._config_path:
                # Per-instance file (MULTI-INSTANCE-ARCHITECTURE §4) —
                # surface the path so the operator can see which file
                # they're editing.  e.g. ``— psk-recorder @ AC0G-B1``.
                instance_tag = self._instance_tag_from_path(self._config_path)
                if instance_tag:
                    title = f"Edit config — {self._client_name} @ {instance_tag}"
            yield Static(title, id="tw-title")
            sub = f"[dim]via `{self._client_bin} config show/apply"
            if self._config_path:
                sub += f" --config {self._config_path}"
            sub += "`[/]"
            yield Static(sub, id="tw-subtitle")
            with ScrollableContainer(id="tw-scroll"):
                yield Static("[dim]loading…[/]", id="tw-placeholder")
            yield Static("", id="tw-status")
            with Horizontal(id="tw-buttons"):
                yield Button("Cancel", id="tw-cancel", variant="default")
                yield Button(
                    "Save", id="tw-save", variant="primary", disabled=True,
                )

    def on_mount(self) -> None:
        self.run_worker(self._load_data, thread=True, name="tw-load")

    def _load_data(self) -> tuple[Optional[dict], str]:
        return load_config_via_show(self._client_bin, self._config_path)

    @staticmethod
    def _instance_tag_from_path(config_path: str) -> str:
        """Best-effort extraction of the reporter-id from a per-instance
        config path.  ``/etc/psk-recorder/AC0G-B1.toml`` → ``AC0G-B1``.
        Returns ``""`` when the path doesn't look reporter-keyed.
        """
        stem = Path(config_path).stem
        # Strip well-known legacy suffixes so e.g. the legacy
        # ``psk-recorder-config`` doesn't get treated as a reporter id.
        if stem in {"config", "psk-recorder-config",
                    "wspr-recorder-config", "hfdl-recorder-config",
                    "codar-sounder-config"} or stem.endswith("-config"):
            return ""
        return stem

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        # Single dispatcher for both the loader and the apply workers.
        if event.worker.name == "tw-load":
            self._handle_load_event(event)
        elif event.worker.name == "tw-apply":
            self._handle_apply_event(event)

    def _handle_load_event(self, event: Worker.StateChanged) -> None:
        if event.state != WorkerState.SUCCESS:
            if event.state == WorkerState.ERROR:
                scroll = self.query_one("#tw-scroll", ScrollableContainer)
                scroll.mount(Static(
                    f"[red]loader errored: {event.worker.error}[/]"
                ))
            return
        data, err = event.worker.result
        scroll = self.query_one("#tw-scroll", ScrollableContainer)
        try:
            placeholder = scroll.query_one("#tw-placeholder", Static)
            placeholder.remove()
        except Exception:
            pass

        if err or data is None:
            scroll.mount(Static(f"[red]{err or 'no data'}[/]"))
            return

        self._render_form(scroll, data)
        self._loaded = True
        self.query_one("#tw-save", Button).disabled = False

    # ------------------------------------------------------------------
    # form rendering — walks the JSON tree
    # ------------------------------------------------------------------

    # Keys that the multi-instance contract pins to the filename or
    # are otherwise structurally locked — rendered read-only so an
    # operator can't break the file's identity from inside the wizard.
    # ``[instance].reporter_id`` MUST match the filename stem per
    # MULTI-INSTANCE-ARCHITECTURE.md §5 (sanity check at load).
    _READ_ONLY_KEYS: set[tuple[str, str]] = {("instance", "reporter_id")}

    def _render_form(self, scroll: ScrollableContainer, data: dict) -> None:
        rendered_anything = False
        for section_name in sorted(data.keys()):
            value = data[section_name]
            if isinstance(value, dict):
                scalars = {k: v for k, v in value.items()
                           if not isinstance(v, (dict, list))}
                if not scalars:
                    # Section exists but holds only sub-tables; skip in pilot.
                    continue
                scroll.mount(Static(f"[{section_name}]", classes="tw-section"))
                for key in sorted(scalars.keys()):
                    if (section_name, key) in self._READ_ONLY_KEYS:
                        scroll.mount(Horizontal(
                            Static(help_label(self._help, section_name, key),
                                   classes="tw-label"),
                            Static(f"[dim]{scalars[key]}[/]  "
                                   f"[dim italic](locked)[/]"),
                            classes="tw-row",
                        ))
                        continue
                    self._mount_leaf(
                        scroll, section_name, key, scalars[key],
                        array_index=None,
                    )
                rendered_anything = True
            elif isinstance(value, list):
                if self._render_array_section(scroll, section_name, value):
                    rendered_anything = True
            else:
                # Bare top-level scalar (rare; psk-recorder has none, but
                # the contract permits it).  Treat as section="" so it
                # round-trips back via config apply at the top level.
                self._mount_leaf(
                    scroll, "", section_name, value, array_index=None,
                )
                rendered_anything = True

        if not rendered_anything:
            scroll.mount(Static(
                "[yellow]no editable scalar fields surfaced.[/]"
            ))

    def _render_array_section(
        self,
        scroll: ScrollableContainer,
        section_name: str,
        blocks: list,
    ) -> bool:
        """Render a TOML array-of-tables (e.g. ``[[radiod]]``).

        Each block becomes its own sub-section; the block's scalar
        leaves are editable, and any nested sub-tables (e.g. a
        ``[[radiod]]`` block with ``ft4 = {...}`` / ``ft8 = {...}``)
        get a read-only "not editable in TUI" tagline.  We cache the
        full original list in ``self._original_arrays`` so save can
        emit a complete list — the contract's overlay-wins merge for
        arrays would otherwise drop blocks we didn't touch.

        Returns True when at least one editable leaf was rendered.
        """
        if not blocks:
            scroll.mount(Static(
                f"[dim][[{section_name}]] — 0 blocks[/]",
                classes="tw-readonly",
            ))
            return False

        self._original_arrays[section_name] = [
            (b.copy() if isinstance(b, dict) else b) for b in blocks
        ]

        rendered = False
        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                # Lists of scalars aren't a TOML idiom we expect here.
                continue
            scalars = {k: v for k, v in block.items()
                       if not isinstance(v, (dict, list))}
            nested = [k for k, v in block.items()
                      if isinstance(v, (dict, list))]

            # Sub-header: try to show the block's ``id`` so multi-block
            # configs disambiguate visually.  Fall back to the bare index.
            block_label = ""
            for key in ("id", "name", "instance"):
                if key in scalars and isinstance(scalars[key], str):
                    block_label = scalars[key]
                    break
            header = f"[[{section_name}]][{index}]"
            if block_label:
                header = f"{header} — {block_label}"
            scroll.mount(Static(header, classes="tw-section"))

            if not scalars:
                scroll.mount(Static(
                    "[dim](no scalar fields in this block)[/]",
                    classes="tw-readonly",
                ))
            else:
                for key in sorted(scalars.keys()):
                    self._mount_leaf(
                        scroll, section_name, key, scalars[key],
                        array_index=index,
                    )
                    rendered = True

            if nested:
                scroll.mount(Static(
                    f"[dim](nested keys not editable in TUI: "
                    f"{', '.join(sorted(nested))})[/]",
                    classes="tw-readonly",
                ))
        return rendered

    def _mount_leaf(
        self,
        scroll: ScrollableContainer,
        section: str,
        key: str,
        value: Any,
        *,
        array_index: Optional[int],
    ) -> None:
        widget_id = (
            _field_id(section, key) if array_index is None
            else _array_field_id(section, array_index, key)
        )
        label_text = help_label(self._help, section, key)

        kind: str
        if isinstance(value, bool):
            kind = "bool"
            widget = Switch(value=value, id=widget_id)
        elif isinstance(value, int):
            kind = "int"
            widget = Input(
                value=str(value), id=widget_id,
                type="integer", classes="tw-input",
            )
        elif isinstance(value, float):
            kind = "float"
            widget = Input(
                value=repr(value), id=widget_id,
                type="number", classes="tw-input",
            )
        elif isinstance(value, str):
            kind = "str"
            widget = Input(
                value=value, id=widget_id,
                classes="tw-input",
            )
        else:
            # None or some other type — render disabled so the operator
            # sees it exists but can't break it.
            scroll.mount(Horizontal(
                Static(f"{label_text}", classes="tw-label"),
                Static(f"[dim]({type(value).__name__})[/]"),
                classes="tw-row",
            ))
            return

        scroll.mount(Horizontal(
            Static(f"{label_text}", classes="tw-label"),
            widget,
            classes="tw-row",
        ))
        self._leaves.append(_Leaf(
            section=section, key=key, original=value, kind=kind,
            array_index=array_index,
        ))

    # ------------------------------------------------------------------
    # buttons
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "tw-cancel":
            self.dismiss(False)
        elif bid == "tw-save":
            self._save()

    def action_cancel(self) -> None:
        self.dismiss(False)

    # ------------------------------------------------------------------
    # save
    # ------------------------------------------------------------------

    def _collect_payload(self) -> tuple[dict, list[str]]:
        """Walk the leaves, collect only fields that changed from
        their original loaded value, and return (payload, errors).

        Top-level scalars ride into the payload as
        ``{"<section>": {"<key>": value}}`` (or a top-level key when
        ``section == ""``) — ``config apply``'s ``_deep_merge`` then
        keeps every untouched key in the section.

        Array-of-tables (e.g. ``[[radiod]]``) are different: the
        contract's overlay-wins replaces the full list because
        ``_deep_merge`` doesn't compose for arrays of dicts.  So we
        rebuild the FULL list from ``self._original_arrays`` and only
        apply the edited keys on top — that way untouched blocks
        survive the round-trip byte-for-byte.
        """
        payload: dict = {}
        errors: list[str] = []
        # Sections that need a full-array rebuild on save.
        dirty_arrays: set[str] = set()
        # Per-block, per-key overrides keyed as (section, index, key).
        array_overrides: dict[tuple[str, int, str], Any] = {}

        for leaf in self._leaves:
            wid = (
                _field_id(leaf.section, leaf.key)
                if leaf.array_index is None
                else _array_field_id(leaf.section, leaf.array_index, leaf.key)
            )
            try:
                widget = self.query_one(f"#{wid}")
            except Exception as exc:
                origin = (
                    f"[{leaf.section}].{leaf.key}"
                    if leaf.array_index is None
                    else f"[[{leaf.section}]][{leaf.array_index}].{leaf.key}"
                )
                errors.append(f"{origin}: widget lookup failed: {exc}")
                continue

            new_value, err = self._coerce_widget_value(widget, leaf)
            if err is not None:
                errors.append(err)
                continue

            if new_value == leaf.original:
                continue

            if leaf.array_index is None:
                if leaf.section == "":
                    payload[leaf.key] = new_value
                else:
                    payload.setdefault(leaf.section, {})[leaf.key] = new_value
            else:
                dirty_arrays.add(leaf.section)
                array_overrides[(leaf.section, leaf.array_index, leaf.key)] = new_value

        # Rebuild dirty arrays in full.
        for section in dirty_arrays:
            original = self._original_arrays.get(section, [])
            rebuilt: list[Any] = []
            for index, block in enumerate(original):
                if not isinstance(block, dict):
                    rebuilt.append(block)
                    continue
                merged = block.copy()
                for (s, i, k), v in array_overrides.items():
                    if s == section and i == index:
                        merged[k] = v
                rebuilt.append(merged)
            payload[section] = rebuilt

        return payload, errors

    def _coerce_widget_value(
        self, widget: Any, leaf: _Leaf,
    ) -> tuple[Any, Optional[str]]:
        """Return (coerced_value, error_message).  error_message is
        None on success.  Centralized so top-level and array leaves
        share the parse logic.
        """
        origin = (
            f"[{leaf.section}].{leaf.key}"
            if leaf.array_index is None
            else f"[[{leaf.section}]][{leaf.array_index}].{leaf.key}"
        )
        if leaf.kind == "bool":
            return bool(widget.value), None
        if leaf.kind == "int":
            raw = widget.value
            if raw == "" or raw is None:
                return None, f"{origin}: integer required (got empty)"
            try:
                return int(raw), None
            except ValueError:
                return None, f"{origin}: not an integer: {raw!r}"
        if leaf.kind == "float":
            raw = widget.value
            if raw == "" or raw is None:
                return None, f"{origin}: number required (got empty)"
            try:
                return float(raw), None
            except ValueError:
                return None, f"{origin}: not a number: {raw!r}"
        # str
        return widget.value, None

    def _save(self) -> None:
        status = self.query_one("#tw-status", Static)
        if not self._loaded:
            status.update("[yellow]not loaded yet[/]")
            return

        payload, errors = self._collect_payload()
        if errors:
            joined = "\n".join(errors)
            status.update(f"[red]{joined}[/]")
            return

        if not payload:
            status.update("[yellow]no changes to save.[/]")
            return

        status.update("[dim]saving…[/]")
        self.query_one("#tw-save", Button).disabled = True
        self.query_one("#tw-cancel", Button).disabled = True

        # Run the apply in a worker so the UI doesn't freeze if sudo
        # falls through to the suspended-password path.
        self._pending_payload = payload
        self.run_worker(self._apply_payload, thread=True, name="tw-apply")

    def _apply_payload(self) -> subprocess.CompletedProcess:
        stdin_bytes = json.dumps(self._pending_payload).encode("utf-8")
        argv = [self._client_bin, 'config', 'apply', '--json', '-']
        if self._config_path:
            # Same precedence the daemon honours (per psk-recorder Phase 3:
            # ``--config`` wins).  We pass the path explicitly so apply
            # writes the per-instance file, not the legacy shared one.
            argv = [
                self._client_bin, 'config', 'apply',
                '--json', '--config', self._config_path, '-',
            ]
        return run_with_stdin(
            self.app, argv, stdin_bytes=stdin_bytes, sudo=True,
        )

    def _handle_apply_event(self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.ERROR:
            status = self.query_one("#tw-status", Static)
            status.update(f"[red]apply worker errored: {event.worker.error}[/]")
            self.query_one("#tw-save", Button).disabled = False
            self.query_one("#tw-cancel", Button).disabled = False
            return
        if event.state != WorkerState.SUCCESS:
            return
        result = event.worker.result
        status = self.query_one("#tw-status", Static)
        if result.returncode == 0:
            self.dismiss(True)
            return
        stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        status.update(
            f"[red]config apply exited {result.returncode}:\n"
            f"{stderr or '(no stderr)'}[/]"
        )
        self.query_one("#tw-save", Button).disabled = False
        self.query_one("#tw-cancel", Button).disabled = False
