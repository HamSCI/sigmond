"""The migration onto the wsprdaemon.org route (uniform-routing plan, Task 3).

Each test builds env files under a scratch ``etc``; ``show`` reads a unit's
env file into a dict (or raises OSError for a unit in ``rig.broken``), and
``restart`` records what it was asked to restart.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

from sigmond import sink_levers
from sigmond import uniform_routing as R
from sigmond.sink_state import ClientState, SinkState, SiteState
from sigmond.upload import env_path_for


class Topo:
    def __init__(self, enabled):
        self.enabled = list(enabled)

    def enabled_components(self, only=None):
        return list(self.enabled)


class Rig:
    def __init__(self, tmp_path):
        self.etc = tmp_path / "etc"
        self.units = {}            # client -> [unit, ...]
        self.broken = set()
        self.overrides = {}        # unit -> dict that show() returns instead
        self.restarts = []
        self.restart_error = None
        self.states = {}           # unit -> (ActiveState, started) or OSError

    def path(self, client, inst):
        return env_path_for(client, inst, base=str(self.etc))

    def env(self, client, inst, **kv):
        p = self.path(client, inst)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(f"{k}={v}\n" for k, v in kv.items()))
        self.units.setdefault(client, []).append(f"{client}@{inst}.service")
        return p

    def text(self, client, inst):
        return self.path(client, inst).read_text()

    def instance_units(self, client, topology):
        return list(self.units.get(client, []))

    def show(self, unit):
        if unit in self.broken:
            raise OSError(f"systemctl show {unit} exited 1")
        if unit in self.overrides:
            return dict(self.overrides[unit])
        client, _, inst = unit.partition("@")
        inst = inst[:-len(".service")]
        env = {}
        for ln in self.path(client, inst).read_text().splitlines():
            k, sep, v = ln.partition("=")
            if sep:
                env[k.strip()] = v.strip()
        return env

    def restart(self, units):
        self.restarts.append(list(units))
        if self.restart_error is not None:
            raise self.restart_error

    def unit_state(self, unit):
        st = self.states.get(unit, ("active", None))
        if isinstance(st, Exception):
            raise st
        return st

    def run(self, enabled, *, lowered, apply=True, undecided_why="",
            heal=False):
        return R.migrate_routing(
            Topo(enabled), lowered=lowered, apply=apply, etc=self.etc,
            instance_units=self.instance_units, show=self.show,
            restart=self.restart, undecided_why=undecided_why,
            unit_state=self.unit_state if heal else None)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


ALL = ["wspr-recorder", "psk-recorder", "meteor-scatter"]


def test_direct_psk_moves_to_server_merge_and_restarts(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="direct")
    res = rig.run(["wspr-recorder", "psk-recorder"], lowered=frozenset())
    assert "PSK_DELIVERY_PIPELINES=server-merge" in rig.text("psk-recorder", "AC0G-S")
    assert "PSK_USE_HS_UPLOADER=1" in rig.text("psk-recorder", "AC0G-S")
    assert res.restarted == ["psk-recorder@AC0G-S.service"]
    assert rig.restarts == [["psk-recorder@AC0G-S.service"]]
    assert res.ok
    assert res.lines() == [
        "routing: psk-recorder@AC0G-S.service: PSK_DELIVERY_PIPELINES direct -> server-merge",
        "routing: restarted psk-recorder@AC0G-S.service",
    ]


def test_legacy_both_and_server_raw_at_upload_move(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_MODE="both")
    rig.env("psk-recorder", "AC0G-T", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="server-raw")
    res = rig.run(["wspr-recorder", "psk-recorder"], lowered=frozenset())
    for inst in ("AC0G-S", "AC0G-T"):
        assert R.via_server("psk-recorder", rig.show(f"psk-recorder@{inst}.service"))
        assert "PSK_DELIVERY_PIPELINES=server-merge" in rig.text("psk-recorder", inst)
    assert res.changed == [
        ("psk-recorder@AC0G-S.service", "PSK_DELIVERY_PIPELINES", "unset", "server-merge"),
        ("psk-recorder@AC0G-T.service", "PSK_DELIVERY_PIPELINES", "server-raw", "server-merge"),
    ]
    assert res.restarted == ["psk-recorder@AC0G-S.service",
                             "psk-recorder@AC0G-T.service"]
    assert len(rig.restarts) == 1


def test_meteor_unset_and_off_at_upload_move_to_deposit(rig):
    rig.env("meteor-scatter", "AC0G-S", METEOR_SCATTER_USE_HS_UPLOADER="1")
    rig.env("meteor-scatter", "AC0G-T", METEOR_SCATTER_USE_HS_UPLOADER="1",
            METEOR_SCATTER_DELIVERY_MODE="off")
    res = rig.run(["wspr-recorder", "meteor-scatter"], lowered=frozenset())
    assert res.changed == [
        ("meteor-scatter@AC0G-S.service", "METEOR_SCATTER_DELIVERY_MODE", "unset", "deposit"),
        ("meteor-scatter@AC0G-T.service", "METEOR_SCATTER_DELIVERY_MODE", "off", "deposit"),
    ]
    for inst in ("AC0G-S", "AC0G-T"):
        assert "METEOR_SCATTER_DELIVERY_MODE=deposit" in rig.text("meteor-scatter", inst)
    assert "routing: meteor-scatter@AC0G-S.service: METEOR_SCATTER_DELIVERY_MODE unset -> deposit" in res.lines()
    assert res.ok


def test_a_lowered_client_is_left_alone_and_reported(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="server-raw")
    rig.env("meteor-scatter", "AC0G-S", METEOR_SCATTER_USE_HS_UPLOADER="1",
            METEOR_SCATTER_DELIVERY_MODE="off")
    before = rig.text("psk-recorder", "AC0G-S")
    res = rig.run(ALL, lowered=frozenset({"psk-recorder", "meteor-scatter"}))
    assert rig.text("psk-recorder", "AC0G-S") == before
    assert res.restarted == [] and rig.restarts == []
    assert res.lowered == [
        ("psk-recorder@AC0G-S.service", "PSK_DELIVERY_PIPELINES=server-raw"),
        ("meteor-scatter@AC0G-S.service", "METEOR_SCATTER_DELIVERY_MODE=off"),
    ]
    assert ("routing: left alone psk-recorder@AC0G-S.service "
            "(PSK_DELIVERY_PIPELINES=server-raw: its sink reads below upload; "
            "`smd sink upload` routes it)") in res.lines()
    assert res.ok


def test_unknown_sink_state_changes_nothing(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="direct")
    before = rig.text("psk-recorder", "AC0G-S")
    res = rig.run(ALL, lowered=None, undecided_why="hs-uploader timed out")
    assert res.lines() == ["routing: cannot read the sink switches "
                           "(hs-uploader timed out); no instance changed"]
    assert not res.ok and rig.restarts == []
    assert rig.text("psk-recorder", "AC0G-S") == before


def test_unknown_sink_state_without_a_reason(rig):
    res = rig.run(ALL, lowered=None)
    assert res.undecided == "no reason given"
    assert not res.ok


def test_no_carrier_leaves_every_instance_and_says_error(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="direct")
    rig.env("meteor-scatter", "AC0G-S", METEOR_SCATTER_USE_HS_UPLOADER="1",
            METEOR_SCATTER_DELIVERY_MODE="direct")
    before = (rig.text("psk-recorder", "AC0G-S"),
              rig.text("meteor-scatter", "AC0G-S"))
    res = rig.run(["psk-recorder", "meteor-scatter"], lowered=frozenset())
    assert (rig.text("psk-recorder", "AC0G-S"),
            rig.text("meteor-scatter", "AC0G-S")) == before
    assert rig.restarts == [] and res.changed == []
    errors = [l for l in res.lines() if l.startswith("routing: ERROR")]
    assert errors == [
        f"routing: ERROR {u} posts from this station: no wspr-recorder is "
        "enabled to carry its spots to wsprdaemon.org; left as it stands "
        "(smd enable wspr-recorder)"
        for u in ("psk-recorder@AC0G-S.service", "meteor-scatter@AC0G-S.service")]
    assert res.ok is False


def test_psk_instance_with_flag_at_zero_is_routed(rig):
    # psk-recorder reads PSK_USE_HS_UPLOADER nowhere: at 0 it still posts directly.
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="0",
            PSK_DELIVERY_PIPELINES="direct")
    res = rig.run(ALL, lowered=frozenset())
    assert "PSK_DELIVERY_PIPELINES=server-merge" in rig.text(
        "psk-recorder", "AC0G-S")
    assert res.upload_off == []
    assert [c[0] for c in res.changed] == ["psk-recorder@AC0G-S.service"]


def test_psk_instance_with_flag_at_zero_stays_when_lowered(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="0",
            PSK_DELIVERY_PIPELINES="direct")
    before = rig.text("psk-recorder", "AC0G-S")
    res = rig.run(ALL, lowered=frozenset({"psk-recorder"}))
    assert rig.text("psk-recorder", "AC0G-S") == before
    assert rig.restarts == [] and res.changed == []
    assert [u for u, _ in res.lowered] == ["psk-recorder@AC0G-S.service"]


def test_unreadable_env_is_named_and_untouched(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="direct")
    rig.env("psk-recorder", "AC0G-T", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="direct")
    rig.broken.add("psk-recorder@AC0G-T.service")
    before = rig.text("psk-recorder", "AC0G-T")
    res = rig.run(ALL, lowered=frozenset())
    assert rig.text("psk-recorder", "AC0G-T") == before
    assert "PSK_DELIVERY_PIPELINES=server-merge" in rig.text("psk-recorder", "AC0G-S")
    assert res.restarted == ["psk-recorder@AC0G-S.service"]
    assert rig.restarts == [["psk-recorder@AC0G-S.service"]]
    assert ("routing: cannot read psk-recorder@AC0G-T.service's environment "
            "(systemctl show psk-recorder@AC0G-T.service exited 1); "
            "left as it stands") in res.lines()
    assert not res.ok


def test_enumeration_failure_is_unreadable(rig):
    def boom(client, topology):
        raise OSError("cannot enumerate psk-recorder units: nope")
    res = R.migrate_routing(Topo(ALL), lowered=frozenset(), etc=rig.etc,
                            instance_units=boom, show=rig.show,
                            restart=rig.restart)
    assert ("psk-recorder", "cannot enumerate its instances: cannot "
            "enumerate psk-recorder units: nope") in res.unreadable
    assert not res.ok


def test_an_override_that_still_posts_is_a_failure_not_a_restart(rig):
    p = rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
                PSK_DELIVERY_PIPELINES="direct")
    rig.overrides["psk-recorder@AC0G-S.service"] = {
        "PSK_USE_HS_UPLOADER": "1", "PSK_DELIVERY_PIPELINES": "direct"}
    res = rig.run(ALL, lowered=frozenset())
    assert rig.restarts == [] and res.restarted == []
    assert res.changed == []
    assert res.failures == [(str(p),
        "written, but psk-recorder@AC0G-S.service's environment still "
        "overrides PSK_DELIVERY_PIPELINES; not restarted")]
    assert f"routing: FAILED {p}: written, but psk-recorder@AC0G-S.service's " \
           "environment still overrides PSK_DELIVERY_PIPELINES; not restarted" \
           in res.lines()
    assert not res.ok


def test_merged_instances_already_routed_change_nothing(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="server-merge")
    rig.env("meteor-scatter", "AC0G-S", METEOR_SCATTER_USE_HS_UPLOADER="1",
            METEOR_SCATTER_DELIVERY_MODE="deposit")
    mtimes = [rig.path(c, "AC0G-S").stat().st_mtime_ns
              for c in ("psk-recorder", "meteor-scatter")]
    res = rig.run(ALL, lowered=frozenset())
    assert [rig.path(c, "AC0G-S").stat().st_mtime_ns
            for c in ("psk-recorder", "meteor-scatter")] == mtimes
    assert rig.restarts == []
    assert res.lines() == ["routing: 2 instance(s) already deliver via wsprdaemon.org"]
    assert res.ok


def test_a_second_run_changes_nothing(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="direct")
    first = rig.run(ALL, lowered=frozenset())
    assert first.restarted == ["psk-recorder@AC0G-S.service"]
    second = rig.run(ALL, lowered=frozenset())
    assert second.changed == [] and second.restarted == []
    assert len(rig.restarts) == 1
    assert second.lines() == [
        "routing: 1 instance(s) already deliver via wsprdaemon.org"]


def test_dry_run_writes_and_restarts_nothing(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="direct")
    before = rig.text("psk-recorder", "AC0G-S")
    res = rig.run(ALL, lowered=frozenset(), apply=False)
    assert rig.text("psk-recorder", "AC0G-S") == before
    assert rig.restarts == [] and res.restarted == []
    assert res.lines() == [
        "routing: would set psk-recorder@AC0G-S.service: "
        "PSK_DELIVERY_PIPELINES direct -> server-merge"]
    assert res.ok


def test_radiod_moved_is_a_failure(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="direct")
    rig.restart_error = sink_levers.RadiodMoved("radiod moved", failures=[])
    res = rig.run(ALL, lowered=frozenset())
    assert not res.ok
    assert any("radiod moved" in why for _, why in res.failures)


def test_restart_failed_names_the_unit(rig):
    rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="direct")
    rig.env("psk-recorder", "AC0G-T", PSK_USE_HS_UPLOADER="1",
            PSK_DELIVERY_PIPELINES="direct")
    rig.restart_error = sink_levers.RestartFailed(
        [("psk-recorder@AC0G-T.service", "systemctl restart exited 1")])
    res = rig.run(ALL, lowered=frozenset())
    assert res.restarted == ["psk-recorder@AC0G-S.service"]
    assert ("psk-recorder@AC0G-T.service", "systemctl restart exited 1") \
        in res.failures
    assert ("routing: FAILED psk-recorder@AC0G-T.service: "
            "systemctl restart exited 1") in res.lines()


def test_nothing_on_this_host(rig):
    res = rig.run(["wspr-recorder"], lowered=frozenset())
    assert res.lines() == [
        "routing: no psk-recorder or meteor-scatter instance on this host"]
    assert res.ok


def _site(state):
    return SiteState(state=state, error="", set_at="", set_by="", reason="")


def _client(eff):
    return ClientState(file_state=eff, effective=eff, held_by=None, error="")


def test_lowered_from_state_counts_a_missing_client_under_a_lowered_site():
    st = SinkState(enforced=True, site=_site("fill"),
                   clients={"wspr-recorder": _client("fill")})
    assert R.lowered_from_state(st) == frozenset(
        {"wspr-recorder", "psk-recorder", "meteor-scatter"})
    st = SinkState(enforced=True, site=_site("upload"),
                   clients={"psk-recorder": _client("off")})
    assert R.lowered_from_state(st) == frozenset({"psk-recorder"})
    st = SinkState(enforced=True, site=_site("upload"),
                   clients={"psk-recorder": _client("upload")})
    assert R.lowered_from_state(st) == frozenset()


# ---------------------------------------------------------------------------
# The stale heal (ruling R7): a restart that failed on an earlier run
# ---------------------------------------------------------------------------

PSK = "psk-recorder@AC0G-S.service"


def _routed(rig, mtime=1000):
    import os
    p = rig.env("psk-recorder", "AC0G-S", PSK_USE_HS_UPLOADER="1",
                PSK_DELIVERY_PIPELINES="server-merge")
    os.utime(p, (mtime, mtime))
    return p


def test_an_env_file_newer_than_the_units_start_is_restarted(rig):
    _routed(rig, mtime=1000)
    rig.states[PSK] = ("active", 900.0)
    res = rig.run(ALL, lowered=frozenset(), heal=True)
    assert rig.restarts == [[PSK]]
    assert res.stale == [(PSK, "it started before its env file last changed")]
    assert ("routing: restarted (stale) psk-recorder@AC0G-S.service: it "
            "started before its env file last changed") in res.lines()
    assert res.ok and res.changed == []


def test_an_env_file_older_than_the_units_start_is_left_alone(rig):
    _routed(rig, mtime=1000)
    for started in (1100.0, 1000.5):        # later, or the same second
        rig.states[PSK] = ("active", started)
        res = rig.run(ALL, lowered=frozenset(), heal=True)
        assert rig.restarts == [] and res.stale == []
        assert res.lines() == [
            "routing: 1 instance(s) already deliver via wsprdaemon.org"]


def test_an_inactive_or_failed_unit_is_never_restarted(rig):
    _routed(rig, mtime=1000)
    for state in ("inactive", "failed"):
        rig.states[PSK] = (state, 900.0)    # started before the file, too
        res = rig.run(ALL, lowered=frozenset(), heal=True)
        assert rig.restarts == [] and res.stale == [], state


def test_an_unreadable_unit_state_or_start_restarts_nothing(rig):
    _routed(rig)
    for st in (OSError("systemctl show exited 1"), ("active", None)):
        rig.states[PSK] = st
        res = rig.run(ALL, lowered=frozenset(), heal=True)
        assert rig.restarts == [] and res.stale == []


def test_the_heal_skips_a_dry_run_a_lowered_client_and_no_reader(rig):
    _routed(rig, mtime=1000)
    rig.states[PSK] = ("active", 900.0)
    rig.run(ALL, lowered=frozenset(), heal=True, apply=False)
    rig.run(ALL, lowered=frozenset({"psk-recorder"}), heal=True)
    rig.run(ALL, lowered=frozenset())               # no unit_state reader
    assert rig.restarts == []


def test_a_stale_restart_that_fails_is_a_failure(rig):
    _routed(rig, mtime=1000)
    rig.states[PSK] = ("active", 900.0)
    rig.restart_error = sink_levers.RestartFailed([(PSK, "exit 1")])
    res = rig.run(ALL, lowered=frozenset(), heal=True)
    assert res.stale == [] and not res.ok


def test_unit_state_reads_systemctl_show():
    import subprocess as sp
    seen = []

    def systemctl(cmd, **kw):
        seen.append(cmd)
        return sp.CompletedProcess(
            cmd, 0, "ActiveState=active\nActiveEnterTimestamp=@1790000000\n", "")
    assert R.unit_state(PSK, systemctl=systemctl) == ("active", 1790000000.0)
    assert seen[0][:2] == ["systemctl", "show"] and "--timestamp=unix" in seen[0]
