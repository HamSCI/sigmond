"""A transporter declares the receivers whose rows it ships.

AC0G-B2 and KFS-MAIN run codar-sounder and hfdl-recorder for receivers on
other machines; each row carries its receiver's reporter id, and
wsprdaemon-server's sigmondb ingest accepts a reporter other than the
sender's own only when the tar declares it
(tasks/transporter-reporters/2026-11-18-design.md).  The render puts
``receivers`` on every wsprdaemon_tar transport for such a station, and
writes nothing for a single-receiver one, so its tars stay byte-identical.

Run with:  .venv/bin/pytest tests/test_uploader_manifest_receivers.py -v
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sigmond import uploader_manifest as um                     # noqa: E402
from sigmond.coordination import Coordination, Host, Station    # noqa: E402
from sigmond.receivers import Receiver                          # noqa: E402

A = "ac0g-b1-a-status.local"
B = "ac0g-b1-b-status.local"
B2 = [Receiver("AC0G/B1A", radiod=A), Receiver("AC0G/B1B", radiod=B)]
KFS = [Receiver("KFS/OMNI", radiod="kfs-omni-status.local"),
       Receiver("KFS/SW", radiod="kfs-sw-status.local"),
       Receiver("KFS/SE", radiod="kfs-se-status.local"),
       Receiver("KFS/NW", radiod="kfs-nw-status.local")]

DEPLOY = """
[[hs_uploader.pipeline]]
name = "wspr-wsprdaemon"
[hs_uploader.pipeline.source]
type = "wspr_cycle"
start_at = "now"
[hs_uploader.pipeline.transport]
type = "wsprdaemon_tar"
receiver = "{call_pathsafe}"
[hs_uploader.pipeline.transport.ftp_fallback]
servers = ["gw2.wsprdaemon.org"]

[[hs_uploader.pipeline]]
name = "codar-sigmondb-arrival"
[hs_uploader.pipeline.source]
type = "sqlite"
[hs_uploader.pipeline.transport]
type = "wsprdaemon_tar"
receiver = "{call_pathsafe}"
tar_tag = "chirp-arrival"

