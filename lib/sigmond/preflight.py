"""Pre-flight requirements check for `smd install <client>`.

Before doing any actual install work, walk the client's transitive
`requires` graph (declared in catalog.toml) and report what isn't
satisfied yet.  For ka9q-radio specifically, probe mDNS for any radiod
reachable on the LAN and lsusb for any SDR attached locally so the
message can recommend the most useful next step (install radiod here
vs. point this client at a remote radiod).

When invoked interactively, prompts before proceeding so the operator
doesn't unwittingly install a client that has no upstream to talk to.
`--yes`/`-y` skips the prompt; a non-TTY stdin without `--yes` aborts
with a clear message rather than silently choosing for the operator.
"""

from __future__ import annotations

import sys
from typing import Dict, List, Tuple

from .catalog import CatalogEntry, get_entry, transitive_requires
from .environment import Environment, Observation
from .discovery import mdns, usb_sdr
from .ui import err, heading, info, warn


def check_requires(client: str,
                   catalog: Dict[str, CatalogEntry],
                   *,
                   yes: bool = False) -> bool:
    """Return True to proceed with install, False to abort.

    Looks up `client`'s transitive `requires` (via catalog.transitive_requires)
    and checks each one with `CatalogEntry.is_installed()`.  If anything is
    missing, prints a guided warning (with mDNS/USB context for ka9q-radio)
    and — unless `yes` is set — prompts the operator to confirm.
    """
    entry = get_entry(client, catalog)
    if entry is None:
        # Let the install path surface its own clear "unknown client" error.
        return True

    missing = _unmet_requires(client, catalog)
    if not missing:
        return True

    # Gather extra context for the message.  The probes are cheap (lsusb
    # is local; avahi-browse times out at 3 s per service) but skip them
    # entirely when ka9q-radio isn't the unmet dep, since they're only
    # useful for that specific recommendation.
    radiod_obs: List[Observation] = []
    sdr_obs: List[Observation] = []
    if any(name == "ka9q-radio" for name, _ in missing):
        env = Environment()
        try:
            radiod_obs = [o for o in mdns.probe(env, timeout=2.0)
                          if o.ok and o.kind == "radiod"]
        except Exception:  # noqa: BLE001 — probe must never abort install
            pass
        try:
            sdr_obs = [o for o in usb_sdr.probe(env, extract_serial=False)
                       if o.ok and o.kind == "sdr"]
        except Exception:  # noqa: BLE001 — probe must never abort install
            pass

    _render_warning(client, missing, radiod_obs, sdr_obs)

    if yes:
        warn("--yes passed; proceeding despite unmet requirements")
        return True

    if not sys.stdin.isatty():
        err("non-interactive stdin and --yes not passed; aborting.  "
            "Re-run with --yes to bypass this pre-flight check.")
        return False

    print()
    try:
        resp = input(f"Continue with {client} install anyway? [y/N]: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return resp.strip().lower().startswith("y")


# ---------------------------------------------------------------------------
# Helpers (also exported for tests)
# ---------------------------------------------------------------------------

def _unmet_requires(client: str,
                    catalog: Dict[str, CatalogEntry]
                    ) -> List[Tuple[str, CatalogEntry]]:
    """Return (name, entry) pairs for each transitive requirement that is
    not currently installed locally.  Skips requirements that aren't in
    the catalog at all (the install path's own validation will catch
    those — pre-flight isn't the place to invent missing entries).
    """
    out: List[Tuple[str, CatalogEntry]] = []
    for name in transitive_requires(client, catalog):
        if name == client:
            continue
        req = get_entry(name, catalog)
        if req is None:
            continue
        if req.is_installed():
            continue
        out.append((name, req))
    return out


def _render_warning(client: str,
                    missing: List[Tuple[str, CatalogEntry]],
                    radiod_obs: List[Observation],
                    sdr_obs: List[Observation]) -> None:
    heading(f"pre-flight: {client}")
    warn(f"{client} declares dependencies that aren't all satisfied:")
    for name, _ in missing:
        info(f"missing: {name}")
        if name == "ka9q-radio":
            _explain_radiod_gap(radiod_obs, sdr_obs)


def _explain_radiod_gap(radiod_obs: List[Observation],
                         sdr_obs: List[Observation]) -> None:
    if radiod_obs:
        info(f"  ↳ {len(radiod_obs)} radiod instance(s) reachable on LAN via mDNS:")
        for o in radiod_obs:
            label = o.fields.get("name") or o.endpoint or "(unnamed)"
            info(f"     - {label}")
        info("  ↳ this client can be pointed at one of those "
             "(no local radiod install needed)")
        return

    info("  ↳ no radiod instances reachable on LAN via mDNS either")
    if sdr_obs:
        info(f"  ↳ but {len(sdr_obs)} SDR device(s) attached locally:")
        for o in sdr_obs:
            sdr_type = o.fields.get("sdr_type", "?")
            bus = o.fields.get("bus", "?")
            dev = o.fields.get("device", "?")
            info(f"     - {sdr_type} (bus {bus} dev {dev})")
        info("  ↳ recommended: install radiod here first so this "
             "client has a data source:")
        info("       smd install ka9q-radio")
        info("       smd config init radiod")
    else:
        info("  ↳ and no local SDR detected either — this client "
             "will have no data source")
