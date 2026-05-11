"""Storage-backend migration: from local ClickHouse to local SQLite.

Why this exists:
    SQLite is now the default sigmond sink (see hamsci_ch.Writer.from_env).
    On hosts that were installed back when ClickHouse was the only choice,
    `clickhouse-server` plus its data dir continue to consume 1-2 GB of
    RAM and several merge-CPU cores even when no producer writes to it.
    This module enumerates and removes those leftover artifacts so the
    host's resources go back to the SDR pipeline.

Surface:
    `plan_clickhouse_removal(probe=...)` → a `RemovalPlan` describing
    every artifact that exists on the host (service units, packages,
    data dirs, sigmond-clickhouse venv) PLUS any `SIGMOND_CLICKHOUSE_*`
    lines in `/etc/sigmond/coordination.env` and the producer services
    that consume that file (so they can be restarted onto SQLite).
    Inspection only — no side effects.

    `execute_removal(plan, runner=...)` → actually rewrites the env
    file, restarts consumers (so they pick up SQLite before CH dies),
    stops services, purges packages, deletes dirs.  Refuses to run
    unless the caller sets `confirmed=True` on the plan.

Caller pattern (`smd storage migrate-to-sqlite`):
    1. Build plan.
    2. Print artifacts that would be removed.
    3. If operator passed `--yes`, mark plan confirmed and execute.
    4. Otherwise exit 0 with a dry-run summary.

The runner / probe interfaces are injectable so the migration is unit-
testable without a running ClickHouse on the test host.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger("sigmond.storage_migrate")


# Artifacts the sigmond-clickhouse install leaves on a host.  Listed
# centrally so tests, dry-run output, and the executor all agree.
CH_SERVICE_UNITS = ("sigmond-clickhouse.service", "clickhouse-server.service")
CH_PACKAGES = ("clickhouse-server", "clickhouse-client", "clickhouse-common-static")
CH_DATA_DIRS = ("/var/lib/clickhouse",)
CH_CONFIG_DIRS = ("/etc/clickhouse-server",)
CH_LOG_DIRS = ("/var/log/clickhouse-server",)
CH_SIGMOND_VENV = "/opt/sigmond-clickhouse"
CH_SIGMOND_UNIT_FILE = "/etc/systemd/system/sigmond-clickhouse.service"
CH_SIGMOND_SYMLINK = "/usr/local/sbin/sigmond-clickhouse"

# Where sigmond writes its shared producer-side env vars.  Producers
# (psk-recorder, hf-timestd, wsprdaemon-client, etc.) pull this via
# systemd EnvironmentFile=, so flipping SIGMOND_CLICKHOUSE_URL off here
# is what makes them fall through to the default-SQLite dispatch on
# next restart.
DEFAULT_COORD_ENV = "/etc/sigmond/coordination.env"

# Pattern for env-var assignments we want to neutralize.  Matches the
# whole `SIGMOND_CLICKHOUSE_*=...` family; already-commented lines
# (starting with `#`) won't match, so re-running the verb is a no-op.
_CH_ENV_RE = re.compile(r"^\s*SIGMOND_CLICKHOUSE_[A-Z_]*\s*=")

# Prefix we prepend when commenting out a live env-var.  Distinctive
# enough to spot in a diff, and preserves the audit trail of what was
# set pre-migration.
NEUTRALIZED_PREFIX = "# pre-sqlite-migration: "

# Backup suffix for any file we rewrite.  Operator can `mv` the .bak
# back if the migration was a mistake.
BACKUP_SUFFIX = ".bak-pre-sqlite"


@dataclass
class RemovalPlan:
    """Concrete list of side effects `execute_removal` would perform."""

    services_to_stop: List[str] = field(default_factory=list)
    services_to_disable: List[str] = field(default_factory=list)
    packages_to_purge: List[str] = field(default_factory=list)
    paths_to_remove: List[str] = field(default_factory=list)
    files_to_remove: List[str] = field(default_factory=list)
    # (env_file_path, the_live_line_text) — recorded so the dry-run can
    # show operators exactly which lines will be commented out.
    env_lines_to_neutralize: List[Tuple[str, str]] = field(default_factory=list)
    # Producer units to restart AFTER the env rewrite, so they reconnect
    # to the default SQLite sink before clickhouse-server is torn down.
    consumers_to_restart: List[str] = field(default_factory=list)
    confirmed: bool = False

    @property
    def env_files_to_rewrite(self) -> List[str]:
        """Distinct env-file paths touched by env_lines_to_neutralize."""
        seen: List[str] = []
        for path, _line in self.env_lines_to_neutralize:
            if path not in seen:
                seen.append(path)
        return seen

    @property
    def is_empty(self) -> bool:
        return not (
            self.services_to_stop
            or self.services_to_disable
            or self.packages_to_purge
            or self.paths_to_remove
            or self.files_to_remove
            or self.env_lines_to_neutralize
            or self.consumers_to_restart
        )


class HostProbe:
    """Pluggable host inspection.  Tests substitute a fake."""

    def service_exists(self, unit: str) -> bool:
        # `list-unit-files` returns nonzero only when systemd isn't
        # there at all; for a missing unit it prints `0 unit files`.
        # We parse stdout to tell those apart.
        try:
            r = subprocess.run(
                ["systemctl", "list-unit-files", unit, "--no-legend"],
                capture_output=True, text=True, check=False,
            )
        except FileNotFoundError:
            return False
        return r.returncode == 0 and unit in r.stdout

    def service_active(self, unit: str) -> bool:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "--quiet", unit],
                check=False,
            )
        except FileNotFoundError:
            return False
        return r.returncode == 0

    def package_installed(self, pkg: str) -> bool:
        try:
            r = subprocess.run(
                ["dpkg-query", "-W", "-f=${Status}", pkg],
                capture_output=True, text=True, check=False,
            )
        except FileNotFoundError:
            return False
        return r.returncode == 0 and "install ok installed" in r.stdout

    def path_exists(self, path: str) -> bool:
        return Path(path).exists()

    def read_text(self, path: str) -> Optional[str]:
        try:
            return Path(path).read_text()
        except (FileNotFoundError, PermissionError, IsADirectoryError):
            return None

    def find_units_using_env_file(self, env_path: str) -> List[str]:
        """Active service units whose EnvironmentFiles include env_path.

        Two systemctl calls per unit, so this is O(units) — fine for a
        sigmond host that has a handful of producer services, not for
        a general-purpose audit tool.
        """
        try:
            r = subprocess.run(
                ["systemctl", "list-units", "--no-pager", "--no-legend",
                 "--type=service", "--state=active"],
                capture_output=True, text=True, check=False,
            )
        except FileNotFoundError:
            return []
        consumers: List[str] = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if not parts:
                continue
            unit = parts[0]
            if not unit.endswith(".service"):
                continue
            try:
                r2 = subprocess.run(
                    ["systemctl", "show", "-p", "EnvironmentFiles", unit],
                    capture_output=True, text=True, check=False,
                )
            except FileNotFoundError:
                continue
            if env_path in r2.stdout:
                consumers.append(unit)
        return consumers


def plan_clickhouse_removal(probe: Optional[HostProbe] = None) -> RemovalPlan:
    """Enumerate ClickHouse artifacts (and producer-side config) on this host."""
    p = probe or HostProbe()
    plan = RemovalPlan()

    for unit in CH_SERVICE_UNITS:
        if p.service_exists(unit):
            plan.services_to_disable.append(unit)
            if p.service_active(unit):
                plan.services_to_stop.append(unit)

    for pkg in CH_PACKAGES:
        if p.package_installed(pkg):
            plan.packages_to_purge.append(pkg)

    for d in CH_DATA_DIRS + CH_CONFIG_DIRS + CH_LOG_DIRS:
        if p.path_exists(d):
            plan.paths_to_remove.append(d)

    if p.path_exists(CH_SIGMOND_VENV):
        plan.paths_to_remove.append(CH_SIGMOND_VENV)

    for f in (CH_SIGMOND_UNIT_FILE, CH_SIGMOND_SYMLINK):
        if p.path_exists(f):
            plan.files_to_remove.append(f)

    # Producer-side env: comment out SIGMOND_CLICKHOUSE_* lines and
    # restart anything that depended on them.  Without this step the
    # producers keep hammering localhost:8123 with retry storms after
    # ClickHouse is torn down (which is exactly what bit us during the
    # first real run of this verb — see psk-recorder log fallout).
    env_text = p.read_text(DEFAULT_COORD_ENV)
    if env_text is not None:
        for line in env_text.splitlines():
            if _CH_ENV_RE.match(line):
                plan.env_lines_to_neutralize.append((DEFAULT_COORD_ENV, line))

    if plan.env_lines_to_neutralize:
        # Only worth restarting consumers when there's actually something
        # to neutralize — a clean reinvocation should be a no-op.
        plan.consumers_to_restart = p.find_units_using_env_file(
            DEFAULT_COORD_ENV,
        )

    return plan


@dataclass
class _ExecutionReport:
    """What execute_removal actually did, for logging and tests."""

    env_files_rewritten: List[str] = field(default_factory=list)
    consumers_restarted: List[str] = field(default_factory=list)
    stopped: List[str] = field(default_factory=list)
    disabled: List[str] = field(default_factory=list)
    purged: List[str] = field(default_factory=list)
    removed_paths: List[str] = field(default_factory=list)
    removed_files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class Runner:
    """Pluggable side-effect surface.  Tests substitute a fake."""

    def run(self, argv: list) -> subprocess.CompletedProcess:
        return subprocess.run(argv, check=False, capture_output=True, text=True)

    def rmtree(self, path: str) -> None:
        shutil.rmtree(path, ignore_errors=True)

    def unlink(self, path: str) -> None:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass

    def rewrite_file(self, path: str, transform: Callable[[str], str]) -> str:
        """Read, transform, write back.  Returns the backup path.

        Backup is written to `<path>.bak-pre-sqlite` so an operator can
        revert with a single `mv`.  We deliberately don't preserve mode
        bits — coordination.env is a root-owned 0644 file by convention,
        and the new file inherits that.
        """
        p = Path(path)
        original = p.read_text()
        backup = path + BACKUP_SUFFIX
        Path(backup).write_text(original)
        p.write_text(transform(original))
        return backup


class NotConfirmed(Exception):
    """Raised when `execute_removal` is called on an unconfirmed plan."""


def _neutralize_clickhouse_lines(text: str) -> str:
    """Prefix every live `SIGMOND_CLICKHOUSE_*=` line with the comment marker.

    Idempotent: lines already starting with `#` don't match `_CH_ENV_RE`.
    Trailing newline of the original file is preserved.
    """
    lines = text.splitlines()
    out = []
    for line in lines:
        if _CH_ENV_RE.match(line):
            out.append(f"{NEUTRALIZED_PREFIX}{line}")
        else:
            out.append(line)
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + suffix


def execute_removal(
    plan: RemovalPlan,
    runner: Optional[Runner] = None,
) -> _ExecutionReport:
    """Execute a confirmed removal plan.  Caller must set plan.confirmed.

    Ordering rationale:

    0. Rewrite env file first.  Consumers won't actually see the change
       until they restart, but doing it before the restarts means the
       single restart cycle is enough to reconfigure them — no second
       pass needed if something else (a sigmond auto-restart) trips it.

    1. Restart consumers BEFORE we tear down ClickHouse.  After restart
       they're using the default-SQLite sink, so subsequent decode
       cycles don't waste a batch hammering a dying CH instance.

    2-6: Tear down CH itself (stop, disable, purge, remove dirs).
    """
    if not plan.confirmed:
        raise NotConfirmed(
            "execute_removal refused: plan.confirmed=False.  Set "
            "plan.confirmed=True only after operator approval (smd "
            "storage migrate-to-sqlite requires --yes)."
        )

    r = runner or Runner()
    report = _ExecutionReport()

    # 0. Neutralize SIGMOND_CLICKHOUSE_* lines.  One rewrite per distinct
    # env file (currently just /etc/sigmond/coordination.env; the
    # structure leaves room for per-client env files later).
    for env_path in plan.env_files_to_rewrite:
        try:
            r.rewrite_file(env_path, _neutralize_clickhouse_lines)
            report.env_files_rewritten.append(env_path)
        except Exception as e:
            report.errors.append(f"rewrite {env_path}: {e}")

    # 1. Restart producers so they pick up the new env BEFORE we kill CH.
    for unit in plan.consumers_to_restart:
        res = r.run(["systemctl", "restart", unit])
        if res.returncode == 0:
            report.consumers_restarted.append(unit)
        else:
            report.errors.append(f"restart {unit}: rc={res.returncode}")

    # 2. Stop CH services so package purge / data removal doesn't race.
    for unit in plan.services_to_stop:
        res = r.run(["systemctl", "stop", unit])
        if res.returncode == 0:
            report.stopped.append(unit)
        else:
            report.errors.append(f"stop {unit}: rc={res.returncode}")

    for unit in plan.services_to_disable:
        res = r.run(["systemctl", "disable", unit])
        # 'disable' is best-effort; missing static units return nonzero
        # without harm.  Record but don't treat as a hard error.
        report.disabled.append(unit)
        if res.returncode != 0:
            logger.debug("disable %s returned rc=%d (ok if not enabled)",
                         unit, res.returncode)

    # 3. Purge Debian packages — `apt-get purge -y` is the only verb
    # that drops both binaries and conffiles.  Skip if dpkg isn't
    # present (non-Debian host).
    if plan.packages_to_purge:
        res = r.run([
            "apt-get", "purge", "-y",
            "--option", "Dpkg::Options::=--force-confnew",
            *plan.packages_to_purge,
        ])
        if res.returncode == 0:
            report.purged.extend(plan.packages_to_purge)
        else:
            report.errors.append(
                f"apt-get purge {plan.packages_to_purge}: "
                f"rc={res.returncode} stderr={res.stderr.strip()[:200]}"
            )

    # 4. Remove data / config / log dirs.  Done after package purge so
    # the postrm scripts can't see (and re-create) directories we
    # intend to delete.
    for path in plan.paths_to_remove:
        r.rmtree(path)
        report.removed_paths.append(path)

    for path in plan.files_to_remove:
        r.unlink(path)
        report.removed_files.append(path)

    # 5. Tell systemd we removed unit files.  Best-effort; not fatal.
    if plan.services_to_disable:
        r.run(["systemctl", "daemon-reload"])

    return report
