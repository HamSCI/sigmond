"""merge_into: a receiver reports under a group's call, merged on the server.

Each [[receiver]] may say ``merge_into = "KFS"`` and optionally
``merge_grid`` (default: the site grid).  The render writes the four
hs-uploader keys into the wspr-wsprdaemon transport; hs-uploader puts them
in routing.json in every tar, and the WD servers post under them
(spec 2026-10-30-multi-receiver-station-design.md §7c).

Run with:  .venv/bin/pytest tests/test_uploader_manifest_merge.py -v
"""
from __future__ import annotations

import sys
import tomllib
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from sigmond import inprocess_senders as ips                    # noqa: E402
from sigmond import uploader_manifest as um                     # noqa: E402
from sigmond.coordination import Coordination, Host, Station    # noqa: E402
from sigmond.receivers import (Receiver, ReceiverError,         # noqa: E402
                               load_receivers, set_merge,
                               write_receivers)

A = "kfs-a-status.local"
B = "kfs-b-status.local"
KEYS = ("wsprnet_reporter_call", "wsprnet_reporter_grid",
        "psk_reporter_call", "psk_reporter_grid")


def _rx(rid, radiod=A, **kw):
    return Receiver(reporter_id=rid, radiod=radiod, **kw)


def _pipes():
    return [
        {"name": "wspr-wsprdaemon",
         "transport": {"type": "wsprdaemon_tar", "version": "4.0"}},
        {"name": "psk-recorder-pskreporter",
         "transport": {"type": "pskreporter"}},
    ]


def _coord(grid="CN87", **station):
    return Coordination(host=Host(call="KFS/SE", grid=grid),
                        station=Station(reporter_id="KFS/SE", **station))


class _Topo:
    def __init__(self, enabled):
        self._enabled = list(enabled)

    def enabled_components(self):
        return list(self._enabled)


@pytest.fixture
def units(monkeypatch):
    """Fake each client's instance units and their effective env."""
    state = {"units": {}, "env": {}}

    def _instance_units(client, topology):
        return list(state["units"].get(client, []))

    def _show(unit):
        if unit not in state["env"]:
            raise OSError(f"no unit {unit}")
        return dict(state["env"][unit])

    monkeypatch.setattr(ips, "_instance_units", _instance_units)
    monkeypatch.setattr(ips, "_systemctl_show", _show)
    return state


# ---- merge_target ----------------------------------------------------------

def test_no_merge_into_means_no_target():
    assert um.merge_target([_rx("KFS/SE"), _rx("KFS/NW", B)], "CN87") is None


def test_one_target_with_the_site_grid_by_default():
    rx = [_rx("KFS/SE", merge_into="KFS"), _rx("KFS/NW", B, merge_into="KFS")]
    assert um.merge_target(rx, "CN87") == ("KFS", "CN87")


def test_merge_grid_wins_over_the_site_grid():
    rx = [_rx("KFS/SE", merge_into="KFS", merge_grid="CN86")]
    assert um.merge_target(rx, "CN87") == ("KFS", "CN86")


def test_the_call_is_upper_cased():
    assert um.merge_target([_rx("KFS/SE", merge_into="kfs ")],
                           "CN87") == ("KFS", "CN87")


def test_two_targets_on_one_host_is_an_error():
    rx = [_rx("KFS/SE", merge_into="KFS"), _rx("KFS/NW", B, merge_into="KFT")]
    with pytest.raises(um.MergeError, match="one merge target per host"):
        um.merge_target(rx, "CN87")


def test_two_grids_for_one_call_is_an_error():
    rx = [_rx("KFS/SE", merge_into="KFS"),
          _rx("KFS/NW", B, merge_into="KFS", merge_grid="CN86")]
    with pytest.raises(um.MergeError, match="one merge target per host"):
        um.merge_target(rx, "CN87")


