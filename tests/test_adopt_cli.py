"""`smd adopt` turns an offer into a running configuration — and only then.

Adoption is the ONLY verb in this design that starts anything.  Everything
upstream of it observes and reports, so every assertion here is really about
one question: does the box do exactly what the operator named, and nothing
else?

On 2026-09-01 a unit nobody asked to run was started by `smd apply` and took a
station's timing chain down twice in one day.  This verb is the one that could
repeat that, which is why the write path is tested against a temporary
clients root rather than mocked away.
"""

import importlib.util
import os
import types

import pytest

from sigmond.adoption import StationInventory
from sigmond.sources import SourceKey


def _smd():
    # bin/smd re-execs into <repo>/venv/bin/python at module scope, right
    # after __file__ becomes resolvable -- which our exec() below makes true
    # for the first time.  Left unset, importing this on a checkout that has
    # a literal venv/ would replace the running pytest process via os.execv()
    # instead of failing cleanly.  setdefault so a caller's override wins.
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    spec = importlib.util.spec_from_loader("smd_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = "bin/smd"
    exec(compile(open("bin/smd").read(), "bin/smd", "exec"), mod.__dict__)
    return mod


RX = SourceKey(type="usb", identifier="04b4:00f1:s")
RX2 = SourceKey(type="usb", identifier="04b4:00f1:other")

FARGO = SourceKey(type="radiod", identifier="fargo-status.local")


def _identity(hostname="fargo-1", **kw):
    from sigmond.station_identity import StationIdentity
    return StationIdentity(hostname=hostname, dasi2_site=False, **kw)


def _args(name, tmp_path, dry_run=False):
    return types.SimpleNamespace(name=name, dry_run=dry_run,
                                 clients_root=str(tmp_path))


def _wire(mod, inv, tmp_path, identity=None, adopted=frozenset()):
    """Point the module's three seams at a fabricated station."""
    mod._station_inventory = lambda: inv
    mod._adopted_sources = lambda **kw: adopted
    mod._station_identity = lambda: identity or _identity()
    # Never elevate in a test: _need_root() os.execvp()s sudo and would
    # replace the pytest process.
    mod._need_root = lambda _name: False


# ---------------------------------------------------------------------------
# Naming: adopt what the operator named, or refuse and say what exists
# ---------------------------------------------------------------------------

def test_adopting_an_unknown_name_fails_and_says_what_is_available(
        tmp_path, capsys):
    mod = _smd()
    _wire(mod, StationInventory(hardware=frozenset({"rx888"}), sources=(RX,)),
          tmp_path)
    rc = mod.cmd_adopt(_args("nope", tmp_path, dry_run=True))
    assert rc != 0
    err = capsys.readouterr().err
    assert "nope" in err
    assert str(RX) in err          # names what IS adoptable
    assert not list(tmp_path.iterdir())


def test_an_empty_station_says_nothing_is_adoptable(tmp_path, capsys):
    mod = _smd()
    _wire(mod, StationInventory(), tmp_path)
    rc = mod.cmd_adopt(_args("dasi2", tmp_path, dry_run=True))
    assert rc != 0
    assert "nothing" in capsys.readouterr().err.lower()


def test_an_already_adopted_source_is_not_adoptable_again(tmp_path):
    mod = _smd()
    _wire(mod, StationInventory(hardware=frozenset({"rx888"}), sources=(RX,)),
          tmp_path, adopted=frozenset({RX}))
    assert mod.cmd_adopt(_args(str(RX), tmp_path, dry_run=True)) != 0


# ---------------------------------------------------------------------------
# Membership: an unrostered DASI name refuses BEFORE anything is touched
# ---------------------------------------------------------------------------

def test_an_unrostered_dasi_hostname_refuses_before_doing_anything(
        tmp_path, capsys):
    """The refusal must come BEFORE any component is touched.

    A machine whose identity cannot be decided must not be half-configured
    under a name it may not own.
    """
    from sigmond.station_identity import UnrosteredDasiName, identify

    with pytest.raises(UnrosteredDasiName):
        identify("DASI019", {"DASI001": {"psws_station": "S1",
                                         "psws_instrument": "I1"}})

    mod = _smd()
    _wire(mod, StationInventory(hardware=frozenset({"rx888"}), sources=(RX,)),
          tmp_path)

    def _refuse():
        raise UnrosteredDasiName("hostname 'DASI019' ... add a DASI019 entry")
    mod._station_identity = _refuse

    rc = mod.cmd_adopt(_args(str(RX), tmp_path))       # NOT a dry run
    assert rc != 0
    assert "DASI019" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())                # nothing written


# ---------------------------------------------------------------------------
# Dry run reports and changes nothing
# ---------------------------------------------------------------------------

