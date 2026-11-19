"""sigmond knows chirp-recorder (Task 19)."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import bringup, cpu, instance, instance_rekey, lifecycle  # noqa: E402
from sigmond.catalog import load_catalog, load_profiles  # noqa: E402


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_chirp", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def test_catalog_entry():
    c = load_catalog()["chirp-recorder"]
    assert c.contract == "0.8" and "hamsci-dsp" in c.requires
    assert c.repo == "https://github.com/HamSCI/chirp-recorder"
    assert list(c.uses) == ["ka9q-python", "hamsci-dsp"]
    assert c.install_script == "/opt/git/sigmond/chirp-recorder/scripts/install.sh"
    assert c.start_priority == 200


def test_no_profile_names_it_yet():
    # github.com/HamSCI/chirp-recorder does not exist until Task 18 passes;
    # a profile entry would make bringup/readiness --with-optional clone it.
    # It joins the dasi2 and client `optional` lists at Task 24.
    for name, prof in load_profiles().items():
        assert "chirp-recorder" not in prof.clients, name
        assert "chirp-recorder" not in prof.optional, name
    assert "codar-sounder" in load_profiles()["client"].clients


def test_affinity_keeps_it_off_radiod_cores():
    assert cpu.AFFINITY_UNITS["chirp-recorder@.service"] == "other"


def test_reporter_keyed_where_it_matters():
    assert "chirp-recorder" in instance_rekey.CLIENTS
    assert "chirp-recorder" not in lifecycle.CONFIG_RADIOD_DEFAULTS
    assert "chirp-recorder" not in instance._TEMPLATED_RECORDER_CLIENTS
    assert "chirp-recorder" not in bringup._REPORTER_KEYED


def test_watch_has_a_chirp_target():
    p = smd._build_parser()
    ns = p.parse_args(["watch", "chirp"])
    assert ns.watch_target == "chirp"


def test_watch_chirp_dispatches_to_the_codar_view():
    argv = ["smd", "watch", "chirp"]
    with mock.patch.object(sys, "argv", argv), \
         mock.patch.object(smd, "cmd_codar_watch", return_value=7) as view:
        assert smd.main() == 7
    assert view.call_count == 1


# One record with the keys chirp-recorder's physics JsonlWriter writes
# (src/chirp_recorder/physics/output.py).
REAL_RECORD = {
    "timestamp": "2026-11-19T12:00:00+00:00", "client": "chirp-recorder",
    "radiod_id": "b4-rx888", "radiod_status_dns": "b4-status.local",
    "station_id": "DUCK", "oblique_freq_hz": 4463000,
    "sweep_rate_hz_per_s": -25730.0, "coherent_seconds": 30.0,
    "peak_index": 0, "peak_count": 1, "mode_layer": "F2", "snr_db": 12.5,
    "group_range_km": 812.4, "ground_distance_km": 600.1,
    "virtual_height_km": 310.2,
}


def _run_view(watch_target, tree, instance="AC0G-B4", config=None, extra=()):
    """Run cmd_codar_watch against a fixture tree; tree maps a path under
    the fake root to the day-file directory.  Returns (rc, text)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        d = time.gmtime()
        day = (root / tree.lstrip("/") / "DUCK" / f"{d.tm_year:04d}"
               / f"{d.tm_mon:02d}" / f"{d.tm_mday:02d}.jsonl")
        day.parent.mkdir(parents=True)
        day.write_text("")
        for e in extra:
            (root / e.lstrip("/")).mkdir(parents=True, exist_ok=True)
        if config:
            cfg = root / "etc/chirp-recorder/AC0G-B4.toml"
            cfg.parent.mkdir(parents=True)
            cfg.write_text(config.replace("@ROOT@", str(root)))

        class P(type(Path())):
            def __new__(cls, *a, **k):
                return super().__new__(cls, *[
                    str(root) + x if str(x).startswith(
                        ("/var/lib/", "/etc/chirp-recorder")) else x
                    for x in a], **k)

        ticks = []

        def fake_sleep(_s):
            if not ticks:
                ticks.append(1)
                day.write_text(json.dumps(REAL_RECORD) + "\n")
            else:
                raise KeyboardInterrupt

        out = io.StringIO()
        ns = argparse.Namespace(instance=instance, recent=0, verbose=False,
                                watch_target=watch_target)
        with mock.patch.object(smd, "Path", P), \
             mock.patch.object(smd.time, "sleep", fake_sleep), \
             mock.patch.object(smd.os, "geteuid", lambda: 0), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = smd.cmd_codar_watch(ns)
        return rc, out.getvalue()


def _assert_sees_record(rc, text):
    assert rc == 130, text
    assert "chirp-recorder" in text
    assert "DUCK" in text and "peaks=  1" in text and "F2" in text


def test_view_reads_a_chirp_physics_jsonl_fixture():
    _assert_sees_record(*_run_view(
        "chirp", "/var/lib/chirp-recorder/AC0G-B4/physics"))


def test_codar_view_falls_back_to_chirp_when_codar_root_is_gone():
    # codar-sounder retired: no /var/lib/codar-sounder, chirp tree present.
    _assert_sees_record(*_run_view(
        "codar", "/var/lib/chirp-recorder/AC0G-B4/physics", instance=None))


def test_codar_view_stays_on_codar_while_its_root_exists():
    rc, text = _run_view(
        "codar", "/var/lib/chirp-recorder/AC0G-B4/physics",
        extra=("/var/lib/codar-sounder",))
    assert rc == 1 and "codar-sounder" in text


def test_view_honours_a_configured_output_dir():
    _assert_sees_record(*_run_view(
        "chirp", "/var/lib/elsewhere/physics",
        config='[paths]\noutput_dir = "@ROOT@/var/lib/elsewhere"\n'))
