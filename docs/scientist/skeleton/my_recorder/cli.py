#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 HamSCI contributors.
#
# Documentation scaffolding shipped with sigmond/docs/scientist/.  Copy it,
# rename it, and make it yours — see ../README.md.  It is not installed by
# sigmond and nothing imports it.
"""my-recorder — the smallest client sigmond will accept, as runnable code.

Four things earn a client its place in `smd status`, and all four are here:

  version            metadata, always exit 0
  inventory --json   what this client costs and where it writes, always exit 0
  validate --json    is this client healthy? nonzero exit when it is not
  daemon             the long-running process the systemd unit starts

plus the wizard surface sigmond's config verbs drive (`config show --json`).

Two rules the contract cares about more than anything else in this file
(sigmond/docs/CLIENT-CONTRACT.md §3):

  1. STDOUT CLEANLINESS.  The contract verbs put the JSON document on stdout
     and nothing else — no banners, no log lines, no progress dots.  Every
     human-readable byte goes to stderr.  `_configure_logging_to_stderr()`
     runs BEFORE argparse so an import-time logger cannot beat the JSON to
     the pipe.  One stray byte makes the payload unparseable for sigmond's
     ContractAdapter and the client reads as "not installed".

  2. `inventory --json` MUST EXIT 0 — including when the config is missing
     or unreadable by the operator's UID (client configs are commonly mode
     0640 and owned by the service user).  A degraded payload with the
     failure recorded in `issues` is conformant; a traceback is not.

Everything below RETURNS data; only `main()` prints.  That split is what
makes the payload builders testable without capturing stdout.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import re
import shutil
import signal
import sys
import threading
from pathlib import Path

try:                                    # Python 3.11+ (the fleet's runtime)
    import tomllib
except ModuleNotFoundError:             # pragma: no cover - 3.10 and older
    tomllib = None

# --- rename these three lines and you have renamed the client ---------------
CLIENT_NAME = "my-recorder"             # also the systemd unit + /etc dir stem
VERSION = "0.1.0"                       # keep in step with deploy.toml [package]
CONTRACT_VERSION = "0.8"                # the CLIENT-CONTRACT.md revision you built against

# Contract §12.5 Pattern A: a client lives at /opt/git/sigmond/<name>/.
CANONICAL_DEPLOY_TOML = f"/opt/git/sigmond/{CLIENT_NAME}/deploy.toml"
CANONICAL_CONFIG = f"/etc/{CLIENT_NAME}/{CLIENT_NAME}-config.toml"
# §19.2: per-instance config wins over the legacy shared path.
INSTANCE_CONFIG_DIR = f"/etc/{CLIENT_NAME}"

# The station envelope, from docs/scientist/station-capabilities.md.  Two spans
# exist and they are not the same: the RX888 Mk II streams 10 kHz - 64 MHz to
# the host (ka9q-radio/docs/SDR/rx888.md), but radiod's LIVE front-end filter is
# narrower -- on AC0G/B4, 2026-08-23, `fe filt low/high` reported 15 kHz -
# 60.912 MHz.  The wider hardware span is the default here because it is the one
# claim true of every DASI2 station; a frequency inside it can still be outside
# your station's passband, so tighten these two numbers to your own station's
# `fe filt low`/`fe filt high` before you rely on `validate` to catch a typo.
RX888_LOW_HZ = 10_000
RX888_HIGH_HZ = 64_000_000
RATE_QUANTUM_HZ = 200                   # radiod serves rates that are a multiple of this
# §19.1 reporter id: uppercase alphanumerics and hyphens, no leading or
# trailing hyphen, at least two characters.  Path-safe by construction — the
# same string becomes the systemd instance, the config stem, the env-file
# stem, the data dir and the log dir, so an id that is wrong here is wrong in
# five places at once.  Check it where you can still refuse to start.
REPORTER_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]*[A-Z0-9]$")
DISK_HEADROOM_BYTES = 1_000_000_000     # refuse to start with less than this free

# Complex I/Q: two components per sample.  Used only for the disk estimate
# sigmond budgets against, so an approximation is honest as long as it is
# labelled — see `disk_writes` below.
BYTES_PER_SAMPLE = {"s16le": 4, "s16be": 4, "f32le": 8, "f32be": 8}

# The defaults live in code so the skeleton runs before anything is
# installed.  A real client renders a config template into /etc/<name>/ from
# a `kind = "render"` install step (ADD-A-CLIENT.md §2) and treats THAT as
# the source of truth.
DEFAULT_CONFIG = {
    "instance": {
        # §19.1 reporter id: [A-Z0-9][A-Z0-9-]*[A-Z0-9].  CHANGE ME.
        "reporter_id": "MY-RECORDER-1",
    },
    "radiod": {
        # The station's radiod control/status multicast name.  CHANGE ME —
        # ask the station operator; `smd admin radiod` prints it.
        "status_dns": "DASI002-status.local",
    },
    "capture": {
        "frequency_hz": 14_110_000,
        "preset": "iq",
        "sample_rate": 12_000,
        "encoding": "f32le",
        "out_dir": f"/var/lib/{CLIENT_NAME}",
    },
    "station": {
        "callsign": "N0CALL",           # CHANGE ME
    },
}

logger = logging.getLogger(CLIENT_NAME)


# --------------------------------------------------------------------------
# stdout cleanliness (§3)
# --------------------------------------------------------------------------
def _configure_logging_to_stderr(verbose: bool) -> None:
    """Send every log record to stderr, before argparse can say anything.

    §11: sigmond may raise or lower the level at runtime without editing
    config, by setting <CLIENT>_LOG_LEVEL in /etc/sigmond/coordination.env
    and sending SIGHUP.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(handler)
    env_level = os.environ.get(CLIENT_NAME.upper().replace("-", "_") + "_LOG_LEVEL")
    root.setLevel(env_level.upper() if env_level else (logging.DEBUG if verbose else logging.INFO))


