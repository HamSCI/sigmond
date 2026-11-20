"""Prune ``/etc/sigmond/catalog.toml`` against the repo's
``etc/catalog.toml``.

The sparse-overlay catalog (see ``sigmond.catalog``) gives the operator
file a job: declare per-host overrides.  Anything in the operator file
that just duplicates the repo file pulls its weight zero — and worse,
silently shadows future upstream updates because operator-layer keys
win regardless of whether they actually differ.

This module removes the dead weight:

* For every ``[client.<name>]`` / ``[deprecated.<name>]`` /
  ``[profile.<name>]`` block in the operator file that has a matching
  block in the repo file, drop any key whose value is byte-equal to the
  repo's value for the same key.
* Every other table or key in the operator file (``[components]
  locally_managed``, say) is carried through verbatim: the prune knows
  nothing of it, so it never drops it.
* If a block ends up empty after that, drop the block entirely.
* Operator-only blocks (those whose name is absent from the repo file)
  are kept verbatim.
* If the operator file ends up with nothing at all, the file is
  removed — sigmond's sparse-overlay handles missing operator files
  fine and an empty file just adds noise to the system.

The emitter is a hand-rolled minimal TOML writer (stdlib has tomllib
for reading but no writer in 3.11).  It only needs to handle string,
int, and list-of-string values — that's the entire catalog schema.

Backup: a ``.bak`` snapshot is written before the operator file is
rewritten or removed, in case the prune is wrong about a value.  Before
either, the rendered file is parsed back and compared with what the prune
meant to keep; on any difference nothing is written (ValueError).

Root runs this (install.sh, ``smd align --apply``), so the operator file
is read, and the file and its ``.bak`` written, through
``sigmond.safe_write``: a symlink at the operator path is refused, and one
planted at either written name is replaced, never written through.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sigmond.safe_write import read_nofollow, write_owned


REPO_CATALOG = (Path(__file__).resolve().parent.parent.parent
                / 'etc' / 'catalog.toml')
OPERATOR_CATALOG = Path('/etc/sigmond/catalog.toml')


@dataclass
class PruneReport:
    operator_path: Path
    backup_path: Optional[Path] = None
    dropped_blocks: list[tuple[str, str]] = field(default_factory=list)  # (section, name)
    pruned_keys: list[tuple[str, str, str]] = field(default_factory=list)  # (section, name, key)
    kept_blocks: list[tuple[str, str]] = field(default_factory=list)
    removed_file: bool = False
    no_op: bool = False     # nothing to do


SECTIONS = ('client', 'deprecated', 'profile')
OTHER = 'other'     # the minimal mapping's key for everything else, verbatim


def _empty() -> dict:
    out: dict = {section: {} for section in SECTIONS}
    out[OTHER] = {}
    return out


def _load_sections(path: Path, *, nofollow: bool = False
                   ) -> dict[str, dict]:
    """Return ``{section: {name: raw_block_dict}}`` for the catalog
    sections the prune compares (``SECTIONS``), plus ``OTHER``: every
    other top-level key and table of the file, verbatim.  Missing
    file → empty mapping (treat as "no operator overrides at all").
    ``nofollow`` (the operator file) reads through
    ``safe_write.read_nofollow``, which refuses a symlink."""
    if nofollow:
        text = read_nofollow(path)
        if text is None:
            return _empty()
        data = tomllib.loads(text)
    else:
        if not path.exists():
            return _empty()
        with open(path, 'rb') as f:
            data = tomllib.load(f)
    out = _empty()
    for key, value in data.items():
        if key in SECTIONS and isinstance(value, dict):
            out[key] = dict(value.items())
        else:
            out[OTHER][key] = value
    return out


def _prune_block(op_block: dict, repo_block: dict) -> dict:
    """Return a new dict containing only the keys whose values differ
    from the repo block.  Keys absent in the repo block are kept (the
    operator is declaring something the repo file doesn't)."""
    out: dict = {}
    for key, value in op_block.items():
        if key in repo_block and repo_block[key] == value:
            continue
        out[key] = value
    return out


def compute_minimal_operator(operator_path: Path = OPERATOR_CATALOG,
                              repo_path: Path = REPO_CATALOG,
                              ) -> tuple[dict[str, dict[str, dict]],
                                         PruneReport]:
    """Compute the minimal operator-catalog content (blocks → keys)
    after pruning duplicates of ``repo_path``.  Pure: no writes.
    Returns ``(minimal_sections, report)`` where ``minimal_sections``
    has the same ``{section: {name: block}}`` shape as
    ``_load_sections``."""
    report = PruneReport(operator_path=operator_path)
    op = _load_sections(operator_path, nofollow=True)
    repo = _load_sections(repo_path)

    minimal = _empty()
    any_op_content = any(op[section] for section in op)
    if not any_op_content:
        report.no_op = True
        return minimal, report
    minimal[OTHER] = dict(op[OTHER])

    for section in SECTIONS:
        for name, op_block in op[section].items():
            repo_block = repo[section].get(name)
            if repo_block is None:
                # Operator-only entry — keep verbatim.
                minimal[section][name] = op_block
                report.kept_blocks.append((section, name))
                continue
            pruned = _prune_block(op_block, repo_block)
            for key in op_block:
                if key not in pruned:
                    report.pruned_keys.append((section, name, key))
            if pruned:
                minimal[section][name] = pruned
                report.kept_blocks.append((section, name))
            else:
                report.dropped_blocks.append((section, name))
    return minimal, report


_ESCAPES = {'\\': '\\\\', '"': '\\"', '\n': '\\n', '\t': '\\t',
            '\r': '\\r', '\b': '\\b', '\f': '\\f'}


def _format_toml_value(value) -> str:
    """Emit a TOML scalar/list-of-scalars.  Only handles the value
    shapes the catalog schema uses (str, int, list[str])."""
    if isinstance(value, bool):  # bool is an int subclass — check first
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        # A TOML basic string: backslash, quote and every control
        # character escaped, so a newline in a value cannot end the line
        # (final review I5).
        return '"' + ''.join(_ESCAPES.get(c) or (
            f'\\u{ord(c):04x}' if ord(c) < 0x20 or ord(c) == 0x7f else c)
            for c in value) + '"'
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(_format_toml_value(v) for v in value) + ']'
    raise TypeError(f"unsupported TOML value type: {type(value).__name__}")


def _format_block(section: str, name: str, block: dict) -> str:
    """Emit ``[section.name]`` plus key=value lines.  Keys are written
    in insertion order; tomllib preserves the source order so the
    result mirrors what the operator wrote."""
    out = [f'[{section}.{_bare_key(name)}]']
    for key, value in block.items():
        out.append(f'{_bare_key(key)} = {_format_toml_value(value)}')
    return '\n'.join(out) + '\n'


def _bare_key(key: str) -> str:
    import re
    return key if re.fullmatch(r'[A-Za-z0-9_-]+', key) else \
        _format_toml_value(key)


def _is_aot(value) -> bool:
    """An array of tables (``[[name]]``)."""
    return (isinstance(value, list) and bool(value)
            and all(isinstance(x, dict) for x in value))


def _format_aot(path: list, items: list) -> list:
    out = []
    head = '.'.join(_bare_key(p) for p in path)
    for item in items:
        out.append(f'[[{head}]]')
        for k, v in item.items():
            out.append(f'{_bare_key(k)} = {_format_toml_value(v)}')
        out.append('')
    return out


def _format_table(path: list, table: dict) -> list:
    """``[a.b]`` with its scalar keys, then its sub-tables and arrays of
    tables, recursively: any table read by tomllib, written back."""
    head = '.'.join(_bare_key(p) for p in path)
    scalars = [(k, v) for k, v in table.items()
               if not isinstance(v, dict) and not _is_aot(v)]
    out = []
    if scalars or not table:
        out.append(f'[{head}]')
        for k, v in scalars:
            out.append(f'{_bare_key(k)} = {_format_toml_value(v)}')
        out.append('')
    for k, v in table.items():
        if isinstance(v, dict):
            out += _format_table(path + [k], v)
        elif _is_aot(v):
            out += _format_aot(path + [k], v)
    return out


def _render_minimal(sections: dict[str, dict[str, dict]]) -> str:
    """Render the minimal-content sections back to a TOML string.
    Includes a generated-by header so an operator opening the file
    knows what created it."""
    parts = [
        '# /etc/sigmond/catalog.toml — per-host operator overrides.\n',
        '# Pruned by `smd config catalog-prune` (or sigmond/install.sh).\n',
        '# Only entries that diverge from the repo catalog remain here.\n',
        '# Edit by hand to add/adjust host-specific overrides; the next\n',
        '# prune will leave your edits alone unless they re-duplicate\n',
        '# the repo value.\n',
        '\n',
    ]
    other = sections.get(OTHER) or {}
    # Top-level keys first: TOML puts every key after a header into it.
    bare = [(k, v) for k, v in other.items()
            if not isinstance(v, dict) and not _is_aot(v)]
    for key, value in bare:
        parts.append(f'{_bare_key(key)} = {_format_toml_value(value)}\n')
    if bare:
        parts.append('\n')
    for section in SECTIONS:
        for name, block in (sections.get(section) or {}).items():
            parts.append(_format_block(section, name, block))
            parts.append('\n')
    for key, value in other.items():
        if isinstance(value, dict):
            parts.append('\n'.join(_format_table([key], value)) + '\n')
        elif _is_aot(value):
            parts.append('\n'.join(_format_aot([key], value)) + '\n')
    return ''.join(parts).rstrip() + '\n'


def _intended(sections: dict) -> dict:
    """What tomllib should read back from ``_render_minimal(sections)``."""
    want = dict(sections.get(OTHER) or {})
    for section in SECTIONS:
        if sections.get(section):
            want[section] = {n: dict(b) for n, b in sections[section].items()}
    return want


def _check_reads_back(rendered: str, sections: dict) -> None:
    """Raise ValueError unless ``rendered`` parses to exactly ``sections``."""
    try:
        got = tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f'the pruned catalog would not parse ({exc}); '
                         f'left as it is') from None
    if got != _intended(sections):
        raise ValueError('the pruned catalog would not read back as the '
                         'entries it keeps; left as it is')