def test_merged_beside_unmerged_is_an_error():
    rx = [_rx("KFS/SE", merge_into="KFS"), _rx("KFS/NW", B)]
    with pytest.raises(um.MergeError, match="KFS/NW"):
        um.merge_target(rx, "CN87")


def test_a_receiver_that_does_not_upload_is_not_counted():
    rx = [_rx("KFS/SE", merge_into="KFS"),
          _rx("KFS/S2", same_antenna_as="KFS/SE")]
    assert um.merge_target(rx, "CN87") == ("KFS", "CN87")


def test_no_grid_anywhere_is_an_error():
    with pytest.raises(um.MergeError, match="grid"):
        um.merge_target([_rx("KFS/SE", merge_into="KFS")], "")


def test_merge_grid_without_merge_into_is_a_problem():
    msgs = um.merge_problems([_rx("KFS/SE", merge_grid="CN86")], "CN87")
    assert msgs and "merge_grid" in msgs[0]


# ---- apply_merge -----------------------------------------------------------

def test_no_target_changes_NOTHING():
    """⛔ Absent stays absent: a receiver with no merge_into reports as
    itself, and every unmerged station in the fleet renders this."""
    assert um.apply_merge(_pipes(), None) == _pipes()


def test_a_target_writes_all_four_keys_on_the_tar():
    out = um.apply_merge(_pipes(), ("KFS", "CN87"))
    t = out[0]["transport"]
    assert {k: t[k] for k in KEYS} == {
        "wsprnet_reporter_call": "KFS", "wsprnet_reporter_grid": "CN87",
        "psk_reporter_call": "KFS", "psk_reporter_grid": "CN87"}
    assert out[1] == _pipes()[1]
    assert _pipes()[0]["transport"].keys().isdisjoint(KEYS)  # no mutation


def test_the_keys_render_as_the_live_kfs_manifest_has_them():
    out = um.apply_merge(_pipes(), ("KFS", "CN87"))
    text = um.render_manifest(out, {"call": "KFS/SE", "grid": "CN87"})
    doc = tomllib.loads(text)
    tar = next(p for p in doc["pipeline"] if p["name"] == "wspr-wsprdaemon")
    assert tar["transport"]["wsprnet_reporter_call"] == "KFS"
    assert tar["transport"]["psk_reporter_grid"] == "CN87"


def test_a_missing_tar_pipeline_changes_nothing():
    pipes = [p for p in _pipes() if p["name"] != "wspr-wsprdaemon"]
    assert um.apply_merge(pipes, ("KFS", "CN87")) == pipes


def test_the_keys_are_the_ones_hs_uploader_reads():
    """A rename on either side is silent; read hs-uploader's own source."""
    src = (Path(__file__).resolve().parents[2] / "hs-uploader" / "src"
           / "hs_uploader" / "pipeline_factory.py")
    if not src.is_file():
        pytest.skip("no ../hs-uploader checkout beside this one")
    text = src.read_text()
    for k in KEYS:
        assert f'"{k}"' in text, k


# ---- two merges ------------------------------------------------------------

def test_station_merge_plus_merge_into_is_a_problem(units):
    units["units"]["wspr-recorder"] = ["wspr-recorder@KFS.service"]
    units["env"]["wspr-recorder@KFS.service"] = {
        "WD_MERGE_REPORTERS": "KFS/SE,KFS/NW"}
    msgs = um.two_merges_problems([_rx("KFS/SE", merge_into="KFS")],
                                  _Topo(["wspr-recorder"]))
    assert msgs and "WD_MERGE_REPORTERS" in msgs[0]


def test_station_merge_alone_is_fine(units):
    units["units"]["wspr-recorder"] = ["wspr-recorder@KFS.service"]
    units["env"]["wspr-recorder@KFS.service"] = {"WD_MERGE_REPORTERS": "A,B"}
    assert um.two_merges_problems([_rx("KFS/SE")],
                                  _Topo(["wspr-recorder"])) == []


