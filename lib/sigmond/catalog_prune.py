"""Prune ``/etc/sigmond/catalog.toml`` against the repo's
``etc/catalog.toml``.

The sparse-overlay catalog (see ``sigmond.catalog``) gives the operator
file a job: declare per-host overrides.  Anything in the operator file
that just duplicates the repo file pulls its weight zero — and worse,
silently shadows future upstream updates because operator-layer keys
win regardless of whether they actually differ.

This module removes the dead weight:

* For every ``[client.<name>]`` / ``[deprecated.<name>]`` block in the
  operator file that has a matching block in the repo file, drop any
  key whose value is byte-equal to the repo's value for the same key.
* If a block ends up empty after that, drop the block entirely.
* Operator-only blocks (those whose name is absent from the repo file)
  are kept verbatim.
* If the operator file ends up with no blocks at all, the file is
  removed — sigmond's sparse-overlay handles missing operator files
  fine and an empty file just adds noise to the system.

The emitter is a hand-rolled minimal TOML writer (stdlib has tomllib
for reading but no writer in 3.11).  It only needs to handle string,
int, and list-of-string values — that's the entire catalog schema.

Backup: a ``.bak`` snapshot is written before the operator file is
rewritten or removed, in case the prune is wrong about a value.
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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


def _load_sections(path: Path) -> dict[str, dict[str, dict]]:
    """Return ``{section: {name: raw_block_dict}}`` for the catalog
    sections we care about (``client``, ``deprecated``).  Missing
    file → empty mapping (treat as "no operator overrides at all")."""
    if not path.exists():
        return {'client': {}, 'deprecated': {}}
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    return {
        'client': dict((data.get('client') or {}).items()),
        'deprecated': dict((data.get('deprecated') or {}).items()),
    }


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
    op = _load_sections(operator_path)
    repo = _load_sections(repo_path)

    minimal: dict[str, dict[str, dict]] = {'client': {}, 'deprecated': {}}
    any_op_content = any(op[section] for section in op)
    if not any_op_content:
        report.no_op = True
        return minimal, report

    for section in ('client', 'deprecated'):
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


def _format_toml_value(value) -> str:
    """Emit a TOML scalar/list-of-scalars.  Only handles the value
    shapes the catalog schema uses (str, int, list[str])."""
    if isinstance(value, bool):  # bool is an int subclass — check first
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        # Backslash-escape, then wrap in double quotes — sufficient for
        # repo URLs, descriptions, and the rest of the schema.
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(_format_toml_value(v) for v in value) + ']'
    raise TypeError(f"unsupported TOML value type: {type(value).__name__}")


def _format_block(section: str, name: str, block: dict) -> str:
    """Emit ``[section.name]`` plus key=value lines.  Keys are written
    in insertion order; tomllib preserves the source order so the
    result mirrors what the operator wrote."""
    out = [f'[{section}.{name}]']
    for key, value in block.items():
        out.append(f'{key} = {_format_toml_value(value)}')
    return '\n'.join(out) + '\n'


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
    for section in ('client', 'deprecated'):
        for name, block in sections[section].items():
            parts.append(_format_block(section, name, block))
            parts.append('\n')
    return ''.join(parts).rstrip() + '\n'


def prune_operator_catalog(operator_path: Path = OPERATOR_CATALOG,
                            repo_path: Path = REPO_CATALOG,
                            *, dry_run: bool = False) -> PruneReport:
    """Prune the operator catalog in place.  Returns a report describing
    what changed.  Safe to call when ``operator_path`` doesn't exist
    (returns ``no_op=True``).

    Side effects when ``dry_run=False`` and there's work to do:
      * Writes ``operator_path.bak`` (overwriting any previous backup).
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

    # Backup before any destructive write.
    report.backup_path = operator_path.with_suffix(
        operator_path.suffix + '.bak')
    shutil.copy2(operator_path, report.backup_path)

    if not has_content:
        operator_path.unlink()
        report.removed_file = True
        return report

    operator_path.write_text(_render_minimal(minimal))
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