def test_dry_run_reports_the_plan_and_changes_nothing(tmp_path, capsys):
    mod = _smd()
    _wire(mod, StationInventory(hardware=frozenset({"rx888"}), sources=(RX,)),
          tmp_path)
    rc = mod.cmd_adopt(_args(str(RX), tmp_path, dry_run=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "ka9q-radio" in out                    # the plan is reported
    assert "dry run" in out.lower()
    assert not list(tmp_path.iterdir())           # ... and nothing happened


def test_a_dasi2_alike_is_told_what_it_still_has_to_be_asked(
        tmp_path, capsys):
    """No roster entry means no grant identity -- ask, don't invent one."""
    mod = _smd()
    _wire(mod, StationInventory(
        hardware=frozenset({"rx888", "gpsdo", "magnetometer"}),
        sources=(RX,)), tmp_path)
    rc = mod.cmd_adopt(_args("dasi2", tmp_path, dry_run=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "psws_station" in out
    assert "mag-recorder" in out and "gpsdo-monitor" in out


def test_a_rostered_dasi2_site_prefills_its_psws_identity(tmp_path, capsys):
    from sigmond.station_identity import StationIdentity
    mod = _smd()
    _wire(mod, StationInventory(
        hardware=frozenset({"rx888", "gpsdo", "magnetometer"}),
        sources=(RX,)), tmp_path,
        identity=StationIdentity(hostname="DASI001", dasi2_site=True,
                                 psws_station="S000123",
                                 psws_instrument="I000456"))
    assert mod.cmd_adopt(_args("dasi2", tmp_path, dry_run=True)) == 0
    out = capsys.readouterr().out
    assert "S000123" in out and "I000456" in out


# ---------------------------------------------------------------------------
# The write path: exactly what was named, nothing else
# ---------------------------------------------------------------------------

def test_adopt_records_only_the_sources_of_the_named_offer(tmp_path):
    """Two SDRs attached, one adopted.  The other must stay untouched --
    hardware appearing is never an instruction."""
    from sigmond.sources import ClientSources
    mod = _smd()
    _wire(mod, StationInventory(hardware=frozenset({"rx888"}),
                                sources=(RX, RX2)), tmp_path)
    rc = mod.cmd_adopt(_args(str(RX), tmp_path))
    assert rc == 0

    # `plan()` maps rx888 -> ka9q-radio + ka9q-web + igmp-querier, so those
    # are the components whose selection the adopted source lands in.
    sel = ClientSources.load("ka9q-radio", root=tmp_path).selected
    assert RX in sel
    assert RX2 not in sel
    assert not (tmp_path / "wspr-recorder.sources.toml").exists()


def test_adopting_twice_does_not_duplicate_the_selection(tmp_path):
    from sigmond.sources import ClientSources
    mod = _smd()
    _wire(mod, StationInventory(hardware=frozenset({"rx888"}), sources=(RX,)),
          tmp_path)
    assert mod.cmd_adopt(_args(str(RX), tmp_path)) == 0
    assert mod.cmd_adopt(_args(str(RX), tmp_path)) == 0
    sel = ClientSources.load("ka9q-radio", root=tmp_path).selected
    assert sel.count(RX) == 1


# ---------------------------------------------------------------------------
# _station_inventory: locally attached hardware must be visible as a source
# ---------------------------------------------------------------------------

def _fake_sysfs(tmp_path, devices):
    """Build a /sys/bus/usb/devices lookalike.  `devices` is a list of
    (dirname, idVendor, idProduct, serial-or-None)."""
    root = tmp_path / "sys"
    root.mkdir()
    for name, vid, pid, serial in devices:
        d = root / name
        d.mkdir()
        (d / "idVendor").write_text(vid + "\n")
        (d / "idProduct").write_text(pid + "\n")
        if serial is not None:
            (d / "serial").write_text(serial + "\n")
    # An interface directory, which carries no idVendor at all.
    (root / "1-2:1.0").mkdir()
    return root


def _no_discovery(monkeypatch, tmp_path):
    import sigmond.discovery as discovery
    monkeypatch.setattr(discovery, "cache_path",
                        lambda: tmp_path / "no-such-cache.json")


def test_a_locally_attached_rx888_is_adoptable_without_any_lan_radiod(
        tmp_path, monkeypatch):
    """The defect this guards: `sources.inventory()` deliberately projects no
    USB devices, so a station with an RX888 plugged in and no LAN radiod would
    report hardware but no sources -- and `offers()` on empty sources returns
    nothing.  The box would sit silent with the hardware in its hand."""
    mod = _smd()
    _no_discovery(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_USB_SYSFS_ROOT",
                        _fake_sysfs(tmp_path,
                                    [("1-4", "04b4", "00f1", "0009072C56")]))
    monkeypatch.setattr(mod, "_detect_local_sdr", lambda: True)
    monkeypatch.setattr(mod, "_detect_gpsdo", lambda: False)
    monkeypatch.setattr(mod, "_detect_magnetometer", lambda: False)

    inv = mod._station_inventory()
    assert inv.hardware == frozenset({"rx888"})
    assert SourceKey(type="usb",
                     identifier="04b4:00f1:0009072C56") in inv.sources

    from sigmond.adoption import offers
    assert offers(inv, frozenset()) != []


def test_an_unreadable_serial_falls_back_to_a_still_stable_key(
        tmp_path, monkeypatch):
    """vid:pid is stable across reboots; a bus/device number is not.  The
    known cost is stated in the code: two identical SDRs collide."""
    mod = _smd()
    _no_discovery(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_USB_SYSFS_ROOT",
                        _fake_sysfs(tmp_path,
                                    [("1-4", "04b4", "00f3", None)]))
    monkeypatch.setattr(mod, "_detect_local_sdr", lambda: True)
    monkeypatch.setattr(mod, "_detect_gpsdo", lambda: False)
    monkeypatch.setattr(mod, "_detect_magnetometer", lambda: False)

    keys = mod._station_inventory().sources
    assert keys == (SourceKey(type="usb", identifier="04b4:00f3"),)


def test_a_serial_with_shell_metacharacters_never_raises(
        tmp_path, monkeypatch):
    """SourceKey rejects whitespace and metacharacters.  A stray byte off the
    USB bus must degrade to the vid:pid key, not blow up in the operator's
    face at `smd status`."""
    mod = _smd()
    _no_discovery(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_USB_SYSFS_ROOT",
                        _fake_sysfs(tmp_path,
                                    [("1-4", "04b4", "00f1", "a b;$c")]))
    monkeypatch.setattr(mod, "_detect_local_sdr", lambda: True)
    monkeypatch.setattr(mod, "_detect_gpsdo", lambda: False)
    monkeypatch.setattr(mod, "_detect_magnetometer", lambda: False)

    keys = mod._station_inventory().sources
    assert keys == (SourceKey(type="usb", identifier="04b4:00f1"),)


def test_unrelated_usb_devices_are_not_offered_as_sources(
        tmp_path, monkeypatch):
    """A hub is not a source, and neither is hardware this station has not
    detected -- sources and hardware must agree, or adopting one would plan
    to install nothing."""
    mod = _smd()
    _no_discovery(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_USB_SYSFS_ROOT",
                        _fake_sysfs(tmp_path, [
                            ("1-1", "1d6b", "0002", "0000:00:14.0"),  # hub
                            ("1-3", "1dd2", "2211", "mini01"),        # GPSDO
                        ]))
    monkeypatch.setattr(mod, "_detect_local_sdr", lambda: True)
    monkeypatch.setattr(mod, "_detect_gpsdo", lambda: False)   # not detected
    monkeypatch.setattr(mod, "_detect_magnetometer", lambda: False)

    assert mod._station_inventory().sources == ()


def test_the_fargo_pair_is_offered_as_two_devices(tmp_path, monkeypatch):
    """The Fargo Beelink: an RX888 and a Bodnar miniGPS, no magnetometer.
    Both must be nameable, or the box cannot say what the dry run predicts."""
    mod = _smd()
    _no_discovery(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_USB_SYSFS_ROOT",
                        _fake_sysfs(tmp_path, [
                            ("1-1", "1d6b", "0002", "0000:00:14.0"),  # hub
                            ("1-3", "1dd2", "2211", "mini01"),
                            ("1-4", "04b4", "00f1", "0009061C028B1629"),
                        ]))
    monkeypatch.setattr(mod, "_detect_local_sdr", lambda: True)
    monkeypatch.setattr(mod, "_detect_gpsdo", lambda: True)
    monkeypatch.setattr(mod, "_detect_magnetometer", lambda: False)

    from sigmond.adoption import offers, recognise
    inv = mod._station_inventory()
    assert set(inv.sources) == {
        SourceKey(type="usb", identifier="1dd2:2211:mini01"),
        SourceKey(type="usb", identifier="04b4:00f1:0009061C028B1629"),
    }
    assert recognise(inv) is None            # no magnetometer: not the kit
    assert {o.name for o in offers(inv, frozenset())} == \
        {str(k) for k in inv.sources}


def test_a_magnetometer_on_the_pololu_adapter_is_named(tmp_path, monkeypatch):
    mod = _smd()
    _no_discovery(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_USB_SYSFS_ROOT",
                        _fake_sysfs(tmp_path,
                                    [("1-5", "1ffb", "2502", "MAG7")]))
    monkeypatch.setattr(mod, "_detect_local_sdr", lambda: False)
    monkeypatch.setattr(mod, "_detect_gpsdo", lambda: False)
    monkeypatch.setattr(mod, "_detect_magnetometer", lambda: True)

    assert mod._station_inventory().sources == (
        SourceKey(type="usb", identifier="1ffb:2502:MAG7"),)


def test_no_local_sdr_means_no_synthesised_usb_key(tmp_path, monkeypatch):
    """Hardware and sources must agree: no `rx888` in hardware, no usb key --
    otherwise adopting one would plan to install nothing."""
    mod = _smd()
    _no_discovery(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_USB_SYSFS_ROOT",
                        _fake_sysfs(tmp_path,
                                    [("1-4", "04b4", "00f1", "0009072C56")]))
    monkeypatch.setattr(mod, "_detect_local_sdr", lambda: False)
    monkeypatch.setattr(mod, "_detect_gpsdo", lambda: False)
    monkeypatch.setattr(mod, "_detect_magnetometer", lambda: False)

    assert mod._station_inventory().sources == ()


def test_a_missing_sysfs_tree_is_not_an_error(tmp_path, monkeypatch):
    mod = _smd()
    _no_discovery(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_USB_SYSFS_ROOT", tmp_path / "absent")
    monkeypatch.setattr(mod, "_detect_local_sdr", lambda: True)
    monkeypatch.setattr(mod, "_detect_gpsdo", lambda: False)
    monkeypatch.setattr(mod, "_detect_magnetometer", lambda: False)

    assert mod._station_inventory().sources == ()


def test_local_usb_and_discovered_radiods_are_both_offered(
        tmp_path, monkeypatch):
    import json
    import sigmond.discovery as discovery

    cache = tmp_path / "environment-cache.json"
    cache.write_text(json.dumps({
        "probed_at": 0.0,
        "observations": [{
            "source": "mdns", "kind": "radiod", "id": "r1",
            "endpoint": "fargo-status.local:5006", "fields": {},
            "observed_at": 1.0, "ok": True, "error": "",
        }],
        "deltas": [],
    }))
    mod = _smd()
    monkeypatch.setattr(discovery, "cache_path", lambda: cache)
    monkeypatch.setattr(mod, "_USB_SYSFS_ROOT",
                        _fake_sysfs(tmp_path,
                                    [("1-4", "04b4", "00f1", "0009072C56")]))
    monkeypatch.setattr(mod, "_detect_local_sdr", lambda: True)
    monkeypatch.setattr(mod, "_detect_gpsdo", lambda: False)
    monkeypatch.setattr(mod, "_detect_magnetometer", lambda: False)

    sources = mod._station_inventory().sources
    assert FARGO in sources
    assert SourceKey(type="usb", identifier="04b4:00f1:0009072C56") in sources


# ---------------------------------------------------------------------------
# The verb is registered, and does not shadow `admin manifest adopt`
# ---------------------------------------------------------------------------

def test_adopt_is_a_top_level_verb(monkeypatch):
    mod = _smd()
    monkeypatch.setattr("sys.argv", ["smd", "adopt", "--help"])
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0


def test_the_blessed_manifest_adopt_still_parses(monkeypatch):
    """A pre-existing, unrelated `smd admin manifest adopt`.  Neither verb
    may shadow the other."""
    mod = _smd()
    monkeypatch.setattr("sys.argv",
                        ["smd", "admin", "manifest", "adopt", "--help"])
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0


# ---------------------------------------------------------------------------
# Adopt must see its own writes, or "already adopted" means nothing
# ---------------------------------------------------------------------------

def test_an_adopted_source_reads_back_as_adopted(tmp_path):
    """The round trip that gives stable identifiers their point.

    `plan()` maps an SDR to ka9q-radio / ka9q-web / igmp-querier, and none of
    those is in `sources.KNOWN_CLIENTS` -- so a reader that consulted only
    that list would never see what adopt just wrote, and the device would be
    offered again on every `smd status`, forever.
    """
    mod = _smd()
    _wire(mod, StationInventory(hardware=frozenset({"rx888"}), sources=(RX,)),
          tmp_path)
    assert mod.cmd_adopt(_args(str(RX), tmp_path)) == 0

    fresh = _smd()          # no stubs: the real reader, against the real files
    assert RX in fresh._adopted_sources(root=tmp_path)


def test_adopt_refuses_a_source_it_already_recorded(tmp_path):
    """End to end with no stubbed reader: adopt, then adopt again."""
    mod = _smd()
    inv = StationInventory(hardware=frozenset({"rx888"}), sources=(RX,))
    mod._station_inventory = lambda: inv
    mod._station_identity = lambda: _identity()
    mod._need_root = lambda _name: False
    assert mod.cmd_adopt(_args(str(RX), tmp_path)) == 0
    assert mod.cmd_adopt(_args(str(RX), tmp_path)) != 0     # nothing left
