"""One ka9q-web per LOCAL radiod.

AC0G-B1 (2026-09-30) runs two radiods, one per RX888.  SIGMOND_RADIOD_STATUS
is unset there by design (CLIENT-CONTRACT §14.2), so the old single-unit
writer installed no ka9q-web at all.  The first local radiod must keep
ka9q-web.service on 8081 (the RAC web proxy port) so single-radiod stations
see no change.
"""
import importlib.util
import os

import pytest

from sigmond import coordination
from sigmond.coordination import Radiod


def _smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    spec = importlib.util.spec_from_loader("smd_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = "bin/smd"
    exec(compile(open("bin/smd").read(), "bin/smd", "exec"), mod.__dict__)
    return mod


class _Coord:
    def __init__(self, radiods):
        self.radiods = {r.id: r for r in radiods}

    def local_radiods(self):
        return [r for r in self.radiods.values() if r.is_local]


@pytest.fixture
def smd():
    return _smd()


def _with(monkeypatch, radiods):
    monkeypatch.setattr(coordination, "load_coordination",
                        lambda *a, **k: _Coord(radiods))


def test_single_local_radiod_is_unchanged(smd, monkeypatch):
    _with(monkeypatch, [Radiod(id="b4", host="localhost", status_dns="AC0G-B4-status.local")])
    assert smd._ka9q_web_targets() == [("ka9q-web.service", "AC0G-B4-status.local", 8081)]


def test_two_local_radiods_get_two_units(smd, monkeypatch):
    _with(monkeypatch, [
        Radiod(id="ac0g-b1-b", host="localhost", status_dns="ac0g-b1-b-status.local"),
        Radiod(id="ac0g-b1-a", host="localhost", status_dns="ac0g-b1-a-status.local"),
    ])
    assert smd._ka9q_web_targets() == [
        ("ka9q-web.service", "ac0g-b1-a-status.local", 8081),
        ("ka9q-web@ac0g-b1-b.service", "ac0g-b1-b-status.local", 8083),
    ]


def test_second_radiod_skips_gmag_webui_port(smd, monkeypatch):
    _with(monkeypatch, [Radiod(id="a", host="localhost", status_dns="a-status.local"),
                        Radiod(id="b", host="localhost", status_dns="b-status.local")])
    assert smd._ka9q_web_targets() == [("ka9q-web.service", "a-status.local", 8081),
                                      ("ka9q-web@b.service", "b-status.local", 8083)]


def test_first_radiod_always_8081(smd):
    assert smd._ka9q_web_port(0, frozenset({8081})) == 8081


def test_ports_skip_reserved_and_count_up(smd):
    assert smd._ka9q_web_port(1, frozenset({8082, 8000})) == 8083
    assert smd._ka9q_web_port(2, frozenset({8082, 8000})) == 8084


def test_remote_radiods_get_no_unit(smd, monkeypatch):
    _with(monkeypatch, [
        Radiod(id="here", host="localhost", status_dns="here-status.local"),
        Radiod(id="there", host="192.0.2.9", status_dns="there-status.local"),
    ])
    assert smd._ka9q_web_targets() == [("ka9q-web.service", "here-status.local", 8081)]


def test_template_reads_its_env_file(smd):
    t = smd._ka9q_web_unit_text("", 0, True)
    assert "EnvironmentFile=/etc/sigmond/ka9q-web/%i.env" in t
    assert "-m ${KA9Q_WEB_STATUS} -p ${KA9Q_WEB_PORT}" in t


def test_template_is_cpu_fenced():
    from sigmond.cpu import AFFINITY_UNITS
    assert "ka9q-web@.service" in AFFINITY_UNITS


# ---------------------------------------------------- radiod rename (spec §10.2)

def _loc(rid):
    return Radiod(id=rid, host="localhost", status_dns=f"{rid}-status.local")


def test_targets_for_is_pure_and_sorted(smd, monkeypatch):
    monkeypatch.setattr(coordination, "load_coordination",
                        lambda *a, **k: pytest.fail("targets_for must not load"))
    before = smd._ka9q_web_targets_for([_loc("omni"), _loc("AI6VN")])
    assert before == [("ka9q-web.service", "AI6VN-status.local", 8081),
                      ("ka9q-web@omni.service", "omni-status.local", 8083)]
    after = smd._ka9q_web_targets_for([_loc("se"), _loc("omni")])
    assert after == [("ka9q-web.service", "omni-status.local", 8081),
                     ("ka9q-web@se.service", "se-status.local", 8083)]


def test_targets_still_reads_coordination(smd, monkeypatch):
    _with(monkeypatch, [_loc("omni"), _loc("AI6VN")])
    assert smd._ka9q_web_targets() == smd._ka9q_web_targets_for(
        [_loc("AI6VN"), _loc("omni")])


def test_port_map_lines(smd):
    assert smd._ka9q_web_port_map([("ka9q-web.service", "a-status.local", 8081)]) \
        == ["ka9q-web: ka9q-web.service  a-status.local  :8081"]


def test_port_map_marks_a_changed_8081_holder(smd, monkeypatch):
    # AI6VN renamed to zz: omni now sorts first and takes 8081.
    _with(monkeypatch, [_loc("AI6VN"), _loc("omni")])
    before, after = smd._ka9q_web_port_map_for_rename("AI6VN", "zz")
    assert before == ["ka9q-web: ka9q-web.service  AI6VN-status.local  :8081",
                      "ka9q-web: ka9q-web@omni.service  omni-status.local  :8083"]
    assert after == ["ka9q-web: ka9q-web.service  omni-status.local  :8081"
                     "  <- 8081 holder changes",
                     "ka9q-web: ka9q-web@zz.service  zz-status.local  :8083"]


def test_port_map_keeps_8081_holder_unmarked(smd, monkeypatch):
    # omni -> se: AI6VN still sorts first; only the templated unit moves.
    _with(monkeypatch, [_loc("AI6VN"), _loc("omni")])
    _, after = smd._ka9q_web_port_map_for_rename("omni", "se")
    assert after == ["ka9q-web: ka9q-web.service  AI6VN-status.local  :8081",
                     "ka9q-web: ka9q-web@se.service  se-status.local  :8083"]
    # Renaming the 8081 holder itself, keeping it first, is no holder change.
    _, after = smd._ka9q_web_port_map_for_rename("AI6VN", "AA")
    assert "holder" not in after[0]


def test_rewrite_for_rename_retires_stale_templated_unit(smd, monkeypatch, tmp_path):
    # AI6VN -> zz flips the 8081 holder: ka9q-web@omni vanishes and the
    # plain unit changes status.
    calls, wrote = [], []

    class _R:
        returncode = 0
        stdout = stderr = ""

    monkeypatch.setattr(smd, "_run", lambda cmd, **k: (calls.append((cmd, k)), _R())[1])
    monkeypatch.setattr(smd, "_write_ka9q_web_unit", lambda: wrote.append(1) or True)
    import os as _os
    from pathlib import Path as _P
    monkeypatch.setattr(_P, "unlink", lambda *a, **k: pytest.fail("no direct unlink"))
    monkeypatch.setattr(_os, "remove", lambda *a, **k: pytest.fail("no direct remove"))
    before = smd._ka9q_web_targets_for([_loc("AI6VN"), _loc("omni")])
    _with(monkeypatch, [_loc("zz"), _loc("omni")])     # the handler's writes
    lines = smd._ka9q_web_rewrite_for_rename("AI6VN", "zz", before=before)

    assert wrote == [1]
    cmds = [c for c, _ in calls]
    assert ["systemctl", "stop", "ka9q-web@omni.service"] in cmds
    assert ["systemctl", "disable", "ka9q-web@omni.service"] in cmds
    assert ["rm", "-f", "/etc/sigmond/ka9q-web/omni.env"] in cmds
    assert ["systemctl", "restart", "ka9q-web.service"] in cmds
    assert all(k.get("sudo") for _, k in calls)
    # Nothing else is stopped or restarted (ka9q-web@zz is new: the handler
    # starts it).
    assert len([c for c in cmds if c[:2] == ["systemctl", "restart"]]) == 1
    assert not any("ka9q-web@zz" in " ".join(c) for c in cmds)
    text = "\n".join(lines)
    assert "ka9q-web: ka9q-web@omni.service  omni-status.local  :8083" in text
    assert "ka9q-web: ka9q-web@zz.service  zz-status.local  :8083" in text
    assert "stopped and disabled ka9q-web@omni.service" in text
    assert "restarted ka9q-web.service" in text


def test_rewrite_for_rename_plain_unit_env_untouched(smd, monkeypatch):
    calls = []

    class _R:
        returncode = 0
        stdout = stderr = ""

    monkeypatch.setattr(smd, "_run", lambda cmd, **k: (calls.append(cmd), _R())[1])
    monkeypatch.setattr(smd, "_write_ka9q_web_unit", lambda: True)
    before = smd._ka9q_web_targets_for([_loc("AI6VN"), _loc("omni")])
    _with(monkeypatch, [_loc("AI6VN"), _loc("se")])
    smd._ka9q_web_rewrite_for_rename("omni", "se", before=before)
    assert ["systemctl", "stop", "ka9q-web@omni.service"] in calls
    assert ["rm", "-f", "/etc/sigmond/ka9q-web/omni.env"] in calls
    assert not any(c[:2] == ["systemctl", "restart"] for c in calls)


def test_dispatch_dry_run_reaches_handler_with_hooks(smd, monkeypatch):
    from sigmond.commands import radiod_rename as rr
    seen = {}

    def fake(args):
        seen["args"] = (args.old, args.new, args.dry_run)
        seen["rewrite"] = rr.ka9q_web_rewrite
        seen["port_map"] = rr.ka9q_web_port_map
        return 7

    monkeypatch.setattr(rr, "cmd_radiod_rename", fake)
    monkeypatch.setattr(rr, "ka9q_web_rewrite", rr.ka9q_web_rewrite)
    monkeypatch.setattr(rr, "ka9q_web_port_map", rr.ka9q_web_port_map)
    monkeypatch.setattr(smd, "_need_root", lambda name: pytest.fail("dry run elevates"))
    _with(monkeypatch, [_loc("a")])
    import sys as _sys
    monkeypatch.setattr(_sys, "argv", ["smd", "admin", "radiod", "rename", "a", "b", "--dry-run"])
    assert smd.main() == 7
    assert seen["args"] == ("a", "b", True)
    assert seen["port_map"] is smd._ka9q_web_port_map_for_rename
    assert seen["rewrite"] is not None and seen["rewrite"].__name__ != "ka9q_web_rewrite"
