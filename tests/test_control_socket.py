"""Tests for sigmond.control_socket — §13 control-socket helpers.

The pure-Python path-resolution helpers (``default_control_socket``,
``resolve_control_socket``) and the no-socket-present failure mode of
``reload_via_socket`` are unit-testable directly.  The HTTP-over-unix-
socket happy path is exercised with a tiny throwaway socket server in
a thread — small enough to keep the test self-contained, large enough
to catch a regression in either UnixHTTPConnection or
reload_via_socket's response parsing.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond.control_socket import (
    UnixHTTPConnection,
    default_control_socket,
    reload_via_socket,
    resolve_control_socket,
)


class DefaultControlSocketTests(unittest.TestCase):
    """Per §13.1: single-instance vs multi-instance path convention."""

    def test_single_instance_when_instance_is_none(self):
        self.assertEqual(
            default_control_socket('hf-timestd', None),
            '/run/hf-timestd/control.sock',
        )

    def test_single_instance_when_instance_is_empty_string(self):
        """Defensive: an empty string should be treated like None
        (concrete unit, no instance suffix)."""
        self.assertEqual(
            default_control_socket('hf-timestd', ''),
            '/run/hf-timestd/control.sock',
        )

    def test_multi_instance(self):
        self.assertEqual(
            default_control_socket('wspr-recorder', 'default'),
            '/run/wspr-recorder/default.control.sock',
        )

    def test_multi_instance_with_hyphenated_name(self):
        """Component names can contain hyphens; the path convention
        doesn't massage them."""
        self.assertEqual(
            default_control_socket('psk-recorder', 'rx888'),
            '/run/psk-recorder/rx888.control.sock',
        )


class ResolveControlSocketTests(unittest.TestCase):
    """Inventory → control_socket resolution with §13.1 fallback."""

    def test_explicit_field_returned(self):
        inv = {'instances': [
            {'instance': 'default', 'control_socket': '/custom/path.sock'},
        ]}
        path = resolve_control_socket('hf-timestd', 'default', inv)
        self.assertEqual(path, '/custom/path.sock')

    def test_absent_field_falls_back_to_default(self):
        inv = {'instances': [{'instance': 'default'}]}
        path = resolve_control_socket('wspr-recorder', 'default', inv)
        self.assertEqual(path, '/run/wspr-recorder/default.control.sock')

    def test_inventory_none_falls_back_to_default(self):
        """When inventory is unavailable (binary missing, parse error),
        we still return the §13.1 default path so '--via=socket' can
        produce a useful "socket not found at X" error pointing at
        the expected location."""
        path = resolve_control_socket('hf-timestd', None, None)
        self.assertEqual(path, '/run/hf-timestd/control.sock')

    def test_instance_not_in_inventory_falls_back_to_default(self):
        """If inventory has instances but none match the requested
        instance name, fall back to the default path for the requested
        instance — don't substitute another instance's socket."""
        inv = {'instances': [
            {'instance': 'rx888-a', 'control_socket': '/custom/a.sock'},
        ]}
        path = resolve_control_socket('psk-recorder', 'rx888-b', inv)
        self.assertEqual(path, '/run/psk-recorder/rx888-b.control.sock')

    def test_concrete_unit_matches_no_instance_entry(self):
        """Concrete (non-templated) unit: instance=None matches the
        inventory entry whose ``instance`` key is missing or empty."""
        inv = {'instances': [
            {'control_socket': '/custom/concrete.sock'},
        ]}
        path = resolve_control_socket('gpsdo-monitor', None, inv)
        self.assertEqual(path, '/custom/concrete.sock')

    def test_empty_string_control_socket_falls_back_to_default(self):
        """A client that publishes ``control_socket = ''`` is treated
        the same as the field being absent (no useful path was given)."""
        inv = {'instances': [
            {'instance': 'default', 'control_socket': ''},
        ]}
        path = resolve_control_socket('hf-timestd', 'default', inv)
        self.assertEqual(path, '/run/hf-timestd/default.control.sock')


class ReloadViaSocketFailureModeTests(unittest.TestCase):
    """The common cases where the socket is unavailable — the only
    behaviour an operator sees today, since no client has implemented
    the §13 server yet."""

    def test_socket_path_does_not_exist(self):
        rc, msg = reload_via_socket('/nonexistent/socket.sock')
        self.assertEqual(rc, 1)
        self.assertIn('socket not found', msg)
        self.assertIn('/nonexistent/socket.sock', msg)

    def test_socket_path_is_a_regular_file(self):
        """A non-socket file at the path → AF_UNIX connect fails.
        We treat this as 'socket error' rather than 'socket not
        found' since the file does exist."""
        with tempfile.NamedTemporaryFile(suffix='.sock') as f:
            rc, msg = reload_via_socket(f.name)
        self.assertEqual(rc, 1)
        # Either 'socket error' (ENOTSOCK from connect) or 'socket
        # connect failed' depending on the kernel; both are correct.
        self.assertTrue(
            'socket error' in msg or 'connect failed' in msg,
            f"unexpected failure message: {msg!r}",
        )


def _start_minimal_socket_server(socket_path: str,
                                  response: bytes,
                                  stop_event: threading.Event) -> threading.Thread:
    """Spin up a one-shot AF_UNIX server that accepts one connection,
    reads the HTTP request line + headers (discarded), and writes
    ``response``.  Returns the worker thread.  Caller is responsible
    for setting stop_event when the test is done."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(socket_path)
    srv.listen(1)
    srv.settimeout(5.0)

    def _worker():
        try:
            while not stop_event.is_set():
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                with conn:
                    conn.settimeout(2.0)
                    # Drain request — read until we see the end of
                    # headers (\r\n\r\n) or run out of data.
                    buf = b''
                    while b'\r\n\r\n' not in buf and len(buf) < 8192:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                    conn.sendall(response)
        finally:
            srv.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t


class ReloadViaSocketHappyPathTests(unittest.TestCase):
    """The HTTP-over-unix-socket round trip — exercises both
    UnixHTTPConnection and reload_via_socket's response parsing
    against a tiny in-process server."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # Sockets must live in a path short enough to fit in the
        # sockaddr_un.sun_path 108-char limit on Linux.
        self.socket_path = os.path.join(self._tmp.name, 'srv.sock')
        self.stop = threading.Event()

    def tearDown(self):
        self.stop.set()
        self._tmp.cleanup()

    def test_200_response_returns_success(self):
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 2\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            b"ok"
        )
        _start_minimal_socket_server(self.socket_path, response, self.stop)
        rc, msg = reload_via_socket(self.socket_path, timeout=3.0)
        self.assertEqual(rc, 0, f"expected success, got {msg!r}")
        self.assertIn("HTTP 200", msg)

    def test_503_response_returns_failure_with_body(self):
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 26\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            b"reload not yet implemented"
        )
        _start_minimal_socket_server(self.socket_path, response, self.stop)
        rc, msg = reload_via_socket(self.socket_path, timeout=3.0)
        self.assertEqual(rc, 1)
        self.assertIn("HTTP 503", msg)
        self.assertIn("reload not yet implemented", msg)


if __name__ == '__main__':
    unittest.main()