def _free_name(path: Path) -> Path:
    """``path``, or ``path.1``, ``path.2`` ... : the first name nothing
    stands at (a dangling link counts as standing there)."""
    cand, n = path, 0
    while os.path.lexists(cand):
        n += 1
        cand = path.with_name(f'{path.name}.{n}')
    return cand


def prune_operator_catalog(operator_path: Path = OPERATOR_CATALOG,
                            repo_path: Path = REPO_CATALOG,
                            *, dry_run: bool = False,
                            backup_suffix: str = '.bak',
                            keep_backups: bool = False) -> PruneReport:
    """Prune the operator catalog in place.  Returns a report describing
    what changed.  Safe to call when ``operator_path`` doesn't exist
    (returns ``no_op=True``).

    Side effects when ``dry_run=False`` and there's work to do:
      * Writes ``operator_path`` + ``backup_suffix`` (default ``.bak``),
        overwriting any previous backup unless ``keep_backups``, which
        takes the next free ``.1``, ``.2`` ... name instead (``smd align
        --apply`` passes a dated suffix and keeps them).
      * If the pruned result is empty: removes ``operator_path``.
      * Otherwise: rewrites ``operator_path`` with the minimal content.
    """
    minimal, report = compute_minimal_operator(operator_path, repo_path)
    if report.no_op:
        return report
    has_content = any(minimal[section] for section in minimal)
    nothing_to_do = (not report.pruned_keys
                     and not report.dropped_blocks)
    if nothing_to_do:
        return report
    if dry_run:
        return report

    # Final review I5: what is written must read back as what the prune
    # meant to keep.  Checked before the backup, so a refusal leaves no
    # trace and the file as it was.
    rendered = None
    if has_content:
        rendered = _render_minimal(minimal)
        _check_reads_back(rendered, minimal)

    # Backup before any destructive write.  Both reads and writes refuse
    # to follow a link (module docstring).
    original = read_nofollow(operator_path)
    if original is None:
        return report
    report.backup_path = operator_path.with_name(
        operator_path.name + backup_suffix)
    if keep_backups:
        report.backup_path = _free_name(report.backup_path)
    write_owned(report.backup_path, original, mode=0o644,
                owner_from_dir=False)

    if not has_content:
        operator_path.unlink()          # unlink never follows a link
        report.removed_file = True
        return report

    write_owned(operator_path, rendered, mode=0o644, owner_from_dir=False)
    return report


def format_report(report: PruneReport) -> str:
    """Human-readable one-paragraph summary of a prune run."""
    if report.no_op:
        return f"{report.operator_path}: no operator overrides — nothing to prune"
    if not report.pruned_keys and not report.dropped_blocks:
        return f"{report.operator_path}: already minimal — no changes"
    lines = [str(report.operator_path) + ':']
    if report.removed_file:
        lines.append(
            f"  removed (every entry duplicated the repo catalog)")
    if report.backup_path:
        lines.append(f"  backup: {report.backup_path}")
    for section, name in report.dropped_blocks:
        lines.append(f"  dropped block [{section}.{name}] "
                     f"(every key matched the repo)")
    for section, name, key in report.pruned_keys:
        lines.append(f"  pruned key   [{section}.{name}].{key}")
    if report.kept_blocks and not report.removed_file:
        kept = ', '.join(f'{s}.{n}' for s, n in report.kept_blocks)
        lines.append(f"  kept: {kept}")
    return '\n'.join(lines)
