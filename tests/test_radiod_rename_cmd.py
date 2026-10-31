"""sigmond.commands.radiod_rename: applying a rename plan on a station.

Every host access goes through the module's hooks; each test replaces all
of them, so nothing here reads or writes outside tmp_path or runs systemctl.
"""
import argparse
import contextlib
import json
from pathlib import Path

import pytest

from sigmond.commands import radiod_rename as rr
from sigmond.coordination import load_coordination, render_env
from sigmond.lifecycle import UnitRef
from sigmond.radiod_rename import plan
from tests.test_radiod_rename import COMPONENTS, _apply, _tree, ai6vn_pm

CLIENTS = ["psk-recorder@AI6VN.service", "wspr-recorder@AI6VN.service",
           "meteor-scatter@AI6VN.service", "timestd-core-recorder.service"]


class Host:
    def __init__(self, active=(), enabled=()):
        self.active = set(active)
        self.enabled = set(enabled)
        self.calls = []
        self.web = []
        self.sdr_lists = 0
        self.locks = []
        self.released = []
        self.fail = set()          # call tuples that return 1

    def systemctl(self, *args):
        self.calls.append(args)
        return 1 if args in self.fail else 0

    def ka9q_web_rewrite(self, old, new):
        self.web.append((old, new))
        return [f"ka9q-web: rewrote {old} -> {new}"]

    def sdr_list(self):
        self.sdr_lists += 1
        return 0

    @contextlib.contextmanager
    def lock(self, reason):
        self.locks.append(reason)
        try:
            yield
        finally:
            self.released.append(reason)


def _no_passthrough(client_type):
    return []


@pytest.fixture
def station(tmp_path, monkeypatch):
    root = ai6vn_pm(tmp_path)

    def make(active=(), enabled=(), confirm=True):
        h = Host(active, enabled)
        monkeypatch.setattr(rr, "ROOT", root)
        monkeypatch.setattr(rr, "systemctl", h.systemctl)
        monkeypatch.setattr(rr, "is_active", lambda u: u in h.active)
        monkeypatch.setattr(rr, "is_enabled", lambda u: u in h.enabled)
        monkeypatch.setattr(rr, "ka9q_web_rewrite", h.ka9q_web_rewrite)
        monkeypatch.setattr(rr, "ka9q_web_port_map", lambda old, new: None)
        monkeypatch.setattr(rr, "confirm", lambda prompt: confirm)
        monkeypatch.setattr(rr, "lock", h.lock)
        monkeypatch.setattr(rr, "sdr_list", h.sdr_list)
        monkeypatch.setattr(rr, "passthrough_lookup", _no_passthrough)
        monkeypatch.setattr(rr, "enabled_components", lambda: list(COMPONENTS))
        monkeypatch.setattr(rr, "catalog_components", lambda: [])
        monkeypatch.setattr(rr, "resolve_client_units",
                            lambda comps: list(CLIENTS) if comps else [])
        return h

    make.root = root
    return make


def _args(old, new, *, description=None, dry_run=False, yes=True):
    return argparse.Namespace(old=old, new=new, description=description,
                              dry_run=dry_run, yes=yes)


def _first_rename_done(root):
    """AI6VN-PM after `omni -> se`: the name omni is free for AI6VN."""
    _apply(plan("omni", "se", root=root, components=COMPONENTS), root)


def _verbs(calls):
    return [c[0] for c in calls]


# ---------------------------------------------------------------- rename

def test_rename_inactive_omni_to_se_starts_nothing(station, capsys):
    h = station()                                  # nothing active or enabled
    root = station.root
    rc = rr.cmd_radiod_rename(_args("omni", "se"))
    assert rc == 0
    assert "start" not in _verbs(h.calls) and "enable" not in _verbs(h.calls)
    assert h.calls == [("stop", "radiod@omni.service"), ("daemon-reload",)]
    assert not (root / "etc/radio/radiod@omni.conf").exists()
    assert "se-status.local" in (root / "etc/radio/radiod@se.conf").read_text()
    assert h.web == [("omni", "se")]
    assert h.locks and h.sdr_lists == 1
    out = capsys.readouterr().out
    assert "radiod@se.service was not running before" in out


