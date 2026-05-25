"""Per-reporter client instance model.

Implements MULTI-INSTANCE-ARCHITECTURE.md §3 (reporter ID format),
§4 (canonical file layout), and the file-side actions for §6
(`smd instance add` / `smd instance remove`).

An *instance* is one deployment context of a recorder client, keyed by
operator-meaningful reporter ID (e.g. `AC0G-B1`).  Each instance owns:

  /etc/<client>/<reporter_id>.toml                    (per-instance config)
  /etc/<client>/env/<reporter_id>.env                 (per-instance env)
  /etc/sigmond/clients/<client>@<reporter_id>.sources.toml
                                                      (per-instance sources)
  /var/lib/<client>/<reporter_id>/                    (state — systemd-managed)
  /var/log/<client>/<reporter_id>/                    (logs — systemd-managed)
  /run/<client>/<reporter_id>/                        (runtime — systemd-managed)

Sigmond writes config / env / sources stubs on `add`; the state/log/run
dirs are created automatically by systemd via StateDirectory= /
LogsDirectory= / RuntimeDirectory= when the unit first starts.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .paths import SIGMOND_CONF


# ---------------------------------------------------------------------------
# Reporter ID — path-safe by construction (MULTI-INSTANCE-ARCHITECTURE.md §3)
# ---------------------------------------------------------------------------

REPORTER_ID_REGEX = re.compile(r"^[A-Z0-9][A-Z0-9-]*[A-Z0-9]$")


class InvalidReporterId(ValueError):
    """Raised when a reporter ID doesn't satisfy REPORTER_ID_REGEX."""


def validate_reporter_id(reporter_id: str) -> None:
    """Raise InvalidReporterId if `reporter_id` is not path-safe.

    See MULTI-INSTANCE-ARCHITECTURE.md §3 for the format rule:
    uppercase alphanumerics + ASCII hyphens; no leading/trailing
    hyphen.  Min length 2 (the regex's `[A-Z0-9][A-Z0-9-]*[A-Z0-9]`
    forces start and end chars to be non-hyphen alphanumerics).
    """
    if not isinstance(reporter_id, str) or not reporter_id:
        raise InvalidReporterId(
            "reporter ID must be a non-empty string"
        )
    if not REPORTER_ID_REGEX.match(reporter_id):
        raise InvalidReporterId(
            f"reporter ID {reporter_id!r} is not path-safe; "
            f"must match {REPORTER_ID_REGEX.pattern} "
            f"(uppercase alphanumerics + hyphens; no leading/trailing "
            f"hyphen; min length 2).  WSPRnet's slash form gets "
            f"rendered only at upload time."
        )


def to_wsprnet_form(reporter_id: str) -> str:
    """Render a sigmond reporter ID into WSPRnet's slash form.

    Mechanical: first hyphen becomes the slash; remaining hyphens
    stay part of the suffix.  E.g.  `AC0G-B1` → `AC0G/B1`,
    `KP4MD-RPI-4` → `KP4MD/RPI-4`.

    Only used at the WSPRnet upload boundary; sigmond-internal
    surfaces never see the slash form.
    """
    return reporter_id.replace("-", "/", 1)


# ---------------------------------------------------------------------------
# Canonical file layout (§4)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InstancePaths:
    """Resolved canonical paths for one instance."""
    client: str
    reporter_id: str

    config: Path        # /etc/<client>/<reporter_id>.toml
    env: Path           # /etc/<client>/env/<reporter_id>.env
    sources: Path       # /etc/sigmond/clients/<client>@<reporter_id>.sources.toml
    state_dir: Path     # /var/lib/<client>/<reporter_id>/  (systemd-managed)
    log_dir: Path       # /var/log/<client>/<reporter_id>/  (systemd-managed)
    run_dir: Path       # /run/<client>/<reporter_id>/      (systemd-managed)
    unit_name: str      # <client>@<reporter_id>.service
    unit_template: str  # <client>@.service (in client repo's systemd/ dir)