def test_an_empty_station_merge_is_fine(units):
    units["units"]["wspr-recorder"] = ["wspr-recorder@KFS-SE.service"]
    units["env"]["wspr-recorder@KFS-SE.service"] = {
        "WD_MERGE_REPORTERS": " "}
    assert um.two_merges_problems([_rx("KFS/SE", merge_into="KFS")],
                                  _Topo(["wspr-recorder"])) == []


# ---- collect_pipelines wiring ----------------------------------------------

def _collect(monkeypatch, tmp_path, receivers, units_state=None):
    deploy = tmp_path / "deploy.toml"
    deploy.write_text(
        '[[hs_uploader.pipeline]]\nname = "wspr-wsprdaemon"\n'
        '[hs_uploader.pipeline.transport]\ntype = "wsprdaemon_tar"\n')
    monkeypatch.setattr(um, "find_deploy_toml", lambda c: deploy)
    return um.collect_pipelines(_Topo(["wspr-recorder"]), _coord(),
                                store_version=99, receivers=receivers)


def test_collect_pipelines_carries_the_merge(monkeypatch, tmp_path, units):
    rx = [_rx("KFS/SE", merge_into="KFS"), _rx("KFS/NW", B, merge_into="KFS")]
    out = _collect(monkeypatch, tmp_path, rx)
    t = out[0]["transport"]
    assert t["wsprnet_reporter_call"] == "KFS"
    assert t["psk_reporter_grid"] == "CN87"


def test_a_merged_proxied_station_renders_as_before(monkeypatch, tmp_path,
                                                    units):
    deploy = tmp_path / "deploy.toml"
    deploy.write_text(
        '[[hs_uploader.pipeline]]\nname = "wspr-wsprdaemon"\n'
        '[hs_uploader.pipeline.transport]\ntype = "wsprdaemon_tar"\n'
        '[[hs_uploader.pipeline]]\nname = "wspr-wsprnet"\n'
        '[hs_uploader.pipeline.transport]\ntype = "wsprnet"\n')
    monkeypatch.setattr(um, "find_deploy_toml", lambda c: deploy)
    rx = [_rx("KFS/SE", merge_into="KFS"), _rx("KFS/NW", B, merge_into="KFS")]
    out = um.collect_pipelines(_Topo(["wspr-recorder"]), _coord(),
                               store_version=99, receivers=rx)
    assert [p["name"] for p in out] == ["wspr-wsprdaemon"]
    assert out[0]["transport"] == {
        "type": "wsprdaemon_tar", "forward_to_wsprnet": True,
        "wsprnet_reporter_call": "KFS", "wsprnet_reporter_grid": "CN87",
        "psk_reporter_call": "KFS", "psk_reporter_grid": "CN87"}


def test_collect_pipelines_unmerged_emits_no_key(monkeypatch, tmp_path):
    out = _collect(monkeypatch, tmp_path, [_rx("KFS/SE")])
    assert out[0]["transport"].keys().isdisjoint(KEYS)


def test_collect_pipelines_refuses_two_targets(monkeypatch, tmp_path, units):
    rx = [_rx("KFS/SE", merge_into="KFS"), _rx("KFS/NW", B, merge_into="KFT")]
    with pytest.raises(um.MergeError):
        _collect(monkeypatch, tmp_path, rx)


def test_collect_pipelines_refuses_two_merges(monkeypatch, tmp_path, units):
    units["units"]["wspr-recorder"] = ["wspr-recorder@KFS.service"]
    units["env"]["wspr-recorder@KFS.service"] = {"WD_MERGE_REPORTERS": "A,B"}
    with pytest.raises(um.MergeError, match="WD_MERGE_REPORTERS"):
        _collect(monkeypatch, tmp_path, [_rx("KFS/SE", merge_into="KFS")])


