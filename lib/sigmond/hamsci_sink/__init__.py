"""HamSCI sink writer: sigmond's fallback import (CONTRACT §17).

Since v3.70 the sink writer lives in hs-uploader as ``hs_uploader.sink``
(tasks/plan-sink-control.md D14, §10.4 item 1).  Clients import
``hs_uploader.sink`` first and fall back to this package for one release.
Every client that writes a sink declares hs-uploader: psk-recorder,
meteor-scatter, wspr-recorder, codar-sounder, hf-tec and superdarn-sounder,
beside the mag-recorder, hamsci-physics and hf-timestd that carried it already.
This package hands each caller a copy of the same code:

- ``hs_uploader.sink.writer`` when the venv can import it.
- ``sigmond.hamsci_sink._bundled`` otherwise, a verbatim copy that sigmond
  ships for older client checkouts.  ``smd update`` also pulls sigmond before
  hs-uploader, so a recorder restarted between the two pulls meets an
  hs-uploader without ``sink``.

``SINK_IMPL`` names the copy in use: ``"hs_uploader"`` or ``"bundled"``.
tests/test_hamsci_sink_compat.py fails when the two copies differ.

``sigmond.hamsci_sink.writer`` names the chosen module itself, not a
re-export.  Rebinding a global such as ``SINK_CONFIG`` on it does not change
what ``Writer.from_env`` reads, because ``sink_path`` binds its defaults at
definition.  A test passes ``config_file=``, ``legacy_path=`` or ``state_dir=``
to ``sink_path``, or sets the env.

Either copy picks the sink the same way, through ``sink_path()``.  The first
match wins: ``HAMSCI_SINK_PATH``, then ``SIGMOND_SQLITE_PATH``, then ``path`` in
``/etc/hs-uploader/sink.toml``, then ``/var/lib/sigmond/sink.db`` if that file
exists, then ``<state dir>/sink.db`` (``HS_UPLOADER_STATE_DIR`` or
``/var/lib/hs-uploader``) if writable.  With none of these the writer falls
back to a no-op, so a client outside a sigmond install stays safe.
``BufferFull`` reports prolonged sink failure
instead of losing rows silently.
"""

import sys

try:
    from hs_uploader.sink import writer
except ImportError:
    # No hs-uploader in this venv, or one older than v3.70 with no sink package.
    from . import _bundled as writer
    SINK_IMPL = "bundled"
else:
    SINK_IMPL = "hs_uploader"

# Register the chosen module under its old name.  `from
# sigmond.hamsci_sink.writer import HEALTH_OK` then reads the module whose
# globals Writer.from_env uses; no writer.py remains on disk to shadow it.
sys.modules[__name__ + ".writer"] = writer

BufferFull = writer.BufferFull
SqliteConfig = writer.SqliteConfig
Writer = writer.Writer

__all__ = [
    "Writer",
    "BufferFull",
    "SqliteConfig",
    "SINK_IMPL",
]