def test_rename_running_ai6vn_to_omni_restarts_clients_in_order(station, capsys):
    root = station.root
    _first_rename_done(root)
    h = station(active={"radiod@AI6VN.service", "ka9q-web.service", *CLIENTS},
                enabled={"radiod@AI6VN.service"})
    rc = rr.cmd_radiod_rename(_args("AI6VN", "omni", description="TCI-530 Omni"))
    assert rc == 0, capsys.readouterr()
    assert h.calls == [
        ("stop", "timestd-core-recorder.service"),
        ("stop", "meteor-scatter@AI6VN.service"),
        ("stop", "wspr-recorder@AI6VN.service"),
        ("stop", "psk-recorder@AI6VN.service"),
        ("stop", "ka9q-web.service"),
        ("stop", "radiod@AI6VN.service"),
        ("disable", "radiod@AI6VN.service"),
        ("daemon-reload",),
        ("enable", "radiod@omni.service"),
        ("start", "radiod@omni.service"),
        ("start", "ka9q-web.service"),
        ("start", "psk-recorder@AI6VN.service"),
        ("start", "wspr-recorder@AI6VN.service"),
        ("start", "meteor-scatter@AI6VN.service"),
        ("start", "timestd-core-recorder.service"),
    ]
    assert h.web == [("AI6VN", "omni")]
    for rel in ("etc/psk-recorder/AI6VN.toml", "etc/psk-recorder/psk-recorder-config.toml",
                "etc/wspr-recorder/AI6VN.toml", "etc/wspr-recorder/config.toml",
                "etc/meteor-scatter/AI6VN.toml",
                "etc/meteor-scatter/meteor-scatter-config.toml",
                "etc/hf-timestd/timestd-config.toml"):
        text = (root / rel).read_text()
        assert "omni-status.local" in text and "AI6VN-status.local" not in text, rel
    assert (root / "etc/radio/radiod@omni.conf.d").is_dir()
    assert (root / "etc/systemd/system/radiod@omni.service.d/smd-cpu-affinity.conf").is_file()
    assert not (root / "etc/radio/radiod@AI6VN.conf").exists()
    env = (root / "etc/sigmond/coordination.env").read_text()
    assert env == render_env(load_coordination(root / "etc/sigmond/coordination.toml"),
                             passthrough_lookup=_no_passthrough)
    assert "omni-status.local" in env and "AI6VN-status.local" not in env
    out = capsys.readouterr().out
    assert "ka9q-web: rewrote AI6VN -> omni" in out
    assert "filed under receiver" in out and "receiver_override" in out


def test_rename_keeps_mode_and_owner(station):
    root = station.root
    _first_rename_done(root)
    p = root / "etc/psk-recorder/AI6VN.toml"
    p.chmod(0o640)
    st = p.stat()
    station()
    assert rr.cmd_radiod_rename(_args("AI6VN", "omni")) == 0
    st2 = p.stat()
    assert (st2.st_mode & 0o7777, st2.st_uid, st2.st_gid) == (0o640, st.st_uid, st.st_gid)
    assert list(p.parent.glob("*.tmp")) == []


def test_dry_run_writes_nothing(station, capsys):
    root = station.root
    _first_rename_done(root)
    h = station(active={"radiod@AI6VN.service", *CLIENTS})
    before = _tree(root)
    rc = rr.cmd_radiod_rename(_args("AI6VN", "omni", dry_run=True, yes=False))
    assert rc == 0
    assert _tree(root) == before
    assert h.calls == [] and h.web == [] and h.locks == []
    out = capsys.readouterr().out
    assert "/etc/radio/radiod@AI6VN.conf -> /etc/radio/radiod@omni.conf" in out
    assert "/etc/psk-recorder/AI6VN.toml  (+1 changed lines)" in out
    assert "history stays under the old name:" in out
    assert "psk-recorder@AI6VN.service" in out


def test_declined_prompt_is_2(station):
    root = station.root
    before = _tree(root)
    h = station(active={"radiod@omni.service"}, confirm=False)
    rc = rr.cmd_radiod_rename(_args("omni", "se", yes=False))
    assert rc == 2
    assert _tree(root) == before and h.calls == [] and h.locks == []


