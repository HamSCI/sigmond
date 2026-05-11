"""GNSS-VTEC probe — verifies each declared GNSS-VTEC source is alive
and presenting data.

Two protocols supported, selected per-declaration via the ``protocol``
field on ``DeclaredGnssVtec``:

* ``"http"`` (default) — GET /api/tec/status (hf-timestd web API) and
  parse the JSON response.  Falls back to /status.  This is the
  original behaviour; existing manifests don't need changes.
* ``"tcp"`` — open a TCP connection to host:port, read up to
  ``_TCP_PEEK_BYTES`` within the timeout, and declare healthy iff at
  least 1 byte arrives.  No protocol decoding — the manifest's
  ``source`` field declares what's expected (uBlox, Septentrio, …);
  we only verify bytes are flowing.

Both branches emit Observations with ``source="http_gnss"`` so the
observation surface stays stable; ``fields["protocol"]`` records which
branch was taken.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Callable

from ..environment import Environment, Observation


_TCP_PEEK_BYTES = 256          # enough to capture a UBX header or several NMEA sentences


def _default_urlopen(url: str, timeout: float):
    return urllib.request.urlopen(url, timeout=timeout)


def _default_connect(host: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((host, port), timeout=timeout)


def probe(env: Environment, *,
          timeout: float = 3.0,
          limiter=None,
          urlopen: Callable = _default_urlopen,
          connect: Callable = _default_connect,
          ) -> list[Observation]:
    if env.discovery.passive_only:
        return []

    now = time.time()
    out: list[Observation] = []
    for v in env.gnss_vtecs:
        protocol = (getattr(v, "protocol", "http") or "http").lower()
        if protocol == "tcp":
            out.append(_probe_tcp(v, connect, timeout, now))
        else:
            out.append(_probe_http(v, urlopen, timeout, now))
    return out


# ---------------------------------------------------------------------------
# HTTP branch — original behaviour
# ---------------------------------------------------------------------------

def _probe_http(declared, urlopen, timeout, now) -> Observation:
    base = f"http://{declared.host}:{declared.port}"
    endpoint = f"{declared.host}:{declared.port}"

    # Try /api/tec/status first (hf-timestd API), fall back to /status
    status = _fetch(urlopen, f"{base}/api/tec/status", timeout)
    if isinstance(status, Exception):
        status = _fetch(urlopen, f"{base}/status", timeout)
        if isinstance(status, Exception):
            return Observation(
                source="http_gnss", kind="gnss_vtec", id=declared.id,
                endpoint=endpoint, fields={"protocol": "http"},
                observed_at=now, ok=False,
                error=f"HTTP probe failed: {status}",
            )

    fields: dict = _parse_gnss_status(status, declared.source)
    fields["protocol"] = "http"

    return Observation(
        source="http_gnss", kind="gnss_vtec", id=declared.id,
        endpoint=endpoint, fields=fields, observed_at=now, ok=True,
    )


def _fetch(urlopen: Callable, url: str, timeout: float):
    try:
        resp = urlopen(url, timeout)
        body = resp.read()
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        return body
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return e
    except Exception as e:                       # noqa: BLE001
        return e


# ---------------------------------------------------------------------------
# TCP branch — liveness check
# ---------------------------------------------------------------------------

def _probe_tcp(declared, connect, timeout, now) -> Observation:
    endpoint = f"{declared.host}:{declared.port}"
    fields: dict = {"protocol": "tcp", "source": declared.source}

    try:
        sock = connect(declared.host, declared.port, timeout)
    except (OSError, TimeoutError) as e:
        return Observation(
            source="http_gnss", kind="gnss_vtec", id=declared.id,
            endpoint=endpoint, fields=fields, observed_at=now,
            ok=False, error=f"TCP connect failed: {e}",
        )

    try:
        sock.settimeout(timeout)
        chunk = sock.recv(_TCP_PEEK_BYTES)
    except (OSError, TimeoutError) as e:
        chunk = b""
        err = f"TCP read failed: {e}"
    else:
        err = None
    finally:
        try:
            sock.close()
        except OSError:
            pass

    if not chunk:
        return Observation(
            source="http_gnss", kind="gnss_vtec", id=declared.id,
            endpoint=endpoint, fields=fields, observed_at=now,
            ok=False,
            error=err or f"connected but no bytes within {timeout:g}s",
        )

    fields["bytes_read"] = len(chunk)
    fields["first_bytes_hex"] = chunk[:8].hex()
    fields["preamble"] = _identify_preamble(chunk)

    return Observation(
        source="http_gnss", kind="gnss_vtec", id=declared.id,
        endpoint=endpoint, fields=fields, observed_at=now, ok=True,
    )


def _identify_preamble(chunk: bytes) -> str:
    """Best-effort identification of common GNSS framings.  Diagnostic only —
    success doesn't depend on a recognised preamble."""
    if len(chunk) < 2:
        return "unknown"
    if chunk[:2] == b"\xb5\x62":
        return "ubx"
    if chunk[:1] == b"\xd3":
        return "rtcm3"
    if chunk[:1] in (b"$", b"!"):
        return "nmea"
    return "unknown"


# ---------------------------------------------------------------------------
# HTTP body parser — hf-timestd /api/tec/status returns JSON.
# ---------------------------------------------------------------------------

def _parse_gnss_status(body: str, source: str) -> dict:
    out: dict = {}
    out["source"] = source

    # Try JSON first
    try:
        data = json.loads(body)
        out["version"] = data.get("version", "")
        out["name"] = data.get("name", "")
        out["uptime"] = data.get("uptime", "")
        out["stations"] = data.get("stations", 0)
        out["satellites"] = data.get("satellites", 0)
        out["tec_ready"] = data.get("tec_ready", False)
        out["last_update"] = data.get("last_update", "")

        # Extract TEC-specific fields if available
        if "tec" in data:
            out["tec_min"] = data["tec"].get("min", 0)
            out["tec_max"] = data["tec"].get("max", 0)
            out["tec_mean"] = data["tec"].get("mean", 0)

        return out
    except (json.JSONDecodeError, AttributeError):
        pass

    # Fall back to line-oriented parsing
    for line in (body or "").splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()

        if key in ("version", "name", "uptime", "source", "last_update"):
            out[key] = val
        elif key in ("stations", "satellites"):
            try:
                out[key] = int(val)
            except ValueError:
                pass

    return out