def _issue(severity: str, message: str, instance: str | None = None) -> dict:
    """One `issues[]` entry.

    Use `fail` and `warn`, and nothing else.  The contract does not
    enumerate the vocabulary, but sigmond's operator-facing rendering
    hard-codes one word: `bin/smd` prints an issue as an ERROR only when
    `severity == "fail"` and as a WARNING for every other string.  So a
    hard failure reported as `"error"` reaches the operator's screen
    looking like a warning.  Every shipped client emits `fail`/`warn`.
    """
    return {"severity": severity, "instance": instance, "message": message}


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
def _deploy_toml_path() -> str:
    """This repo's deploy.toml if we are running from a checkout, else §12.5."""
    local = Path(__file__).resolve().parents[1] / "deploy.toml"
    return str(local) if local.exists() else CANONICAL_DEPLOY_TOML


def resolve_config_path(explicit: str | None, instance: str | None) -> str | None:
    """Which config file this invocation should read, or None for defaults.

    Order: --config wins; then §19.2's per-instance /etc/<client>/<id>.toml;
    then the legacy shared /etc/<client>/<client>-config.toml.  None means
    "nothing installed yet" — the built-in defaults, reported honestly as
    `config_path: null`.
    """
    if explicit:
        return explicit
    if instance:
        per_instance = Path(INSTANCE_CONFIG_DIR) / f"{instance}.toml"
        if per_instance.exists():
            return str(per_instance)
    return CANONICAL_CONFIG if Path(CANONICAL_CONFIG).exists() else None