def test_refusal_is_1_and_writes_nothing(station, capsys):
    root = station.root
    before = _tree(root)
    h = station()
    assert rr.cmd_radiod_rename(_args("AI6VN", "omni")) == 1   # omni exists
    assert "radiod@omni.conf already exists" in capsys.readouterr().err
    assert _tree(root) == before and h.calls == [] and h.locks == []


def test_refusal_when_new_unit_is_enabled_or_active(station, capsys):
    root = station.root
    before = _tree(root)
    for state in ("enabled", "active"):
        h = station(**{state: {"radiod@se.service"}})
        assert rr.cmd_radiod_rename(_args("omni", "se")) == 1
        err = capsys.readouterr().err
        assert "radiod@se.service is already" in err and "systemctl disable --now" in err
        assert _tree(root) == before and h.calls == []


def test_refusal_on_hold_file(station, capsys):
    root = station.root
    (root / "var/lib/sigmond").mkdir(parents=True)
    (root / "var/lib/sigmond/sdr-hold.json").write_text(json.dumps(
        {"X": {"serial": "X", "radiod_unit": "radiod@omni.service"}}))
    h = station()
    assert rr.cmd_radiod_rename(_args("omni", "se")) == 1
    assert "manual RX888 hold" in capsys.readouterr().err
    assert h.calls == []


UNITS_LINE = ("units: radiod@AI6VN.service stopped and disabled; "
              "radiod@omni.service not enabled; finish by hand or re-run "
              "after fixing")


def test_write_failure_midway_exits_4_and_lists_written(station, capsys, monkeypatch):
    root = station.root
    _first_rename_done(root)
    h = station(active={"radiod@AI6VN.service", "ka9q-web.service", *CLIENTS},
                enabled={"radiod@AI6VN.service"})
    real = rr.write_atomic
    n = {"edits": 0}

    def flaky(path, text):
        n["edits"] += 1
        if n["edits"] == 3:
            raise OSError(28, "No space left on device")
        real(path, text)
    monkeypatch.setattr(rr, "write_atomic", flaky)

    rc = rr.cmd_radiod_rename(_args("AI6VN", "omni"))
    assert rc == 4
    assert "start" not in _verbs(h.calls) and "enable" not in _verbs(h.calls)
    assert "daemon-reload" not in _verbs(h.calls)
    assert h.web == []
    out = capsys.readouterr().out
    tail = out[out.index("written:\n"):].splitlines()
    assert tail == [
        "written:",
        "/etc/radio/radiod@AI6VN.conf -> /etc/radio/radiod@omni.conf",
        "/etc/radio/radiod@AI6VN.conf.d -> /etc/radio/radiod@omni.conf.d",
        "/etc/systemd/system/radiod@AI6VN.service.d -> /etc/systemd/system/radiod@omni.service.d",
        "/etc/radio/radiod@omni.conf",
        "/etc/sigmond/coordination.toml",
        UNITS_LINE,
    ]
    assert h.locks == ["admin radiod rename AI6VN omni"]
    assert h.released == h.locks


def test_failed_daemon_reload_exits_4_and_starts_nothing(station, capsys):
    root = station.root
    _first_rename_done(root)
    h = station(active={"radiod@AI6VN.service", "ka9q-web.service", *CLIENTS},
                enabled={"radiod@AI6VN.service"})
    h.fail = {("daemon-reload",)}
    assert rr.cmd_radiod_rename(_args("AI6VN", "omni")) == 4
    assert _verbs(h.calls)[-1] == "daemon-reload"
    assert "start" not in _verbs(h.calls) and "enable" not in _verbs(h.calls)
    out = capsys.readouterr().out
    tail = out[out.index("written:\n"):].splitlines()
    assert tail[1] == "/etc/radio/radiod@AI6VN.conf -> /etc/radio/radiod@omni.conf"
    assert "/etc/sigmond/coordination.env" in tail
    assert tail[-1] == UNITS_LINE
    assert h.released == h.locks == ["admin radiod rename AI6VN omni"]