def instance_paths(client: str, reporter_id: str) -> InstancePaths:
    """Resolve all canonical paths for (client, reporter_id).

    Does NOT touch the filesystem; just returns the path objects.
    Reporter ID is validated; raises InvalidReporterId on a bad name.
    """
    validate_reporter_id(reporter_id)
    if not client or "/" in client or client.startswith("."):
        raise ValueError(f"bad client name: {client!r}")
    etc_client = Path("/etc") / client
    return InstancePaths(
        client=client,
        reporter_id=reporter_id,
        config=etc_client / f"{reporter_id}.toml",
        env=etc_client / "env" / f"{reporter_id}.env",
        sources=SIGMOND_CONF / "clients" / f"{client}@{reporter_id}.sources.toml",
        state_dir=Path("/var/lib") / client / reporter_id,
        log_dir=Path("/var/log") / client / reporter_id,
        run_dir=Path("/run") / client / reporter_id,
        unit_name=f"{client}@{reporter_id}.service",
        unit_template=f"{client}@.service",
    )


# ---------------------------------------------------------------------------
# Instance enumeration (`smd instance list`)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Instance:
    """A discovered instance: paths plus an existence summary."""
    paths: InstancePaths
    has_config: bool
    has_env: bool
    has_sources: bool

    @property
    def client(self) -> str:
        return self.paths.client

    @property
    def reporter_id(self) -> str:
        return self.paths.reporter_id


def list_instances(catalog_clients: Optional[list[str]] = None) -> list[Instance]:
    """Walk /etc/<client>/<reporter_id>.toml across known clients.

    `catalog_clients`: list of client names to consider; if None,
    walks every /etc/<X>/ directory and reports each *.toml file
    whose stem is a valid reporter ID.  Defaults to None.

    Returns instances sorted by (client, reporter_id).  Files whose
    stems aren't valid reporter IDs (e.g. the legacy
    `wspr-recorder-config.toml` shape) are silently skipped — those
    are pre-multi-instance deployments that haven't been migrated
    yet, handled by `smd instance migrate`.
    """
    results: list[Instance] = []
    etc = Path("/etc")

    if catalog_clients is None:
        client_dirs = [
            p for p in etc.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        ] if etc.exists() else []
    else:
        client_dirs = [etc / c for c in catalog_clients]

    for client_dir in client_dirs:
        if not client_dir.is_dir():
            continue
        client = client_dir.name
        for cfg in sorted(client_dir.glob("*.toml")):
            stem = cfg.stem
            try:
                validate_reporter_id(stem)
            except InvalidReporterId:
                continue
            paths = instance_paths(client, stem)
            results.append(Instance(
                paths=paths,
                has_config=paths.config.exists(),
                has_env=paths.env.exists(),
                has_sources=paths.sources.exists(),
            ))

    results.sort(key=lambda i: (i.client, i.reporter_id))
    return results


def get_instance(client: str, reporter_id: str) -> Optional[Instance]:
    """Return Instance for (client, reporter_id) if any file exists.

    Returns None if no per-instance file (config / env / sources)
    is present — i.e., the instance has not been created.
    """
    paths = instance_paths(client, reporter_id)
    has_config = paths.config.exists()
    has_env = paths.env.exists()
    has_sources = paths.sources.exists()
    if not (has_config or has_env or has_sources):
        return None
    return Instance(
        paths=paths,
        has_config=has_config,
        has_env=has_env,
        has_sources=has_sources,
    )


# ---------------------------------------------------------------------------
# File scaffolding (`smd instance add` / `remove`)
# ---------------------------------------------------------------------------

# Header lines written into each stub file so an operator opening one
# in an editor knows what created it and what it's for.
def _stub_header(client: str, reporter_id: str, kind: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"# {kind} for {client}@{reporter_id}\n"
        f"# Created by `smd instance add` on {ts}.\n"
        f"# See docs/MULTI-INSTANCE-ARCHITECTURE.md for the canonical "
        f"layout.\n"
    )


def _config_stub(client: str, reporter_id: str) -> str:
    return (
        _stub_header(client, reporter_id, "Per-instance config")
        + "\n"
        f"[instance]\n"
        f'reporter_id = "{reporter_id}"\n'
        "\n"
        "# Source-keys this instance consumes from.  Use\n"
        '#   smd sources add ' + client + '@' + reporter_id + ' <kind>:<id>\n'
        "# to populate.  See `smd sources list` for what's discoverable.\n"
        "sources = []\n"
        "\n"
        "[instance.metadata]\n"
        '# antenna  = "loop"            # operator description\n'
        '# sdr      = "rx888-mk2"       # SDR model / serial / friendly name\n'
        "\n"
        "# Client-specific sections follow.  Run `smd instance edit\n"
        f"# {client} {reporter_id}` to invoke the client's config flow.\n"
    )


