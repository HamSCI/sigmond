"""The wsprdaemon transport's `receiver` key is the SENDER: the machine that
ships the tars.  Sources are named by reporter id, row by row.  Michael,
2026-11-12: rename it on sigmond's side.  An hs-uploader without the
`sender-key` feature would ignore `sender` and lose the name, so the rename
waits for the feature."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond.uploader_manifest import SENDER_FEATURE, apply_sender_key  # noqa: E402

PIPES = [{"name": "wspr-wsprdaemon",
          "transport": {"type": "wsprdaemon_tar", "receiver": "KFS-MAIN",
                        "servers": ["gw1"]}},
         {"name": "psk-recorder-pskreporter",
          "transport": {"type": "pskreporter"}}]


def test_renamed_when_hs_uploader_reads_sender():
    out = apply_sender_key(PIPES, frozenset({"gate", SENDER_FEATURE}))
    t = out[0]["transport"]
    assert t["sender"] == "KFS-MAIN" and "receiver" not in t
    assert list(t) == ["type", "sender", "servers"]      # key order kept
    assert out[1] is PIPES[1]


def test_kept_for_an_older_hs_uploader():
    assert apply_sender_key(PIPES, frozenset({"gate"})) is PIPES
    assert apply_sender_key(PIPES, None) is PIPES


def test_the_caller_s_pipelines_are_not_changed():
    apply_sender_key(PIPES, frozenset({SENDER_FEATURE}))
    assert PIPES[0]["transport"]["receiver"] == "KFS-MAIN"
