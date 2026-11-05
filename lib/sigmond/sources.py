"""Per-client SDR source selection model.

Each HamSCI client (wspr-recorder, psk-recorder, future
hfdl/codar/etc.) can be served by **zero-or-more** SDR sources — a
mix of local USB SDRs, local or remote radiod control planes, and
KiwiSDRs.  This module is the **selection** layer: it answers
"which sources should client X consume from?" and persists the
answer as ``/etc/sigmond/clients/<client>.sources.toml``.

Discovery (what's *visible* on the LAN) lives in
``sigmond.discovery.*`` and is consumed here read-only via
``inventory()``.

Source key grammar
==================

Every source has a **stable**, human-readable key of the form
``<type>:<identifier>``:

  * ``radiod:<hostname-or-ip>``      — a ka9q-radio control plane,
                                       identified by the mDNS hostname
                                       it publishes (preferred) or the
                                       multicast group IP (fallback when
                                       hostname isn't resolvable).  No
                                       port — radiod always uses 5006
                                       for status.
  * ``kiwisdr:<host>:<port>``        — a KiwiSDR's HTTP endpoint.
                                       Port included because KiwiSDRs
                                       are sometimes on non-default
                                       ports (8073 is the default).
  * ``usb:<vid>:<pid>:<serial>``     — a local USB SDR, identified by
                                       USB vendor/product ID and the
                                       device's iSerial string.  Stable
                                       across reboots and across USB
                                       hub re-plug events.

The key is what operators type at the CLI and what gets stored on
disk.  A separate ``label`` (free-form operator string) lives in
``/var/lib/sigmond/sdr-labels.toml`` and is used for display only;
edits to the label never invalidate selections.

Data model
==========

  ``SourceKey``     — parsed ``<type>:<identifier>`` token.
  ``ClientSources`` — per-client list of SourceKey, with load/save.
  ``InventoryRow``  — joined view of one source as it appears to the
                      operator: key + display label + reachability
                      hint, sourced from environment probe + label
                      store.

No client-facing config rendering yet — that lands in Phase 3 when
wspr-recorder starts accepting multiple sources.  For now ``apply()``
returns a structured diff for the operator to inspect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:
    import tomllib                              # py 3.11+
except ImportError:                             # pragma: no cover — py 3.10 fallback
    import tomli as tomllib                     # type: ignore[no-redef]


# Operator-facing CLI surface uses these literals verbatim — keep
# stable across releases.  Adding a new type requires a coordinated
# bump in both the discovery probes and any rendering code that
# consumes them.
VALID_SOURCE_TYPES = frozenset({"radiod", "kiwisdr", "usb"})

# Default storage roots.  Overridable in tests + when running under a
# non-root user (CI fixtures, integration harnesses).
DEFAULT_CLIENTS_ROOT = Path("/etc/sigmond/clients")


# Clients that participate in source selection.  Adding a new client
# means writing a renderer in Phase 3+ that consumes its selection —
# until then the CLI is happy to record selections for any client name
# (treat unknown names as opt-in / forward-compat).
KNOWN_CLIENTS = (
    "wspr-recorder",
    "psk-recorder",
    "hfdl-recorder",   # future
    "codar-sounder",   # future
)


# ---------------------------------------------------------------------------
# SourceKey
# ---------------------------------------------------------------------------

# Identifier subset reservations.  Plain text + : is fine inside the
# identifier portion (for ``kiwisdr:1.2.3.4:8073``), but we reject
# whitespace, quotes, and shell metacharacters up front so an operator
# typo doesn't produce something we'd have to escape later.
_INVALID_ID_CHARS = re.compile(r"[\s\'\"\\\$\;\|\&<>(){}\[\]]")


@dataclass(frozen=True)
class SourceKey:
    """One ``<type>:<identifier>`` selection token.

    The frozen dataclass gives us hashing for free — selections are
    stored as sets/dicts keyed by SourceKey in higher-level code.
    """
    type: str
    identifier: str

    def __post_init__(self) -> None:
        if self.type not in VALID_SOURCE_TYPES:
            raise ValueError(
                f"unknown source type {self.type!r}; "
                f"expected one of {sorted(VALID_SOURCE_TYPES)}"
            )
        if not self.identifier:
            raise ValueError("source identifier cannot be empty")
        if _INVALID_ID_CHARS.search(self.identifier):
            raise ValueError(
                f"source identifier {self.identifier!r} contains "
                "whitespace or shell metacharacters"
            )

    def __str__(self) -> str:
        return f"{self.type}:{self.identifier}"

    @classmethod
    def parse(cls, raw: str) -> "SourceKey":
        """Parse a colon-separated source-key string.

        Empty input, missing type prefix, and unknown types all raise
        ``ValueError`` with operator-readable messages.
        """
        if not raw or not isinstance(raw, str):
            raise ValueError("empty source key")
        if ":" not in raw:
            raise ValueError(
                f"source key {raw!r} must be '<type>:<identifier>' "
                f"(types: {sorted(VALID_SOURCE_TYPES)})"
            )
        type_, _, identifier = raw.partition(":")
        return cls(type=type_.strip(), identifier=identifier.strip())


# ---------------------------------------------------------------------------
# Per-client selection
# ---------------------------------------------------------------------------

@dataclass
class ClientSources:
    """The selection for one client — ordered list of SourceKey.

    Order matters because some clients may treat the first source as
    primary (e.g., for cycle alignment) and additional ones as
    diversity inputs.  The CLI preserves operator-specified order;
    deduplication is the caller's responsibility.
    """
    client: str
    selected: list[SourceKey] = field(default_factory=list)

    # ------------------------------- I/O ----------------------------------

    @staticmethod
    def _path(client: str, root: Path) -> Path:
        return root / f"{client}.sources.toml"

    @classmethod
    def load(
        cls, client: str, root: Path = DEFAULT_CLIENTS_ROOT,
    ) -> "ClientSources":
        """Load the saved selection for ``client``.

        A missing file is *not* an error — it just means "no selection
        yet"; we return an empty ClientSources.  Other I/O / parse
        errors propagate so the operator sees them.
        """
        p = cls._path(client, root)
        if not p.exists():
            return cls(client=client, selected=[])
        with p.open("rb") as f:
            data = tomllib.load(f)
        raw = data.get("selected", [])
        if not isinstance(raw, list):
            raise ValueError(
                f"{p}: 'selected' must be an array of strings, got {type(raw).__name__}"
            )
        return cls(
            client=client,
            selected=[SourceKey.parse(s) for s in raw],
        )

    def save(self, root: Path = DEFAULT_CLIENTS_ROOT) -> Path:
        """Write the selection to ``<root>/<client>.sources.toml``.

        Creates ``root`` and any parents.  Atomic-via-rename so an
        interrupted write leaves the prior file intact.
        """
        root.mkdir(parents=True, exist_ok=True)
        p = self._path(self.client, root)
        tmp = p.with_suffix(p.suffix + ".tmp")
        # tomllib is read-only in stdlib; for write we hand-format the
        # tiny schema so we don't add a tomli-w dependency for one
        # array.  Keep the file readable for operators editing by hand.
        body_lines = [
            f"# /etc/sigmond/clients/{self.client}.sources.toml",
            f"# Source selection for {self.client}.  Managed by",
            "# `smd admin sources add|remove|apply`.  Manual edits are",
            "# preserved but must remain valid TOML.",
            "",
            "selected = [",
        ]
        for k in self.selected:
            # Plain double-quoted strings; identifiers were already
            # vetted against shell metacharacters at construction time.
            body_lines.append(f'  "{k}",')
        body_lines.append("]")
        body_lines.append("")
        tmp.write_text("\n".join(body_lines))
        tmp.replace(p)
        return p

    # ------------------------------- ops ----------------------------------

    def add(self, key: SourceKey) -> bool:
        """Append ``key`` if not already present.  Returns True on add."""
        if key in self.selected:
            return False
        self.selected.append(key)
        return True

    def remove(self, key: SourceKey) -> bool:
        """Drop ``key`` if present.  Returns True on removal."""
        try:
            self.selected.remove(key)
        except ValueError:
            return False
        return True

    def has(self, key: SourceKey) -> bool:
        return key in self.selected


# ---------------------------------------------------------------------------
# Inventory join (read-only, sources are surfaced from elsewhere)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InventoryRow:
    """One source as it appears to the operator: stable key + display
    label + a short reachability hint.

    ``observed_at`` is the unix epoch time of the last successful
    probe.  ``None`` means "configured but never observed" — possible
    when an operator hand-added a selection for a source that hasn't
    yet been discovered.
    """
    key: SourceKey
    label: str
    reachability: str   # "ok" | "stale" | "unknown"
    observed_at: float | None = None
    fields: dict = field(default_factory=dict)


def _radiod_key_from_observation(obs) -> SourceKey | None:
    """Build a SourceKey from an mDNS radiod Observation.

    Prefer the mDNS hostname (the operator-controlled stable name) —
    fall back to the multicast group address if the hostname is
    missing or unresolvable.  The endpoint field is
    ``<hostname>:<port>`` post-parse, so trim the ``:5006`` suffix
    since all radiod control planes use the same port.
    """
    endpoint = getattr(obs, "endpoint", "") or ""
    hostname, _, _port = endpoint.partition(":")
    if hostname and hostname != "0.0.0.0":
        return SourceKey(type="radiod", identifier=hostname)
    addr = (obs.fields or {}).get("address")
    if addr:
        return SourceKey(type="radiod", identifier=addr)
    return None


def where(row: "InventoryRow", local_host: str) -> str:
    """Where a discovered source runs, for the operator's eye: ``local``
    when a radiod's TXT ``source=`` names this machine, else that host
    name; a Kiwi is its address.  ``?`` when the producer said nothing.
    The station can mix local and remote radiods (KFS: two local RX888s
    plus a Beelink's two), so the list must say which is which (rob,
    2026-11-01)."""
    f = row.fields or {}
    if row.key.type == "radiod":
        host = (f.get("source_host") or "").strip()
        if not host:
            return "?"
        a = host.split(".")[0].lower()
        b = (local_host or "").split(".")[0].lower()
        return "local" if a and a == b else host
    if row.key.type == "kiwisdr":
        return f.get("address") or row.key.identifier
    return "?"


def inventory(observations: Iterable) -> list[InventoryRow]:
    """Project a list of discovery ``Observation``s into operator-facing
    inventory rows.

    Unknown source kinds are dropped silently — callers can pass the
    full probe output without filtering.  Duplicate keys are kept
    once (first occurrence wins); inventory is meant to be the
    deduplicated *catalogue* of stable identities.
    """
    seen: set[SourceKey] = set()
    rows: list[InventoryRow] = []
    for o in observations:
        if not getattr(o, "ok", False):
            continue
        kind = getattr(o, "kind", "")
        key: SourceKey | None = None
        label = ""
        if kind == "radiod":
            key = _radiod_key_from_observation(o)
            label = (o.fields or {}).get("mdns_name", "") or ""
        elif kind == "kiwisdr":
            ep = getattr(o, "endpoint", "") or ""
            if ep:
                key = SourceKey(type="kiwisdr", identifier=ep)
                label = (o.fields or {}).get("mdns_name", "") or ep
        # usb_sdr probes emit kind="sdr"; we'll wire those when the
        # USB iSerial reading is reliable across hosts.  Phase 2 ships
        # without USB selection — operators bring up radiod with its
        # usb-attached frontend and select the radiod control plane,
        # not the USB endpoint directly.
        if key is None or key in seen:
            continue
        seen.add(key)
        rows.append(InventoryRow(
            key=key,
            label=label or str(key),
            reachability="ok",
            observed_at=getattr(o, "observed_at", None),
            fields=dict(o.fields or {}),
        ))
    return rows


# ---------------------------------------------------------------------------
# Radiod discovery, selection and naming (spec 4.4)
# ---------------------------------------------------------------------------

class SourceSelectionError(ValueError):
    """A radiod the operator named is absent, or two derive one reporter ID."""


@dataclass(frozen=True)
class DiscoveredRadiod:
    status_dns: str   # "ac0g-b1-a-status.local"
    label: str        # advertised name, "AC0G @EM38ww B1-a longwire"
    host: str         # TXT source_host, else the mDNS hostname


_MDNS_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_MDNS_HOSTNAME = re.compile(rf"{_MDNS_LABEL}(?:\.{_MDNS_LABEL})*")


def valid_mdns_hostname(name) -> bool:
    """A DNS host name: dot-separated labels of letters, digits and inner
    hyphens, each at most 63 characters, at most 253 in all.

    Every radiod name and host that sigmond writes into coordination.toml
    passes this first.  A LAN device chooses what it advertises, and a `"`,
    `\\` or newline there would otherwise reach coordination.toml and,
    through render_env, the EnvironmentFile of root services."""
    return (isinstance(name, str) and 0 < len(name) <= 253
            and _MDNS_HOSTNAME.fullmatch(name) is not None)


def has_control_chars(text: str) -> bool:
    """True when ``text`` holds a C0 or C1 control character
    (U+0000-U+001F, U+007F-U+009F)."""
    return any(ord(ch) < 0x20 or 0x7f <= ord(ch) <= 0x9f for ch in text)


_REFUSED_ADVERTS: set = set()


def discover_radiods(timeout: float = 5.0) -> list[DiscoveredRadiod]:
    """Browse mDNS through the same path `smd admin sources` uses.

    Drops, with one warning per value, any radiod whose status name or host
    is not a valid host name (see valid_mdns_hostname)."""
    import logging
    from sigmond.environment import Environment
    from sigmond.discovery import mdns
    rows = inventory(mdns.probe(Environment(), timeout=timeout))
    out = []
    for r in rows:
        if r.key.type != "radiod":
            continue
        host = ((r.fields or {}).get("source_host") or "").strip()
        dns = r.key.identifier
        bad = [v for v in (dns, host or dns) if not valid_mdns_hostname(v)]
        # The label reaches the operator's terminal in bring-up's bind list.
        # mdns decodes avahi's `\\010` to a newline and `\\027` to ESC, so a
        # crafted label could forge a "bind …" line or hide one (security
        # review, 2026-11-05).
        if has_control_chars(r.label or ""):
            bad.append(r.label)
        if bad:
            for v in bad:
                if v not in _REFUSED_ADVERTS:
                    _REFUSED_ADVERTS.add(v)
                    logging.getLogger(__name__).warning(
                        "ignoring radiod advertisement: %r is not a valid "
                        "host name or carries a control character", v)
            continue
        out.append(DiscoveredRadiod(status_dns=dns, label=r.label,
                                    host=host or dns))
    return out


def advertised_call(label: str) -> str:
    """First token of the advertised name, upper-cased, `/suffix` removed.

    sigma (2026-11-05) advertises "AC0G/S RX-888 Mk2": its first token is
    a reporter ID, and the station's call is the part before the slash.
    """
    toks = (label or "").split()
    return toks[0].split("/")[0].upper() if toks else ""


def reporter_id_for(callsign: str, status_dns: str) -> str:
    """`ac0g-b1-a-status.local` under AC0G gives `AC0G/B1A`."""
    call = callsign.upper()
    name = status_dns.lower()
    if name.endswith("-status.local"):
        name = name[: -len("-status.local")]
    elif name.endswith(".local"):
        name = name[: -len(".local")]
    if name.startswith(call.lower() + "-"):
        name = name[len(call) + 1:]
    tail = re.sub(r"[^A-Za-z0-9]", "", name).upper()
    if not tail:
        # Nothing left to name the instance: "AC0G/" is not a reporter ID.
        raise SourceSelectionError(
            f"{status_dns}: no usable name left for a reporter ID")
    return f"{call}/{tail}"


def select_radiods(found: list[DiscoveredRadiod], callsign: str,
                   named: list[str]) -> list[DiscoveredRadiod]:
    """Keep the radiods that belong to this station.

    `named` (--remote-radiod) names radiods outright and overrides the
    callsign rule; a name that did not answer is an error.  With none
    named, keep radiods advertising the station's own callsign.  Two
    kept radiods that derive one reporter ID would share an instance
    (ac0g-b1-a and ac0g-b1a both give AC0G/B1A), so that is an error.
    """
    if named:
        by_dns = {r.status_dns.lower(): r for r in found}
        keep = []
        for n in named:
            r = by_dns.get(n.lower())
            if r is None:
                raise SourceSelectionError(n)
            keep.append(r)
    else:
        keep = [r for r in found if advertised_call(r.label) == callsign.upper()]
    seen: dict[str, DiscoveredRadiod] = {}
    for r in keep:
        rid = reporter_id_for(callsign, r.status_dns)
        if rid in seen:
            raise SourceSelectionError(
                f"{seen[rid].status_dns} and {r.status_dns} both give "
                f"reporter ID {rid}")
        seen[rid] = r
    return keep


# ---------------------------------------------------------------------------
# Top-level convenience for the CLI
# ---------------------------------------------------------------------------

def load_all_selections(
    clients: Iterable[str] = KNOWN_CLIENTS,
    root: Path = DEFAULT_CLIENTS_ROOT,
) -> dict[str, ClientSources]:
    """Load every known client's selection into a single dict."""
    return {c: ClientSources.load(c, root=root) for c in clients}
