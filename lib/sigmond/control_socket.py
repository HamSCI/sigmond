"""CLIENT-CONTRACT.md §13 control-socket helpers.

Used by ``smd reload --via=socket|auto`` to call the per-client unix
sockets that the §13 control surface exposes.

Status note: as of CONTRACT v0.7, no client has yet implemented the
§13 socket server.  Inventory advertises the path (or sigmond infers
the §13.1 default), but the socket file itself is absent in
practice — every conformant client today is reloaded by systemctl.
So ``--via=auto`` falls through to systemctl in real deployments;
``--via=socket`` errors loudly.  The wiring is here for when clients
start landing socket servers (and for the test harness to exercise
the routing logic).

Functions:

- :func:`default_control_socket` — derive the §13.1 conventional path
  from component name + instance.
- :func:`resolve_control_socket` — find the path an inventory entry
  reports (or fall back to the convention if the field is absent).
- :func:`reload_via_socket` — POST /reload to a unix socket and
  return ``(exit_code, message)`` mirroring _run's reporting shape.
- :class:`UnixHTTPConnection` — thin http.client subclass that
  connects over AF_UNIX.  Exposed so callers can do their own
  request bodies if they want.

All stdlib, no third-party dependencies — sigmond's core promises
stdlib-only.
"""

from __future__ import annotations

import http.client
import socket as _socket
from pathlib import Path
from typing import Optional, Tuple


def default_control_socket(component: str,
                           instance: Optional[str]) -> str:
    """Return the §13.1 convention socket path for a (component, instance).

    Single-instance (instance is None or empty)::

        /run/<component>/control.sock

    Multi-instance (instance is a string)::

        /run/<component>/<instance>.control.sock

    The contract requires clients to use this path *or* report an
    explicit override via the ``control_socket`` inventory field.
    """
    if not instance:
        return f"/run/{component}/control.sock"
    return f"/run/{component}/{instance}.control.sock"


def resolve_control_socket(component: str,
                           instance: Optional[str],
                           inventory: Optional[dict]) -> str:
    """Resolve a unit's expected control-socket path.

    Looks for an explicit ``control_socket`` field on the matching
    instance entry in ``inventory['instances']``; falls back to the
    §13.1 default if the field is absent.  Falls back to the default
    *path* (not None) when ``inventory`` itself is None — that lets
    ``smd reload --via=socket`` produce a "socket not found at <path>"
    error pointing at the expected location, which is more useful
    than a generic "inventory unavailable."

    Matching rule: an instance entry whose ``instance`` field equals
    the requested ``instance``.  When ``instance`` is None (concrete
    unit), the entry with no ``instance`` field (or with an empty
    ``instance``) matches.
    """
    if inventory:
        for inst in (inventory.get('instances') or []):
            inst_name = inst.get('instance')
            if inst_name == instance or (not instance and not inst_name):
                explicit = inst.get('control_socket')
                if explicit:
                    return str(explicit)
                break
    return default_control_socket(component, instance)


class UnixHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that connects over a Unix domain socket.

    Use exactly like a normal :class:`http.client.HTTPConnection`;
    pass the path to the unix socket instead of a host/port pair.
    Operationally equivalent to::

        curl --unix-socket /run/<client>/control.sock http://./<path>

    which the contract (§13.1) calls out as a required headless-debug
    surface.
    """

    def __init__(self, socket_path: str, timeout: float = 5.0):
        # The host string is sent in the HTTP request line; "localhost"
        # is the natural choice and works with every contract-conformant
        # server (which doesn't care about Host: when serving over a
        # filesystem-named socket).
        super().__init__('localhost', timeout=timeout)
        self._unix_path = socket_path

    def connect(self):  # noqa: D401 — override semantics
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._unix_path)
        self.sock = sock


def reload_via_socket(socket_path: str,
                      timeout: float = 5.0) -> Tuple[int, str]:
    """POST ``/reload`` to a §13 unix-socket control endpoint.

    Returns ``(exit_code, message)``:

    - ``exit_code == 0`` and ``message`` like ``"HTTP 200"`` when the
      server returned a 2xx response.
    - ``exit_code == 1`` and ``message`` describing why otherwise:
      socket missing, connect failure, HTTP error response, timeout.

    The caller is expected to render the message; this function does
    no printing, no logging, and never raises.  That keeps it easy to
    unit-test by mocking ``UnixHTTPConnection`` (or just pointing it
    at a real test socket).
    """
    if not Path(socket_path).exists():
        return 1, f"socket not found: {socket_path}"
    conn = None
    try:
        conn = UnixHTTPConnection(socket_path, timeout=timeout)
        conn.request('POST', '/reload')
        resp = conn.getresponse()
        body = resp.read().decode('utf-8', errors='replace').strip()
        body_excerpt = body[:200] if body else ''
        if 200 <= resp.status < 300:
            return 0, f"HTTP {resp.status}"
        msg = f"HTTP {resp.status} {resp.reason}"
        if body_excerpt:
            msg = f"{msg}: {body_excerpt}"
        return 1, msg
    except FileNotFoundError:
        # Race: file existed at the Path.exists() check but vanished
        # before connect.  Same operator-facing outcome.
        return 1, f"socket not found: {socket_path}"
    except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError) as e:
        return 1, f"socket connect failed: {e}"
    except (_socket.timeout, TimeoutError):
        return 1, "socket request timed out"
    except OSError as e:
        return 1, f"socket error: {e}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