def _env_stub(client: str, reporter_id: str) -> str:
    return (
        _stub_header(client, reporter_id, "Per-instance env")
        + "\n"
        f"# Loaded by {client}@{reporter_id}.service via\n"
        f"#   EnvironmentFile=-/etc/{client}/env/{reporter_id}.env\n"
        "# Empty by default; add KEY=VALUE lines as the client requires.\n"
    )


def _sources_stub(client: str, reporter_id: str) -> str:
    return (
        _stub_header(client, reporter_id, "Per-instance sources selection")
        + "\n"
        "# Rendered by `smd sources apply` from the instance config's\n"
        "# `sources = [...]` list.  Don't hand-edit; use\n"
        f"#   smd sources add {client}@{reporter_id} <kind>:<id>\n"
        f"#   smd sources remove {client}@{reporter_id} <kind>:<id>\n"
        "selections = []\n"
    )


class InstanceExists(RuntimeError):
    """Raised by create_instance when any per-instance file already exists."""


def create_instance(
    client: str,
    reporter_id: str,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> InstancePaths:
    """Initialize the per-instance config/env/sources files.

    Does NOT enable or start the systemd unit (per spec §6).  Does
    NOT create state/log/run dirs — systemd handles those via
    StateDirectory= / LogsDirectory= / RuntimeDirectory= when the
    unit first starts.

    Raises InstanceExists if any of the three files exist and
    `force=False`.  With `force=True`, existing files are left in
    place; only missing files are created.

    With `dry_run=True`, returns the paths that WOULD be created
    without touching the filesystem.
    """
    paths = instance_paths(client, reporter_id)

    existing = [
        p for p in (paths.config, paths.env, paths.sources)
        if p.exists()
    ]
    if existing and not force:
        existing_list = ", ".join(str(p) for p in existing)
        raise InstanceExists(
            f"instance {client}@{reporter_id} already has files: "
            f"{existing_list}.  Use --force to keep them and create "
            f"only missing files, or `smd instance remove` first."
        )

    if dry_run:
        return paths

    # Per-client config dir must exist (created by `smd install`)
    paths.config.parent.mkdir(parents=True, exist_ok=True)
    # env subdir
    paths.env.parent.mkdir(parents=True, exist_ok=True)
    # sigmond's clients dir
    paths.sources.parent.mkdir(parents=True, exist_ok=True)

    if not paths.config.exists():
        paths.config.write_text(_config_stub(client, reporter_id))
    if not paths.env.exists():
        paths.env.write_text(_env_stub(client, reporter_id))
    if not paths.sources.exists():
        paths.sources.write_text(_sources_stub(client, reporter_id))

    return paths


def remove_instance(
    client: str,
    reporter_id: str,
    *,
    purge: bool = False,
    dry_run: bool = False,
) -> list[Path]:
    """Remove per-instance files (config/env/sources) and optionally
    the state/log/run dirs (`--purge`).

    Does NOT stop or disable the systemd unit; the caller is
    responsible for that ordering.

    Returns the list of paths that WERE removed (or that WOULD be
    removed with `dry_run=True`).  Best-effort: missing files are
    silently skipped.
    """
    paths = instance_paths(client, reporter_id)

    file_targets = [paths.config, paths.env, paths.sources]
    dir_targets: list[Path] = []
    if purge:
        dir_targets = [paths.state_dir, paths.log_dir, paths.run_dir]

    removed: list[Path] = []
    for f in file_targets:
        if f.exists() and not f.is_dir():
            removed.append(f)
            if not dry_run:
                try:
                    f.unlink()
                except OSError:
                    pass
    for d in dir_targets:
        if d.exists() and d.is_dir():
            removed.append(d)
            if not dry_run:
                try:
                    shutil.rmtree(d)
                except OSError:
                    pass

    return removed
