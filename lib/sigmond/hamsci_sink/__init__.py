"""HamSCI sink writer: sigmond's compatibility import (CONTRACT §17).

Since v3.70 the sink writer lives in hs-uploader as ``hs_uploader.sink``
(tasks/plan-sink-control.md D14, §10.4 item 1).  Clients still import it from
here, and this package hands each one a copy of the same code:

- ``hs_uploader.sink.writer`` when the client's venv can import it.  Six
  clients carry hs-uploader as an editable sibling: psk-recorder,
  meteor-scatter, wspr-recorder, mag-recorder, hamsci-physics and hf-timestd.
- ``sigmond.hamsci_sink._bundled`` otherwise, a verbatim copy that sigmond
  ships.  codar-sounder, hf-tec and superdarn-sounder carry sigmond but no
  hs-uploader.  ``smd update`` also pulls sigmond before hs-uploader, so a
  recorder restarted between the two pulls meets an hs-uploader without
  ``sink``.

``SINK_IMPL`` names the copy in use: ``"hs_uploader"`` or ``"bundled"``.
tests/test_hamsci_sink_compat.py fails when the two copies differ.

``sigmond.hamsci_sink.writer`` names the chosen module itself, not a
re-export.  A global set through it, such as ``_DEFAULT_SQLITE_PATH`` in a
test, changes what ``Writer.from_env`` reads.

Either copy picks the sink the same way.  ``SIGMOND_SQLITE_PATH`` names it
when set.  Otherwise the writer uses ``/var/lib/sigmond/sink.db`` if that
directory is writable, and falls back to a no-op, so a client outside a
sigmond install stays safe.  ``BufferFull`` reports prolonged sink failure
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