def test_hook_failure_lists_ka9q_web_actions_taken(station, capsys, monkeypatch):
    root = station.root
    _first_rename_done(root)
    h = station(active={"radiod@AI6VN.service", "ka9q-web.service", *CLIENTS},
                enabled={"radiod@AI6VN.service"})

    def hook(old, new):
        e = RuntimeError("systemctl restart blew up")
        e.lines = ["ka9q-web: stopped and disabled ka9q-web@omni.service",
                   "ka9q-web: removed /etc/sigmond/ka9q-web/omni.env"]
        raise e
    monkeypatch.setattr(rr, "ka9q_web_rewrite", hook)
    assert rr.cmd_radiod_rename(_args("AI6VN", "omni")) == 4
    assert "start" not in _verbs(h.calls)
    out = capsys.readouterr().out
    tail = out[out.index("written:\n"):].splitlines()
    assert "/etc/sigmond/coordination.env" in tail
    i = tail.index(UNITS_LINE)
    assert tail[i + 1:] == [
        "ka9q-web actions already taken:",
        "  ka9q-web: stopped and disabled ka9q-web@omni.service",
        "  ka9q-web: removed /etc/sigmond/ka9q-web/omni.env",
    ]


def test_units_line_when_old_radiod_was_not_enabled(station, capsys):
    root = station.root
    _first_rename_done(root)
    h = station(active={"radiod@AI6VN.service"})
    h.fail = {("daemon-reload",)}
    assert rr.cmd_radiod_rename(_args("AI6VN", "omni")) == 4
    out = capsys.readouterr().out
    assert ("units: radiod@AI6VN.service stopped; radiod@omni.service not "
            "enabled; finish by hand or re-run after fixing\n") in out


def test_web_units_for_is_the_handlers_rule(station):
    root = station.root
    _first_rename_done(root)
    station()
    p = plan("AI6VN", "omni", root=root, components=COMPONENTS)
    assert rr.web_units_for(p) == ["ka9q-web.service", "ka9q-web@AI6VN.service"]
    p.edits = [e for e in p.edits
               if e.path != root / "etc/systemd/system/ka9q-web.service"]
    assert rr.web_units_for(p) == ["ka9q-web@AI6VN.service"]


def test_handler_publishes_its_web_units_before_the_hook(station, monkeypatch):
    root = station.root
    _first_rename_done(root)
    h = station(active={"radiod@AI6VN.service", "ka9q-web.service"})
    seen = []
    monkeypatch.setattr(rr, "web_units_owned", None)
    monkeypatch.setattr(rr, "ka9q_web_rewrite",
                        lambda old, new: seen.append(rr.web_units_owned) or [])
    assert rr.cmd_radiod_rename(_args("AI6VN", "omni")) == 0
    assert seen == [["ka9q-web.service", "ka9q-web@AI6VN.service"]]


def test_unit_that_does_not_start_is_exit_5(station, capsys):
    root = station.root
    _first_rename_done(root)
    h = station(active={"radiod@AI6VN.service", "ka9q-web.service", *CLIENTS},
                enabled={"radiod@AI6VN.service"})
    h.fail = {("start", "wspr-recorder@AI6VN.service")}
    assert rr.cmd_radiod_rename(_args("AI6VN", "omni")) == 5
    # the remaining clients were still started after the failure
    assert ("start", "meteor-scatter@AI6VN.service") in h.calls
    assert ("start", "timestd-core-recorder.service") in h.calls
    out = capsys.readouterr().out
    assert "renamed, but these units did not start: wspr-recorder@AI6VN.service\n" in out
    started = out.split("started again:")[1].split("history stays")[0]
    assert "wspr-recorder@AI6VN.service" not in started
    assert "meteor-scatter@AI6VN.service" in started
    assert h.released == h.locks


def test_gated_ka9q_web_start_is_not_reported_as_started(station, capsys, monkeypatch):
    root = station.root
    _first_rename_done(root)
    h = station(active={"radiod@AI6VN.service", "ka9q-web.service", *CLIENTS},
                enabled={"radiod@AI6VN.service"})
    real = h.systemctl

    def systemctl(*args):
        rc = real(*args)
        if args[0] == "stop":
            h.active.discard(args[1])
        return rc          # a start returns 0 but changes nothing (gated)

    monkeypatch.setattr(rr, "systemctl", systemctl)
    assert rr.cmd_radiod_rename(_args("AI6VN", "omni")) == 0
    assert ("start", "ka9q-web.service") in h.calls
    out = capsys.readouterr().out
    started = out.split("started again:")[1].split("\n", 1)[1]
    started = started.split("history stays")[0]
    assert "ka9q-web.service" not in started


