"""Render one fixed set of inputs through a given ``uploader_manifest`` module.

``tests/test_uploader_render_forms.py`` renders these inputs twice: with
this checkout's module, in the test process, and with the branch base's
module (sigmond be91e22, part 1), in a separate interpreter whose
``sys.path`` holds only that old tree.  The two versions never meet in one
interpreter, and both see exactly the same inputs because both come from
here.

This file imports no ``sigmond`` module at the top: the caller passes the
module, and the coordination types come from whichever ``sigmond`` that
interpreter loaded.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

# psk-recorder and meteor-scatter both declare this block today
# (tests/test_uploader_manifest_split.py SHARED_BLOCK, verbatim).
SHARED_BLOCK = """
[[hs_uploader.pipeline]]
name = "psk-pskreporter"
batch_limit = 500
[hs_uploader.pipeline.source]
type = "sqlite"
database = "psk"
table = "spots"
accepted_schema_versions = [2]
start_at = "now"
delete_on_commit = false
select_columns = ["time", "frequency", "mode", "snr_db", "tx_call", "grid", "score", "message", "rx_source", "frequency_bucket_hz"]
extra_where = [["tx_call", "!=", ""], ["mode", "IN", ["ft8", "ft4", "msk144"]], ["forward_to_pskreporter", "=", 0]]
[hs_uploader.pipeline.transport]
type = "pskreporter"
decoding_software = "psk-recorder/0.1 (radiod={radiod_status})"
[hs_uploader.pipeline.retry]
base = 2.0
cap_sec = 300.0
"""

WSPR_BLOCK = """
[[hs_uploader.pipeline]]
name = "wspr-wsprnet"
batch_limit = 900
[hs_uploader.pipeline.source]
type = "wspr_cycle"
database = "wspr"
table = "spots"
[hs_uploader.pipeline.transport]
type = "wsprnet"
reporter = "{call}"
grid = "{grid}"
[hs_uploader.pipeline.retry]
base = 2.0
cap_sec = 900.0
"""

BODIES = {
    "wspr-recorder": WSPR_BLOCK,
    "psk-recorder": SHARED_BLOCK,
    "meteor-scatter": SHARED_BLOCK,
}

PSK = ("psk-recorder", "pskreporter")


class _Topo:
    def __init__(self, names):
        self._names = list(names)

    def enabled_components(self, only=None):
        return list(self._names)


def coordination(mode: str, *, heartbeat: bool = True):
    """The coordination every render here reads; ``mode`` sets ``[uploads]``."""
    from sigmond.coordination import (Coordination, Heartbeat, Host, Radiod,
                                      Uploads)
    hb = (Heartbeat(enabled=True, station="AC0G-B4", host="hb.example.org")
          if heartbeat else Heartbeat())
    return Coordination(
        host=Host(call="AC0G", grid="EM38ww"),
        radiods={"sigma-status.local": Radiod(id="sigma-status.local")},
        heartbeat=hb,
        uploads=Uploads(enabled=(mode == "upload"), reason="bench",
                        mode=mode))


def render(um, tmp: Path, mode: str, *, store_version=2, supports_discard=True,
           heartbeat: bool = True, senders=(), forms=None):
    """Render the fixed inputs through `um`.

    ``forms`` None calls ``um.generate()`` and returns its text (the only
    call the part 1 module offers).  A dict calls ``um.generate_forms(**forms)``
    and returns ``(legacy_text, sigmond_file)``.  No probe may run: the
    probes raise, so a render that asks the venv fails loudly."""
    tmp = Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    paths = {}
    for client, body in BODIES.items():
        p = tmp / f"{client}.toml"
        p.write_text(body)
        paths[client] = p

    def no_probe(*a, **k):
        raise AssertionError("the render asked the daemon's venv")

    with mock.patch.object(um, "list_instances", return_value=[]), \
         mock.patch.object(um, "host_key_file", return_value="/k"), \
         mock.patch.object(um, "find_deploy_toml", side_effect=paths.get), \
         mock.patch.object(um.psws, "read_state",
                           side_effect=FileNotFoundError("fixture")), \
         mock.patch.object(um, "hs_uploader_supports_discard",
                           side_effect=no_probe), \
         mock.patch.object(um, "hs_uploader_store_version",
                           side_effect=no_probe), \
         mock.patch("sigmond.inprocess_senders.inprocess_senders",
                    return_value=set(senders)):
        topo = _Topo(BODIES)
        coord = coordination(mode, heartbeat=heartbeat)
        if forms is None:
            return um.generate(topo, coord, supports_discard=supports_discard,
                               store_version=store_version)
        r = um.generate_forms(topo, coord, supports_discard=supports_discard,
                              store_version=store_version, **forms)
        return r.legacy_text, r.sigmond_file


def main(argv) -> int:
    """``render_forms_driver.py <old lib dir> <tmp dir>``, cases as JSON on stdin.

    Each case is ``[key, mode, {render keywords}]``; prints ``{key: text}``
    rendered through the ``sigmond`` found under the old lib dir.  The caller
    runs this with ``python -I`` so only the paths set here count."""
    lib, tmp = argv[1], argv[2]
    sys.path.insert(0, lib)
    import sigmond
    from sigmond import uploader_manifest as um
    if not str(Path(sigmond.__file__).resolve()).startswith(
            str(Path(lib).resolve())):
        print(f"wrong sigmond imported: {sigmond.__file__}", file=sys.stderr)
        return 2
    out = {}
    for key, mode, kw in json.load(sys.stdin):
        kw = dict(kw)
        kw["senders"] = [tuple(s) for s in kw.get("senders", [])]
        out[key] = render(um, Path(tmp) / key, mode, **kw)
    json.dump(out, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
