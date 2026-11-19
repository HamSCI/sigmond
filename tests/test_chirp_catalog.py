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


def test_profiles_list_it_optional_only():
    profiles = load_profiles()
    for name in ("dasi2", "client"):
        assert "chirp-recorder" in profiles[name].optional
        assert "chirp-recorder" not in profiles[name].clients
        assert "codar-sounder" in profiles[name].clients


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


def test_view_reads_a_chirp_physics_jsonl_fixture():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        d = time.gmtime()
        day = (root / "var/lib/chirp-recorder/AC0G-B4/physics/DUCK"
               / f"{d.tm_year:04d}" / f"{d.tm_mon:02d}" / f"{d.tm_mday:02d}.jsonl")
        day.parent.mkdir(parents=True)
        day.write_text("")

        class P(type(Path())):
            def __new__(cls, *a, **k):
                return super().__new__(cls, *[
                    str(root) + x if str(x).startswith("/var/lib/chirp-recorder")
                    or str(x).startswith("/etc/chirp-recorder") else x
                    for x in a], **k)

        recs = []

        def fake_sleep(_s):
            if not recs:
                recs.append(1)
                day.write_text(json.dumps({"mode_layer": "F2", "snr_db": 12.5,
                                           "station_id": "DUCK"}) + "\n")
            else:
                raise KeyboardInterrupt

        out = io.StringIO()
        ns = argparse.Namespace(instance="AC0G-B4", recent=0, verbose=False,
                                watch_target="chirp")
        with mock.patch.object(smd, "Path", P), \
             mock.patch.object(smd.time, "sleep", fake_sleep), \
             mock.patch.object(smd.os, "geteuid", lambda: 0), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = smd.cmd_codar_watch(ns)
        text = out.getvalue()
        assert rc == 130, text
        assert "chirp-recorder" in text
        assert "DUCK" in text and "peaks=  1" in text and "F2" in text