[[hs_uploader.pipeline]]
name = "psk-recorder-pskreporter"
[hs_uploader.pipeline.transport]
type = "pskreporter"
"""


class _Topo:
    def enabled_components(self):
        return ["wspr-recorder"]


def _coord(call):
    return Coordination(host=Host(call=call, grid="EM38ww"),
                        station=Station(reporter_id=call, uploader_call=call))


def _collect(monkeypatch, tmp_path, receivers, call):
    deploy = tmp_path / "deploy.toml"
    deploy.write_text(DEPLOY)
    monkeypatch.setattr(um, "find_deploy_toml", lambda c: deploy)
    return um.collect_pipelines(_Topo(), _coord(call), store_version=99,
                                receivers=receivers, features=frozenset(),
                                is_running=lambda c: False)


def _render(pipes):
    return um.render_manifest(pipes, {"call": "X"})


def test_b2_declares_both_receivers_on_every_tar(monkeypatch, tmp_path):
    out = _collect(monkeypatch, tmp_path, B2, "AC0G/B2")
    tars = [p for p in out if p["transport"]["type"] == "wsprdaemon_tar"]
    assert len(tars) == 2
    for p in tars:
        assert p["transport"]["receivers"] == ["AC0G/B1A", "AC0G/B1B"]
    psk = [p for p in out if p["name"] == "psk-recorder-pskreporter"]
    assert all("receivers" not in p["transport"] for p in psk)


def test_kfs_main_declares_its_four_receivers(monkeypatch, tmp_path):
    out = _collect(monkeypatch, tmp_path, KFS, "KFS-MAIN")
    assert out[0]["transport"]["receivers"] == [
        "KFS/NW", "KFS/OMNI", "KFS/SE", "KFS/SW"]


def test_the_declaration_survives_the_render(monkeypatch, tmp_path):
    out = _collect(monkeypatch, tmp_path, B2, "AC0G/B2")
    doc = tomllib.loads(_render(out))
    t = next(p for p in doc["pipeline"]
             if p["name"] == "codar-sigmondb-arrival")["transport"]
    assert t["receivers"] == ["AC0G/B1A", "AC0G/B1B"]


def test_a_single_receiver_station_renders_byte_identical(monkeypatch, tmp_path):
    one = [Receiver("AC0G/ND", radiod="ac0g-nd-status.local")]
    out = _collect(monkeypatch, tmp_path, one, "AC0G/ND")
    assert all("receivers" not in p["transport"] for p in out)
    bare = _collect(monkeypatch, tmp_path, [], "AC0G/ND")
    assert _render(out) == _render(bare)


@pytest.mark.parametrize("sender", ["AC0G/ND", "ac0g/nd", "AC0G=ND"])
def test_any_spelling_of_the_own_id_counts_as_own(sender):
    one = [Receiver("AC0G/ND", radiod="x-status.local")]
    assert um.declared_receivers(one, sender) == []


def test_one_receiver_that_is_not_the_sender_is_declared():
    assert um.declared_receivers([Receiver("AC0G/B1A", radiod=A)],
                                 "AC0G/B2") == ["AC0G/B1A"]


def test_no_receivers_declares_nothing():
    assert um.declared_receivers([], "AC0G/B2") == []


def test_the_key_matches_what_hs_uploader_READS():
    """⛔ CROSS-REPO: hs-uploader's pipeline_factory reads ``receivers`` off
    the wsprdaemon_tar transport (a8faec7).  A rename on either side passes
    silently, and the server then refuses every receiver's rows.  Skips
    without an hs-uploader checkout."""
    src = (Path(__file__).resolve().parents[2] / "hs-uploader" / "src" /
           "hs_uploader" / "pipeline_factory.py")
    if not src.exists():
        pytest.skip("no hs-uploader checkout to compare against")
    assert f'spec.get("{um.RECEIVERS_KEY}")' in src.read_text(), \
        f"hs-uploader does not read {um.RECEIVERS_KEY}"


# ---- the cycle gate: expected_reporters on the wspr_cycle source --------

def _wspr_source(out):
    return next(p for p in out if p["name"] == "wspr-wsprdaemon")["source"]


def test_b2_waits_for_both_receivers_before_a_cycle_ships(monkeypatch, tmp_path):
    """⛔ AC0G-B2, 2026-11-18: without the list, the cycle shipped on the
    first receiver's commit and the other's rows never left."""
    src = _wspr_source(_collect(monkeypatch, tmp_path, B2, "AC0G/B2"))
    assert src["expected_reporters"] == ["AC0G/B1A", "AC0G/B1B"]


def test_kfs_main_waits_for_its_four(monkeypatch, tmp_path):
    src = _wspr_source(_collect(monkeypatch, tmp_path, KFS, "KFS-MAIN"))
    assert src["expected_reporters"] == ["KFS/NW", "KFS/OMNI", "KFS/SE",
                                         "KFS/SW"]


def test_the_gate_survives_the_render(monkeypatch, tmp_path):
    doc = tomllib.loads(_render(_collect(monkeypatch, tmp_path, B2,
                                         "AC0G/B2")))
    src = next(p for p in doc["pipeline"]
               if p["name"] == "wspr-wsprdaemon")["source"]
    assert src["expected_reporters"] == ["AC0G/B1A", "AC0G/B1B"]


def test_one_receiver_has_nothing_to_race(monkeypatch, tmp_path):
    out = _collect(monkeypatch, tmp_path, [Receiver("AC0G/B1A", radiod=A)],
                   "AC0G/B2")
    assert "expected_reporters" not in _wspr_source(out)


def test_a_receiver_without_wspr_is_not_waited_for():
    """It never writes a noise row, so naming it would hold every cycle to
    the backstop."""
    rx = [Receiver("AC0G/B1A", radiod=A,
                   clients=("psk-recorder", "wspr-recorder")),
          Receiver("AC0G/B1B", radiod=B, clients=("psk-recorder",))]
    assert um.wspr_receivers(rx) == []
    rx.append(Receiver("AC0G/B1C", radiod="c-status.local"))
    assert um.wspr_receivers(rx) == ["AC0G/B1A", "AC0G/B1C"]


def test_a_receiver_that_does_not_upload_is_not_waited_for():
    rx = B2 + [Receiver("AC0G/B1C", radiod="c-status.local", upload=False)]
    assert um.wspr_receivers(rx) == ["AC0G/B1A", "AC0G/B1B"]


def test_a_source_with_its_own_list_keeps_it():
    pipes = [{"name": "w", "source": {"type": "wspr_cycle",
                                      "expected_reporters": ["X/Y"]}}]
    assert um.apply_cycle_gate(pipes, ["A/B", "C/D"]) == pipes


def test_the_gate_key_matches_what_hs_uploader_READS():
    """⛔ CROSS-REPO: hs-uploader's pipeline_factory reads
    ``expected_reporters`` off the wspr_cycle source.  Skips without an
    hs-uploader checkout."""
    src = (Path(__file__).resolve().parents[2] / "hs-uploader" / "src" /
           "hs_uploader" / "pipeline_factory.py")
    if not src.exists():
        pytest.skip("no hs-uploader checkout to compare against")
    assert f'spec.get("{um.EXPECTED_REPORTERS_KEY}")' in src.read_text()