def test_collect_pipelines_reads_the_site_profile(monkeypatch, tmp_path,
                                                  units):
    from sigmond import site_profile as sp
    prof = tmp_path / "site-profile.toml"
    write_receivers(prof, [_rx("KFS/SE", merge_into="KFS")])
    monkeypatch.setattr(sp, "SITE_PROFILE_PATH", prof)
    deploy = tmp_path / "deploy.toml"
    deploy.write_text(
        '[[hs_uploader.pipeline]]\nname = "wspr-wsprdaemon"\n'
        '[hs_uploader.pipeline.transport]\ntype = "wsprdaemon_tar"\n')
    monkeypatch.setattr(um, "find_deploy_toml", lambda c: deploy)
    out = um.collect_pipelines(_Topo(["wspr-recorder"]), _coord(),
                               store_version=99)
    assert out[0]["transport"]["psk_reporter_call"] == "KFS"


# ---- delivery env ----------------------------------------------------------

def test_delivery_mismatch_names_psk_and_meteor(units):
    psk = "psk-recorder@KFS-SE.service"
    ms = "meteor-scatter@KFS-SE.service"
    units["units"] = {"psk-recorder": [psk], "meteor-scatter": [ms]}
    units["env"] = {psk: {"PSK_DELIVERY_PIPELINES": "direct"},
                    ms: {"METEOR_SCATTER_DELIVERY_MODE": "direct"}}
    msgs = um.delivery_mismatches(
        [_rx("KFS/SE", merge_into="KFS")],
        _Topo(["psk-recorder", "meteor-scatter"]))
    assert any("PSK_DELIVERY_PIPELINES=server-merge" in m for m in msgs)
    assert any("METEOR_SCATTER_DELIVERY_MODE=deposit" in m for m in msgs)


def test_delivery_that_agrees_is_quiet(units):
    psk = "psk-recorder@KFS-SE.service"
    ms = "meteor-scatter@KFS-SE.service"
    units["units"] = {"psk-recorder": [psk], "meteor-scatter": [ms]}
    units["env"] = {psk: {"PSK_DELIVERY_PIPELINES": "server-merge"},
                    ms: {"METEOR_SCATTER_DELIVERY_MODE": "deposit"}}
    assert um.delivery_mismatches(
        [_rx("KFS/SE", merge_into="KFS")],
        _Topo(["psk-recorder", "meteor-scatter"])) == []


def test_delivery_of_an_unmerged_receiver_is_not_checked(units):
    psk = "psk-recorder@KFS-SE.service"
    units["units"] = {"psk-recorder": [psk]}
    units["env"] = {psk: {"PSK_DELIVERY_PIPELINES": "direct"}}
    assert um.delivery_mismatches([_rx("KFS/SE")],
                                  _Topo(["psk-recorder"])) == []


# ---- set_merge (receivers.py) ----------------------------------------------

def test_set_merge_sets_and_clears():
    cur = [_rx("KFS/SE", antenna="omni"), _rx("KFS/NW", B)]
    out = set_merge(cur, ["kfs/se"], into="kfs", grid="CN87ab")
    assert out[0] == _rx("KFS/SE", antenna="omni", merge_into="KFS",
                         merge_grid="CN87ab")
    assert out[1] == cur[1]
    back = set_merge(out, ["KFS/SE"], into="", grid="")
    assert back == cur


def test_set_merge_without_grid_drops_an_old_grid():
    cur = [_rx("KFS/SE", merge_into="KFS", merge_grid="CN86")]
    out = set_merge(cur, ["KFS/SE"], into="KFS", grid="")
    assert out[0].merge_grid == ""


def test_set_merge_refuses_an_unknown_receiver():
    with pytest.raises(ReceiverError, match="no receiver"):
        set_merge([_rx("KFS/SE")], ["KFS/XX"], into="KFS", grid="")


@pytest.mark.parametrize("into,grid", [("K FS", ""), ("KFS", "CN8"),
                                       ("KFS", "ZZ99")])
def test_set_merge_refuses_a_bad_call_or_grid(into, grid):
    with pytest.raises(ReceiverError):
        set_merge([_rx("KFS/SE")], ["KFS/SE"], into=into, grid=grid)


