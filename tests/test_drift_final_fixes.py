"""The drift report's final-review fixes, run without mocking the report
builder: C1 (`io` was never imported, so `--report` and the --apply refresh
died), I2 (apply-only flags reached the refresh's dry run), T7 (a non-UTF-8
exceptions file), T9 (SystemExit in the refresh), and the exceptions file's
header verb.

The earlier tests patched `_align_collect_report` or `_align_drift_report`
out, which is how C1 shipped.  Here only the station's probes are faked:
the dry run, the report builder, every collector, the exceptions file,
drift.json and the sink row all run for real against a fixture tree."""
import argparse
import contextlib
import dataclasses
import io
import json
import shutil
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from sigmond import align
from sigmond.coordination import Coordination, Host, Radiod
from sigmond.drift import build_report as br
from sigmond.drift import exceptions as drift_exceptions
from sigmond.drift import publish

from tests import test_align_cli as _tac

smd = _tac.smd
REL = _tac.REL
LIVE = {"sigmond": "daba1f6", "hf-timestd": "5c8196d", "ka9q-radio": "401992c"}
REPOS = Path(__file__).resolve().parents[2]
PSK_TEMPLATE = REPOS / "psk-recorder/config/psk-recorder-config.toml.template"


class FakeWriter:
    is_noop = False

    def __init__(self):
        self.rows = []
        self.buffered = 0

    def insert(self, rows):
        self.rows.extend(rows)

    def flush(self):
        pass

    def close(self):
        pass


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A station on disk: a checkout root with sigmond's units and the
    psk-recorder template, a unit directory, a decoder directory, a
    psk-recorder config equal to its template, and the publish targets."""
    base = tmp_path / "git"
    sysd = base / "sigmond" / "systemd"
    sysd.mkdir(parents=True)
    (sysd / "sigmond-drift.timer").write_text("[Timer]\nOnCalendar=daily\n")
    (sysd / "sigmond-drift.service").write_text("[Service]\nType=oneshot\n")
    units = tmp_path / "units"
    units.mkdir()
    (units / "sigmond-drift.service").write_text("[Service]\nType=oneshot\n")
    etc = tmp_path / "etc" / "psk-recorder"
    etc.mkdir(parents=True)
    psk_cfg = etc / "psk-recorder-config.toml"
    if PSK_TEMPLATE.is_file():
        (base / "psk-recorder" / "config").mkdir(parents=True)
        shutil.copy(PSK_TEMPLATE, base / "psk-recorder" / "config")
        psk_cfg.write_text(PSK_TEMPLATE.read_text())
    else:
        psk_cfg.write_text('[paths]\ndecoder_jt9 = "/usr/local/bin/jt9_decode"\n')

    writer = FakeWriter()
    drift_json = tmp_path / "var" / "drift.json"
    mid = tmp_path / "machine-id"
    mid.write_text("0123456789abcdef0123456789abcdef\n")
    monkeypatch.setattr(publish, "DRIFT_JSON", drift_json)
    monkeypatch.setattr(publish, "MACHINE_ID", mid)
    monkeypatch.setattr(publish, "make_writer", lambda: writer)
    monkeypatch.setattr("sigmond.site_profile.SITE_PROFILE_PATH",
                        tmp_path / "no-site-profile.toml")
    monkeypatch.setattr(
        "sigmond.coordination.load_coordination",
        lambda *a, **k: Coordination(
            host=Host(call="N0CALL", grid="AA00aa", lat=0.0, lon=0.0),
            radiods={"x-status.local": Radiod(id="x-status.local")}))
    monkeypatch.setattr("sigmond.receivers.load_receivers", lambda *a, **k: [])
    # The station's name reads its running instances through systemctl.
    monkeypatch.setattr("sigmond.uploader_manifest.reporter_call",
                        lambda coord: "N0CALL")

    exc_path = tmp_path / "drift-exceptions.toml"
    real_readers = br.station_readers

    def fixture_readers(**kw):
        r = real_readers(**kw)
        return dataclasses.replace(
            r, base=base, components=["sigmond", "psk-recorder"],
            sigmond_repo=base / "sigmond", unit_dir=units,
            decoder_dir=tmp_path / "decoders", stamp_dir=tmp_path / "stamps",
            enabled={"psk-recorder": True}, is_enabled=lambda u: False,
            service_active=lambda u: True, template_instances=lambda u: 0,
            client_configs={"psk-recorder": [psk_cfg]},
            catalog_override=tmp_path / "none-catalog.toml",
            pins_file=tmp_path / "none-pins.toml",
            coordination=tmp_path / "none-coord.toml",
            catalog_names=set(), unit_refs=[], exceptions_path=exc_path,
            run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    monkeypatch.setattr(br, "station_readers", fixture_readers)
    # Never the host's own aligned.json / host-aligned.json.
    monkeypatch.setattr(smd, "_align_json_file", lambda p: None)
    monkeypatch.setattr(smd, "_need_root", lambda name: False)
    return argparse.Namespace(drift_json=drift_json, writer=writer,
                              exc_path=exc_path, psk_cfg=psk_cfg)


@contextlib.contextmanager
def dry_station():
    """The dry run's own probes, as test_align_cli.run fakes them, with the
    drift report builder left REAL."""
    import sigmond.catalog
    patches = [
        mock.patch.object(smd, "_align_services", return_value={}),
        mock.patch.object(smd, "_align_units_started_at", return_value=None),
        mock.patch.object(smd, "_align_head_moved_at", return_value=None),
        mock.patch.object(smd, "_align_radiod_built_at", return_value=None),
        mock.patch.object(smd, "_align_live_state",
                          return_value=(dict(LIVE), {}, {}, {})),
        mock.patch("sigmond.align.image_file_drift", return_value=[
            {"path": "/usr/local/sbin/sigmond-site-timing",
             "status": "current", "note": ""}]),
        mock.patch("sigmond.align.recorded_release", return_value="v3.53"),
        mock.patch("sigmond.align.fetch_release", return_value=REL),
        mock.patch("sigmond.align_apply.alignment_summary", return_value=None),
        mock.patch.object(sigmond.catalog, "load_catalog", return_value={}),
    ]
    with contextlib.ExitStack() as st:
        for p in patches:
            st.enter_context(p)
        yield


def _args(**kw):
    ns = dict(release=None, manifest=None, base="/opt/git/sigmond",
              no_cost=True, apply=False, verify=False, json=False,
              report=False, accept=None, reason=None, test=False, until=None,
              no_restart=False, allow_rollback=False, max_bytes=None,
              install_missing=False)
    ns.update(kw)
    return argparse.Namespace(**ns)


def _cmd(args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = smd.cmd_align(args)
    return rc, out.getvalue()


# ---- C1 ---------------------------------------------------------------------

def test_smd_imports_io_at_module_level():
    assert smd.io is io


def test_collect_report_runs_with_only_cmd_align_stubbed():
    from sigmond.drift.model import Report
    want = Report(station="S", release=None, baseline="none")

    def fake_cmd_align(dry):
        print("dry-run text that must be swallowed")
        dry._drift_sink["report"] = want
        return 1
    out = io.StringIO()
    with mock.patch.object(smd, "cmd_align", fake_cmd_align), \
            contextlib.redirect_stdout(out):
        got = smd._align_collect_report(_args(report=True))
    assert got is want and out.getvalue() == ""


def test_collect_report_unmocked_builds_the_real_report(tree):
    with dry_station():
        report = smd._align_collect_report(_args(report=True))
    assert report is not None and report.station == "N0CALL"
    keys = {i.key for i in report.items}
    assert "unit:sigmond-drift.timer" in keys        # not installed
    assert not any(k.startswith("override:") for k in keys)   # C3


def test_align_report_end_to_end_writes_drift_json_and_one_row(tree):
    with dry_station():
        rc, out = _cmd(_args(report=True))
    assert rc == 0, out
    doc = json.loads(tree.drift_json.read_text())
    assert doc["station"] == "N0CALL"
    assert "unit:sigmond-drift.timer" in {i["key"] for i in doc["items"]}
    assert len(tree.writer.rows) == 1
    assert tree.writer.rows[0]["reporter_id"] == "N0CALL"
    assert out.isascii()             # --report prints nothing but ASCII


# ---- I2 ---------------------------------------------------------------------

@pytest.mark.parametrize("flag", [
    {"no_restart": True}, {"allow_rollback": True}, {"max_bytes": 1 << 20},
    {"no_restart": True, "allow_rollback": True, "max_bytes": 5},
])
def test_refresh_after_apply_with_apply_only_flags(tree, flag):
    with dry_station(), mock.patch.object(smd, "_align_apply", return_value=0):
        rc, out = _cmd(_args(apply=True, **flag))
    assert rc == 0
    assert "drift.json not refreshed" not in out, out
    assert json.loads(tree.drift_json.read_text())["station"] == "N0CALL"


@pytest.mark.parametrize("key", sorted(smd._ALIGN_DRY_OFF))
def test_dry_args_turn_every_mode_and_apply_flag_off(key):
    on = {"apply": True, "verify": True, "json": True, "report": True,
          "accept": "unit:x", "reason": "r", "test": True, "until": "2026-12-01",
          "no_restart": True, "allow_rollback": True, "max_bytes": 9}
    args = _args(**on, release="v3.53", install_missing=True)
    args._drift_sink = {"stale": 1}
    dry = smd._align_dry_args(args)
    assert getattr(dry, key) == smd._ALIGN_DRY_OFF[key]
    assert (dry.release, dry.install_missing) == ("v3.53", True)
    assert not hasattr(dry, "_drift_sink")
    assert args.apply is True          # the caller's args are untouched


# ---- T9 ---------------------------------------------------------------------

def test_refresh_survives_systemexit_and_keeps_the_apply_rc():
    with mock.patch.object(smd, "_align_apply", return_value=0), \
            mock.patch.object(smd, "_align_collect_report",
                              side_effect=SystemExit(3)):
        rc, out = _cmd(_args(apply=True))
    assert rc == 0
    assert "drift.json not refreshed: 3" in out


# ---- T7 ---------------------------------------------------------------------

def _bad_exceptions(tree):
    tree.exc_path.write_bytes(b'[[exception]]\nkey = "\xff\xfe"\n')


def test_non_utf8_exceptions_file_is_a_fault_in_report(tree):
    _bad_exceptions(tree)
    with dry_station():
        rc, out = _cmd(_args(report=True))
    assert rc == 0, out
    doc = json.loads(tree.drift_json.read_text())
    item = next(i for i in doc["items"]
                if i["key"] == f"override:{tree.exc_path}")
    assert item["severity"] == "fault"
    assert "cannot be parsed" in item["detail"]


def test_non_utf8_exceptions_file_dry_run_and_json(tree):
    _bad_exceptions(tree)
    with dry_station():
        rc, out = _cmd(_args())
    assert rc in (0, 1) and "drift exceptions file unreadable" in out
    drift = out[out.index("  drift report"):]
    assert drift.isascii()
    with dry_station():
        rc, out = _cmd(_args(json=True))
    doc = json.loads(out)
    assert f"override:{tree.exc_path}" in {i["key"] for i in doc["items"]}


def test_non_utf8_exceptions_file_refuses_accept(tmp_path):
    p = tmp_path / "exc.toml"
    p.write_bytes(b"\xff")
    from datetime import datetime, timezone
    with pytest.raises(drift_exceptions.ExceptionsFileError):
        drift_exceptions.accept(p, "unit:x", reason="r", by="mjh", test=True,
                                until=None,
                                now=datetime(2026, 11, 19, tzinfo=timezone.utc))


# ---- M2 ---------------------------------------------------------------------

def test_exceptions_header_names_the_real_verb(tmp_path):
    from datetime import datetime, timezone
    p = tmp_path / "exc.toml"
    drift_exceptions.accept(p, "unit:x", reason="r", by="mjh", test=True,
                            until=None,
                            now=datetime(2026, 11, 19, tzinfo=timezone.utc))
    head = p.read_text().splitlines()[0]
    assert "smd align --accept" in head and "smd drift" not in head


# ---- v3.94 Task 4: WB6CXC end to end -----------------------------------------

def _git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin"}
    for cmd in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", "-C", str(path)] + cmd, check=True, env=env,
                       capture_output=True)


def test_wb6cxc_report_shows_only_the_real_timer_fault(tree, tmp_path,
                                                       monkeypatch):
    """WB6CXC, 2026-11-19: one radiod (plain ka9q-web.service, no
    ka9q-web@ instance), mag-recorder enabled but stopped on purpose,
    UberSDR decoders built at sigmond's pin while the ubersdr_wsprd
    checkout sits ahead.  Through the real `smd align --report`, the only
    fault left is the sigmond-drift.timer that really is not installed."""
    from sigmond import decoder_choice
    base, units = tmp_path / "git", tmp_path / "units"
    (base / "mag-recorder").mkdir()
    (base / "mag-recorder" / "deploy.toml").write_text(
        '[systemd]\nunits = ["mag-recorder.service", '
        '"mag-recorder-upload.timer"]\n')
    (base / "psk-recorder").mkdir(exist_ok=True)
    (base / "psk-recorder" / "deploy.toml").write_text(
        '[systemd]\nunits = ["psk-recorder@.service"]\n')
    for u in ("ka9q-web.service", "psk-recorder@.service",
              "mag-recorder.service", "mag-recorder-upload.timer"):
        (units / u).write_text("[Unit]\n")
    _git_repo(base / "ubersdr_wsprd")          # HEAD is not sigmond's pin
    dec = tmp_path / "decoders" / "ubersdr"
    dec.mkdir(parents=True)
    (dec / "wsprd").write_text("")
    (dec / ".provenance").write_text(
        f"{smd._UBERSDR_COMMIT} parity=verified\n")
    monkeypatch.setattr(decoder_choice, "BIN_DIR", tmp_path / "bin")
    monkeypatch.setattr(decoder_choice, "COORDINATION_TOML",
                        tmp_path / "none.toml")
    monkeypatch.setattr(smd, "_COMPONENT_PINS_FILE", tmp_path / "no-pins")
    monkeypatch.delenv("SIGMOND_TRACK_MAIN", raising=False)

    tree_readers = br.station_readers
    seen = {}

    def wb6cxc_readers(**kw):
        r = tree_readers(**kw)
        seen["native_pins"] = br._v(r.native_pins)
        return dataclasses.replace(
            r, enabled={"psk-recorder": True, "mag-recorder": True,
                        "ka9q-web": True},
            service_active=lambda s: s == "ka9q-web.service",
            template_instances=lambda t: 1 if t == "psk-recorder@.service"
            else 0)
    monkeypatch.setattr(br, "station_readers", wb6cxc_readers)

    with dry_station():
        rc, out = _cmd(_args(report=True))
    assert rc == 0, out
    # smd filled the pins from _native_pin, not the checkout.
    assert seen["native_pins"]["ubersdr_wsprd"] == smd._UBERSDR_COMMIT
    doc = json.loads(tree.drift_json.read_text())
    faults = {i["key"] for i in doc["items"] if i["severity"] == "fault"}
    # The fixture holds no aligned record, so source:baseline drifts too.
    drifts = {i["key"]: i for i in doc["items"]
              if i["severity"] == "drift" and i["kind"] != "source"}
    assert faults == {"unit:sigmond-drift.timer"}, doc["items"]
    assert set(drifts) == {"unit:mag-recorder.service"}, doc["items"]
    assert drifts["unit:mag-recorder.service"]["fix"] == \
        "smd start mag-recorder"