def test_failed_enable_of_new_radiod_is_exit_5(station, capsys):
    root = station.root
    _first_rename_done(root)
    h = station(active={"radiod@AI6VN.service"}, enabled={"radiod@AI6VN.service"})
    h.fail = {("enable", "radiod@omni.service"), ("start", "radiod@omni.service")}
    assert rr.cmd_radiod_rename(_args("AI6VN", "omni")) == 5
    assert ("renamed, but these units did not start: radiod@omni.service\n"
            in capsys.readouterr().out)


def test_default_resolve_client_units_orders_services(station, monkeypatch):
    station()
    seen = {}

    def fake_resolve(components, enabled):
        seen["args"] = (list(components), list(enabled))
        return [UnitRef("wspr-recorder", "wspr-recorder@AI6VN.service", None, None,
                        "service", "t"),
                UnitRef("hf-timestd", "timestd-core-recorder.service", None, None,
                        "service", "t"),
                UnitRef("hf-timestd", "timestd-daily.timer", None, None, "timer", "t"),
                UnitRef("psk-recorder", "psk-recorder@old.service", None, None,
                        "service", "t", orphaned=True)]

    def fake_order(units, coordination=None):
        seen["coord"] = coordination
        return list(reversed(units))

    monkeypatch.setattr(rr, "resolve_units", fake_resolve)
    monkeypatch.setattr(rr, "order_units", fake_order)
    monkeypatch.setattr(rr, "resolve_client_units", rr._default_resolve_client_units)
    got = rr.resolve_client_units(["wspr-recorder", "hf-timestd", "hfdl-recorder"])
    assert got == ["timestd-core-recorder.service", "wspr-recorder@AI6VN.service"]
    # only enabled components are resolved (hfdl-recorder is not enabled)
    assert seen["args"][0] == ["wspr-recorder", "hf-timestd"]
    assert seen["coord"] is not None


# ---------------------------------------------------------------- describe

def _dargs(instance, text, *, dry_run=False, yes=True):
    return argparse.Namespace(instance=instance, text=text, dry_run=dry_run, yes=yes)


def test_describe_restarts_only_radiod(station):
    root = station.root
    h = station(active={"radiod@AI6VN.service", *CLIENTS})
    assert rr.cmd_radiod_describe(_dargs("AI6VN", "TCI-530 Omni")) == 0
    assert h.calls == [("restart", "radiod@AI6VN.service")]
    assert 'description = "TCI-530 Omni"' in (root / "etc/radio/radiod@AI6VN.conf").read_text()

    h = station()                                  # parked: written, not started
    assert rr.cmd_radiod_describe(_dargs("omni", "TCI-530")) == 0
    assert h.calls == []
    assert 'description = "TCI-530"' in (root / "etc/radio/radiod@omni.conf").read_text()


def test_describe_failed_restart_is_exit_5(station, capsys):
    h = station(active={"radiod@AI6VN.service"})
    h.fail = {("restart", "radiod@AI6VN.service")}
    assert rr.cmd_radiod_describe(_dargs("AI6VN", "TCI-530 Omni")) == 5
    assert ("description set, but these units did not start: radiod@AI6VN.service\n"
            in capsys.readouterr().out)
    assert h.released == h.locks == ["admin radiod describe AI6VN"]


def test_describe_dry_run_refusal_and_decline(station):
    root = station.root
    before = _tree(root)
    h = station(active={"radiod@AI6VN.service"}, confirm=False)
    assert rr.cmd_radiod_describe(_dargs("AI6VN", "x", dry_run=True, yes=False)) == 0
    assert rr.cmd_radiod_describe(_dargs("AI6VN", "x", yes=False)) == 2
    assert rr.cmd_radiod_describe(_dargs("nope", "x")) == 1
    assert _tree(root) == before and h.calls == []