def test_set_merge_round_trips_through_the_profile(tmp_path):
    prof = tmp_path / "site-profile.toml"
    prof.write_text('[station]\ncallsign = "KFS"\n')
    write_receivers(prof, [_rx("KFS/SE")])
    write_receivers(prof, set_merge(load_receivers(prof), ["KFS/SE"],
                                    into="KFS", grid=""))
    assert load_receivers(prof)[0].merge_into == "KFS"
    assert prof.read_text().startswith('[station]\ncallsign = "KFS"\n')


# ---- merge_report (smd admin merge show) -----------------------------------

def test_merge_report_shows_rendered_and_installed(tmp_path):
    man = tmp_path / "pipelines.toml"
    man.write_text(um.render_manifest(
        um.apply_merge(_pipes(), ("KFS", "CN87")), {"call": "KFS/SE"}))
    rep = um.merge_report([_rx("KFS/SE", merge_into="KFS"),
                           _rx("KFS/S2", same_antenna_as="KFS/SE")],
                          "CN87", manifest_path=man)
    assert rep["rendered"] == rep["installed"] == {
        "wsprnet_reporter_call": "KFS", "wsprnet_reporter_grid": "CN87",
        "psk_reporter_call": "KFS", "psk_reporter_grid": "CN87"}
    assert rep["refused"] == ""
    assert rep["receivers"][0]["grid"] == "CN87"
    assert rep["receivers"][1]["merge_into"] == ""


def test_merge_report_names_a_refusal_and_a_missing_manifest(tmp_path):
    rep = um.merge_report([_rx("KFS/SE", merge_into="KFS"), _rx("KFS/NW", B)],
                          "CN87", manifest_path=tmp_path / "absent.toml")
    assert "KFS/NW" in rep["refused"]
    assert rep["rendered"] == {} and rep["installed"] == {}


# ---- smd admin merge show / set / clear ------------------------------------

def _load_smd():
    import importlib.util
    import os
    from unittest import mock
    spec = importlib.util.spec_from_loader("smd_mod_merge", loader=None)
    smd = importlib.util.module_from_spec(spec)
    smd.__dict__["__file__"] = "bin/smd"
    src = Path(__file__).resolve().parent.parent / "bin" / "smd"
    with mock.patch.dict(os.environ, {"SIGMOND_NO_VENV_REEXEC": "1"}):
        exec(compile(src.read_text(), str(src), "exec"), smd.__dict__)
    return smd


def test_merge_set_and_clear_edit_only_the_receiver(tmp_path, capsys):
    smd = _load_smd()
    prof = tmp_path / "site-profile.toml"
    write_receivers(prof, [_rx("KFS/SE", antenna="omni"), _rx("KFS/NW", B)])
    ns = types.SimpleNamespace
    assert smd.cmd_merge_set(ns(profile_path=prof, reporters=["KFS/SE"],
                                into="kfs", grid="")) == 0
    rx = load_receivers(prof)
    assert rx[0] == _rx("KFS/SE", antenna="omni", merge_into="KFS")
    assert rx[1] == _rx("KFS/NW", B)
    # merged beside unmerged: written, and said
    assert "render refuses" in "".join(capsys.readouterr())
    assert smd.cmd_merge_set(ns(profile_path=prof, reporters=["KFS/SE"]),
                             clear=True) == 0
    assert load_receivers(prof)[0] == _rx("KFS/SE", antenna="omni")


def test_merge_set_refuses_an_unknown_receiver(tmp_path):
    smd = _load_smd()
    prof = tmp_path / "site-profile.toml"
    write_receivers(prof, [_rx("KFS/SE")])
    before = prof.read_bytes()
    assert smd.cmd_merge_set(types.SimpleNamespace(
        profile_path=prof, reporters=["KFS/XX"], into="KFS", grid="")) == 1
    assert prof.read_bytes() == before