def _merge(base: dict, overlay: dict) -> dict:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: str | None) -> tuple[dict, list[dict]]:
    """(config, issues).  Never raises — see rule 2 in the module docstring."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if path is None:
        return cfg, []
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return cfg, [_issue("fail", f"config {path} does not exist; reporting built-in defaults")]
    except PermissionError:
        return cfg, [_issue("fail", f"config {path} is not readable by uid {os.geteuid()}; "
                                    "reporting built-in defaults")]
    except OSError as exc:
        return cfg, [_issue("fail", f"config {path} could not be read: {exc}")]
    if tomllib is None:
        return cfg, [_issue("fail", "python is older than 3.11: no tomllib to parse the config")]
    try:
        _merge(cfg, tomllib.loads(raw.decode("utf-8")))
    except Exception as exc:
        return cfg, [_issue("fail", f"config {path} is not valid TOML: {exc}")]
    return cfg, []


# --------------------------------------------------------------------------
# contract payloads (§3)
# --------------------------------------------------------------------------
def build_version() -> dict:
    return {
        "client": CLIENT_NAME,
        "version": VERSION,
        "contract_version": CONTRACT_VERSION,
        "python": sys.version.split()[0],
        "deploy_toml_path": _deploy_toml_path(),
    }


def instance_entry(cfg: dict) -> dict:
    """One `instances[]` entry — sigmond's whole per-instance view.

    §18: this skeleton is an RTP-default client.  It labels its samples from
    radiod's own GPS-referenced anchor and never subscribes to a timing
    authority, so `timing_authority_applied` is null and
    `uses_timing_calibration` is false.  Claim otherwise only when you
    really do apply a correction — the field is what a downstream analyst
    reads to decide how much the timestamps are worth.
    """
    cap = cfg["capture"]
    per_sample = BYTES_PER_SAMPLE.get(str(cap["encoding"]).lower(), 8)
    bytes_per_day = int(cap["sample_rate"]) * per_sample * 86400
    status_dns = cfg["radiod"]["status_dns"]
    return {
        "instance": cfg["instance"]["reporter_id"],
        "radiod_id": status_dns,
        "radiod_status_dns": status_dns,
        "host": "localhost",
        "required_cores": [],
        "preferred_cores": "worker",
        "frequencies_hz": [int(cap["frequency_hz"])],
        "ka9q_channels": 1,
        # §7: read this back from ka9q-python once the channel exists; never
        # compute a multicast address yourself.  null until then.
        "data_destination": None,
        "disk_writes": [
            {
                "path": str(cap["out_dir"]),
                "mb_per_day": round(bytes_per_day / 1e6, 1),
                "retention_days": 0,     # 0 = operator-managed, no auto-prune
            }
        ],
        "uses_timing_calibration": False,
        "provides_timing_calibration": False,
        "timing_authority_applied": None,
        # §16.3: how this instance receives samples.
        "data_path": {"kind": "radiod-ka9q-python", "radiod_id": status_dns},
        "deploy_toml_path": _deploy_toml_path(),
    }


def build_inventory(cfg: dict, config_path: str | None, issues: list[dict]) -> dict:
    return {
        "client": CLIENT_NAME,
        "version": VERSION,
        "contract_version": CONTRACT_VERSION,
        "config_path": config_path,
        "instances": [instance_entry(cfg)],
        "deps": {"pypi": [{"name": "ka9q-python", "version": "3.25.2"}]},
        "issues": issues,
    }


def _nearest_existing_ancestor(path: Path) -> Path | None:
    """The first directory at or above `path` that exists.

    An output directory is routinely absent until the deploy `mkdir` step
    creates it, and that alone must not fail validate — but `disk_usage()`
    needs a real path.  (Pattern borrowed from the event-recorder client.)
    """
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return None


def build_validate(cfg: dict, issues: list[dict]) -> dict:
    """§3 `validate --json`: is this client fit to run right now?

    Check the things that would silently produce nothing: an envelope the
    station cannot serve, and a disk that cannot hold the output.
    """
    out = list(issues)
    cap = cfg["capture"]
    name = cfg["instance"]["reporter_id"]

    freq = float(cap["frequency_hz"])
    if not (RX888_LOW_HZ <= freq <= RX888_HIGH_HZ):
        out.append(_issue("fail", f"frequency {freq:.0f} Hz is outside the RX888 front end's "
                                   f"{RX888_LOW_HZ}-{RX888_HIGH_HZ} Hz span", name))

    rate = int(cap["sample_rate"])
    if rate <= 0 or rate % RATE_QUANTUM_HZ:
        out.append(_issue("fail", f"sample_rate {rate} is not a positive multiple of "
                                   f"{RATE_QUANTUM_HZ} Hz; radiod will not serve it", name))

    if not REPORTER_ID_RE.match(str(name)):
        out.append(_issue("fail", f"reporter id {name!r} does not match the §19.1 form "
                                  "[A-Z0-9][A-Z0-9-]*[A-Z0-9]", name))

    if not str(cfg["station"]["callsign"]).strip():
        out.append(_issue("fail", "station.callsign is unset", name))

    anchor = _nearest_existing_ancestor(Path(cap["out_dir"]))
    if anchor is None:
        out.append(_issue("fail", f"no existing ancestor of {cap['out_dir']} to measure", name))
    else:
        free = shutil.disk_usage(anchor).free
        if free < DISK_HEADROOM_BYTES:
            out.append(_issue("fail", f"only {free / 1e9:.1f} GB free at {anchor}", name))

    return {"ok": not any(i["severity"] == "fail" for i in out), "issues": out}


# --------------------------------------------------------------------------
# daemon — what the systemd unit starts
# --------------------------------------------------------------------------
def run_daemon(cfg: dict, stop: threading.Event) -> int:
    """The capture loop, stubbed.

    Replace the body with your recorder: open ONE radiod channel through
    ka9q-python with an explicit `lifetime`, then write its bytes plus a
    timing sidecar — the Tier-0 recipe in
    sigmond/docs/scientist/capture-quickstart.md is the same code, and it
    drops straight in here.  What matters at this layer is the shape kept
    below: log to stderr (systemd captures it), and exit cleanly on SIGTERM
    so `smd restart` is not a data-loss event.
    """
    cap = cfg["capture"]
    out_dir = Path(cap["out_dir"])
    logger.info("starting: instance=%s %.6f MHz preset=%s rate=%d encoding=%s out=%s",
                cfg["instance"]["reporter_id"], float(cap["frequency_hz"]) / 1e6,
                cap["preset"], cap["sample_rate"], cap["encoding"], out_dir)
    ticks = 0
    while not stop.is_set():
        ticks += 1
        logger.info("tick %d — replace this with your capture loop "
                    "(out_dir=%s, present=%s)", ticks, out_dir, out_dir.is_dir())
        stop.wait(10.0)
    logger.info("stopped cleanly after %d tick(s)", ticks)
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=CLIENT_NAME, description="minimal sigmond client skeleton")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--config", metavar="PATH", help="config file (default: see resolve_config_path)")
    p.add_argument("--instance", metavar="REPORTER_ID", help="per-instance config to use (§19.2)")
    sub = p.add_subparsers(dest="cmd", required=True)

    for verb, helptext in (("version", "client metadata"),
                           ("inventory", "resource view per instance"),
                           ("validate", "self-validate every instance")):
        s = sub.add_parser(verb, help=f"[contract §3] {helptext}")
        s.add_argument("--json", action="store_true", default=True,
                       help="emit JSON (always on; accepted for contract symmetry)")

    d = sub.add_parser("daemon", help="[contract §3] the long-running process")
    # Accept BOTH spellings the fleet's unit files use: the positional
    # (`daemon %i`) and §19.2's flag (`daemon --instance %i`).  Costs two
    # lines here and removes a whole class of "unit starts, exits 2".
    d.add_argument("instance_arg", nargs="?", metavar="REPORTER_ID",
                   help="instance name; the unit passes %%i here")
    d.add_argument("--instance", dest="instance_flag", metavar="REPORTER_ID",
                   help="same thing, as a flag (CLIENT-CONTRACT §19.2)")

    c = sub.add_parser("config", help="[contract §14] configuration interview")
    csub = c.add_subparsers(dest="config_cmd", required=True)
    show = csub.add_parser("show", help="machine-readable current config")
    show.add_argument("--json", action="store_true", default=True)
    show.add_argument("--defaults", action="store_true",
                      help="report the shipped defaults instead of the installed config")
    # `config init` / `config edit` are the interactive halves sigmond's
    # `smd config init|edit <client>` verbs invoke via [contract.config] in
    # deploy.toml (§14.1); `config apply --json -` reads a partial config on
    # stdin and saves it.  Write them when you have a config worth editing.
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _configure_logging_to_stderr("-v" in argv or "--verbose" in argv)
    args = build_parser().parse_args(argv)

    instance = (args.instance
                or getattr(args, "instance_flag", None)
                or getattr(args, "instance_arg", None))
    config_path = resolve_config_path(args.config, instance)
    cfg, issues = load_config(config_path)
    if instance:
        cfg["instance"]["reporter_id"] = instance

    if args.cmd == "version":
        doc, code = build_version(), 0
    elif args.cmd == "inventory":
        # Always 0.  sigmond's `installed` flag depends on it (ADD-A-CLIENT §4).
        doc, code = build_inventory(cfg, config_path, issues), 0
    elif args.cmd == "validate":
        doc = build_validate(cfg, issues)
        code = 0 if doc["ok"] else 1
    elif args.cmd == "config":
        doc = copy.deepcopy(DEFAULT_CONFIG) if args.defaults else cfg
        code = 0
    else:
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda s, f: stop.set())
        return run_daemon(cfg, stop)

    print(json.dumps(doc, indent=2))    # the ONLY write to stdout in this process
    return code


if __name__ == "__main__":              # §12.1: without this the unit exits 0 doing nothing
    sys.exit(main())