def test_merge_show_json(tmp_path, units, capsys):
    smd = _load_smd()
    prof = tmp_path / "site-profile.toml"
    write_receivers(prof, [_rx("KFS/SE", merge_into="KFS", merge_grid="CN87")])
    rc = smd.cmd_merge_show(types.SimpleNamespace(
        profile_path=prof, json=True, topology=_Topo([]),
        manifest_path=tmp_path / "absent.toml"))
    import json
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert doc["rendered"]["psk_reporter_call"] == "KFS"
    assert doc["installed"] == {}
    assert doc["receivers"][0]["merge_into"] == "KFS"


def test_merge_show_text_says_the_render_refuses(tmp_path, units, capsys):
    smd = _load_smd()
    prof = tmp_path / "site-profile.toml"
    write_receivers(prof, [_rx("KFS/SE", merge_into="KFS"), _rx("KFS/NW", B)])
    rc = smd.cmd_merge_show(types.SimpleNamespace(
        profile_path=prof, json=False, topology=_Topo([]),
        manifest_path=tmp_path / "absent.toml"))
    assert rc == 1
    assert "render refuses" in "".join(capsys.readouterr())


def test_merge_is_an_admin_command():
    smd = _load_smd()
    a = smd._build_parser().parse_args(["admin", "merge", "set", "KFS/SE", "--into", "KFS",
                      "--grid", "CN87"])
    assert (a.merge_command, a.reporters, a.into, a.grid) == (
        "set", ["KFS/SE"], "KFS", "CN87")


# ---- smd config render -----------------------------------------------------

def _render(tmp_path, monkeypatch, receivers):
    import sigmond.commands.config as cfg
    from sigmond import site_profile as sp
    from sigmond import topology as topo
    prof = tmp_path / "site-profile.toml"
    prof.write_text('[station]\ncallsign = "KFS"\ngrid_square = "CN87"\n')
    write_receivers(prof, receivers)
    env = tmp_path / "coordination.env"
    for mod in (sp, cfg):
        monkeypatch.setattr(mod, "SITE_PROFILE_PATH", prof)
    monkeypatch.setattr(cfg, "COORDINATION_PATH",
                        tmp_path / "coordination.toml")
    monkeypatch.setattr(cfg, "COORDINATION_ENV", env)
    monkeypatch.setattr(topo, "load_topology", lambda *a, **k: _Topo(
        ["wspr-recorder", "psk-recorder", "meteor-scatter"]))
    return cfg.cmd_config_render(types.SimpleNamespace()), env


def test_render_refuses_two_targets(tmp_path, monkeypatch, units, capsys):
    rc, env = _render(tmp_path, monkeypatch, [
        _rx("KFS/SE", merge_into="KFS"), _rx("KFS/NW", B, merge_into="KFT")])
    assert rc == 1
    assert "one merge target per host" in "".join(capsys.readouterr())
    assert not env.exists()


def test_render_refuses_two_merges(tmp_path, monkeypatch, units, capsys):
    units["units"]["wspr-recorder"] = ["wspr-recorder@KFS.service"]
    units["env"]["wspr-recorder@KFS.service"] = {"WD_MERGE_REPORTERS": "A,B"}
    rc, env = _render(tmp_path, monkeypatch,
                      [_rx("KFS/SE", merge_into="KFS")])
    assert rc == 1
    assert "WD_MERGE_REPORTERS" in "".join(capsys.readouterr())
    assert not env.exists()


def test_render_warns_on_delivery_and_goes_on(tmp_path, monkeypatch, units,
                                              capsys):
    psk = "psk-recorder@KFS-SE.service"
    units["units"]["psk-recorder"] = [psk]
    units["env"][psk] = {"PSK_DELIVERY_PIPELINES": "direct"}
    rc, env = _render(tmp_path, monkeypatch,
                      [_rx("KFS/SE", merge_into="KFS")])
    out = "".join(capsys.readouterr())
    assert rc == 0 and env.exists()
    assert "PSK_DELIVERY_PIPELINES=server-merge" in out
    assert "KFS/CN87" in out
