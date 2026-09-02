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

import contextlib
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

MAG = SourceKey(type="usb", identifier="1ffb:2502:MAG7")

FARGO = SourceKey(type="radiod", identifier="fargo-status.local")


def _station(hardware, *pairs):
    """A station whose local sources carry the hardware kind they stand for.

    The kind is not decoration: `plan()` reads it to decide what adopting ONE
    source brings.  A fixture that omits it is a station that plans nothing.
    """
    return StationInventory(hardware=frozenset(hardware),
                            sources=tuple(k for k, _ in pairs),
                            source_kinds=tuple(pairs))


def _identity(hostname="fargo-1", **kw):
    from sigmond.station_identity import StationIdentity
    return StationIdentity(hostname=hostname, dasi2_site=False, **kw)


def _args(name, tmp_path, dry_run=False, yes=True):
    return types.SimpleNamespace(name=name, dry_run=dry_run, yes=yes,
                                 topology=None, clients_root=str(tmp_path))


class _Started(list):
    """Records every call the adoption makes into the start machinery."""

    def __call__(self, start_args):
        self.append(list(getattr(start_args, "names", []) or []))
        return 0


def _wire(mod, inv, tmp_path, identity=None, adopted=frozenset(),
          may_elevate=True):
    """Point the module's seams at a fabricated station.  Returns the
    recorder standing in for `cmd_start` -- the real one drives systemctl.

    `may_elevate=False` makes `_need_root` FAIL the test if it is called, for
    the read-only paths: nothing that only reports may ask for sudo.  That
    property is the whole reason `adopt` stays out of main()'s `_MUTATING`
    set, and without this stub nothing would notice it regressing.
    """
    mod._station_inventory = lambda: inv
    mod._adopted_sources = lambda **kw: adopted
    mod._station_identity = lambda: identity or _identity()
    # Never elevate for real in a test: _need_root() os.execvp()s sudo and
    # would replace the pytest process.
    if may_elevate:
        mod._need_root = lambda _name: False
    else:
        def _forbidden(_name):
            raise AssertionError(
                "_need_root called on a path that changes nothing")
        mod._need_root = _forbidden
    started = _Started()
    mod.cmd_start = started
    # The real preview probes live host hardware and the real catalog; the
    # fabricated stations here have neither.  `TestStartPreview` below covers
    # the preview itself.
    mod._adopt_start_preview = lambda comps: (list(comps), [])
    mod._radiod_already_running = lambda: False
    # The real lock file lives in /var/lib; take a no-op in tests but record
    # that it was entered, and when.
    @contextlib.contextmanager
    def _fake_lock(reason=""):
        started.append(f"lock:{reason}")
        yield
    mod.lifecycle_lock = _fake_lock
    return started


# ---------------------------------------------------------------------------
# Naming: adopt what the operator named, or refuse and say what exists
# ---------------------------------------------------------------------------

def test_adopting_an_unknown_name_fails_and_says_what_is_available(
        tmp_path, capsys):
    mod = _smd()
    _wire(mod, _station({"rx888"}, (RX, "rx888")),
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
    _wire(mod, _station({"rx888"}, (RX, "rx888")),
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
    _wire(mod, _station({"rx888"}, (RX, "rx888")),
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
    started = _wire(mod, _station({"rx888"}, (RX, "rx888")), tmp_path,
                    may_elevate=False)
    rc = mod.cmd_adopt(_args(str(RX), tmp_path, dry_run=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "ka9q-radio" in out                    # the plan is reported
    assert "dry run" in out.lower()
    assert not list(tmp_path.iterdir())           # ... and nothing happened
    assert started == []                          # ... and nothing started


def test_a_dasi2_alike_is_told_what_it_still_has_to_be_asked(
        tmp_path, capsys):
    """No roster entry means no grant identity -- ask, don't invent one."""
    mod = _smd()
    _wire(mod, _station({"rx888", "gpsdo", "magnetometer"}, (RX, "rx888")), tmp_path)
    rc = mod.cmd_adopt(_args("dasi2", tmp_path, dry_run=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "psws_station" in out
    assert "mag-recorder" in out and "gpsdo-monitor" in out


def test_a_rostered_dasi2_site_prefills_its_psws_identity(tmp_path, capsys):
    from sigmond.station_identity import StationIdentity
    mod = _smd()
    _wire(mod, _station({"rx888", "gpsdo", "magnetometer"}, (RX, "rx888")), tmp_path,
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
    _wire(mod, _station({"rx888"}, (RX, "rx888"), (RX2, "rx888")), tmp_path)
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
    _wire(mod, _station({"rx888"}, (RX, "rx888")),
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
    _wire(mod, _station({"rx888"}, (RX, "rx888")),
          tmp_path)
    assert mod.cmd_adopt(_args(str(RX), tmp_path)) == 0

    fresh = _smd()          # no stubs: the real reader, against the real files
    assert RX in fresh._adopted_sources(root=tmp_path)


def test_adopt_refuses_a_source_it_already_recorded(tmp_path):
    """End to end with no stubbed reader: adopt, then adopt again."""
    mod = _smd()
    inv = _station({"rx888"}, (RX, "rx888"))
    real_reader = mod._adopted_sources
    started = _wire(mod, inv, tmp_path)
    mod._adopted_sources = real_reader   # the real reader, on the real files
    assert mod.cmd_adopt(_args(str(RX), tmp_path)) == 0
    assert started
    assert mod.cmd_adopt(_args(str(RX), tmp_path)) != 0     # nothing left


# ---------------------------------------------------------------------------
# Adoption actually adopts -- and only after an explicit yes
# ---------------------------------------------------------------------------

class _Stdin:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


def test_adopt_enables_and_starts_what_the_offer_brings(tmp_path):
    """`smd adopt` is the verb that starts things.  It must hand the plan's
    components to the SAME start machinery `smd start` uses -- which carries
    the auto-enable, the requires closure and the radiod-first ordering --
    rather than printing a copy-paste line."""
    mod = _smd()
    started = _wire(mod, _station({"rx888"}, (RX, "rx888")), tmp_path)
    rc = mod.cmd_adopt(_args(str(RX), tmp_path, yes=True))
    assert rc == 0
    assert ["ka9q-radio", "ka9q-web", "igmp-querier"] in started


def test_the_lifecycle_lock_is_held_before_anything_starts(tmp_path):
    mod = _smd()
    started = _wire(mod, _station({"rx888"}, (RX, "rx888")), tmp_path)
    assert mod.cmd_adopt(_args(str(RX), tmp_path, yes=True)) == 0
    locks = [i for i, e in enumerate(started)
             if isinstance(e, str) and e.startswith("lock:")]
    assert locks, f"no lifecycle lock taken: {started}"
    assert locks[0] < started.index(["ka9q-radio", "ka9q-web", "igmp-querier"])


def test_declining_the_confirmation_starts_nothing(tmp_path, monkeypatch,
                                                   capsys):
    """The 2026-09-01 incident in one assertion: a unit nobody asked to run
    must not run."""
    mod = _smd()
    started = _wire(mod, _station({"rx888"}, (RX, "rx888")), tmp_path)
    monkeypatch.setattr("sys.stdin", _Stdin(True))
    monkeypatch.setattr("builtins.input", lambda _p="": "n")

    rc = mod.cmd_adopt(_args(str(RX), tmp_path, yes=False))
    assert rc == 0                       # the operator's choice, not an error
    assert started == []                 # nothing started
    assert not list(tmp_path.iterdir())  # nothing recorded either
    assert "nothing started" in capsys.readouterr().out.lower()


def test_confirming_at_the_prompt_adopts(tmp_path, monkeypatch):
    mod = _smd()
    started = _wire(mod, _station({"rx888"}, (RX, "rx888")), tmp_path)
    monkeypatch.setattr("sys.stdin", _Stdin(True))
    monkeypatch.setattr("builtins.input", lambda _p="": "y")

    assert mod.cmd_adopt(_args(str(RX), tmp_path, yes=False)) == 0
    assert ["ka9q-radio", "ka9q-web", "igmp-querier"] in started


def test_an_absent_terminal_is_never_read_as_consent(tmp_path, monkeypatch,
                                                     capsys):
    """A cron job or a pipe must not be able to start a station's services by
    saying nothing.  --yes is how a script consents."""
    mod = _smd()
    started = _wire(mod, _station({"rx888"}, (RX, "rx888")), tmp_path,
                    may_elevate=False)
    monkeypatch.setattr("sys.stdin", _Stdin(False))

    def _no_prompt(_p=""):
        raise AssertionError("prompted with no terminal")
    monkeypatch.setattr("builtins.input", _no_prompt)

    rc = mod.cmd_adopt(_args(str(RX), tmp_path, yes=False))
    assert rc != 0
    assert started == []
    assert not list(tmp_path.iterdir())
    assert "--yes" in capsys.readouterr().err


def test_yes_adopts_without_a_terminal(tmp_path, monkeypatch):
    """Scripted fleet provisioning: twenty DASI2 kits, no keyboard."""
    mod = _smd()
    started = _wire(mod, _station({"rx888", "gpsdo", "magnetometer"}, (RX, "rx888")), tmp_path)
    monkeypatch.setattr("sys.stdin", _Stdin(False))

    assert mod.cmd_adopt(_args("dasi2", tmp_path, yes=True)) == 0
    assert any(isinstance(e, list) and "ka9q-radio" in e for e in started)


def test_a_failing_start_is_reported_not_swallowed(tmp_path):
    mod = _smd()
    _wire(mod, _station({"rx888"}, (RX, "rx888")),
          tmp_path)
    mod.cmd_start = lambda _a: 3
    assert mod.cmd_adopt(_args(str(RX), tmp_path, yes=True)) != 0


# ---------------------------------------------------------------------------
# Selections are recorded only where something reads them
# ---------------------------------------------------------------------------

def test_only_source_consuming_components_get_a_selection(tmp_path):
    """ka9q-web serves radiod's status page and igmp-querier keeps multicast
    alive.  Neither consumes a source, so a selection file for them would be
    a stored fact nobody reads."""
    mod = _smd()
    _wire(mod, _station({"rx888"}, (RX, "rx888")),
          tmp_path)
    assert mod.cmd_adopt(_args(str(RX), tmp_path, yes=True)) == 0

    written = {p.name for p in tmp_path.iterdir()}
    assert written == {"ka9q-radio.sources.toml"}


def test_the_whole_kit_records_one_selection_per_consumer(tmp_path):
    from sigmond.sources import ClientSources
    mod = _smd()
    _wire(mod, _station({"rx888", "gpsdo", "magnetometer"}, (RX, "rx888"), (MAG, "magnetometer")), tmp_path)
    assert mod.cmd_adopt(_args("dasi2", tmp_path, yes=True)) == 0

    # Each key lands ONLY in the component that reads it.  This assertion used
    # to say `ka9q-radio` was told about the magnetometer and `mag-recorder`
    # about the RX888: adopting a kit looped components x sources.  A kit is
    # still ONE decision; it is just no longer one undifferentiated pile.
    written = {p.name for p in tmp_path.iterdir()}
    assert written == {"ka9q-radio.sources.toml", "mag-recorder.sources.toml"}
    assert ClientSources.load("ka9q-radio", root=tmp_path).selected == [RX]
    assert ClientSources.load("mag-recorder", root=tmp_path).selected == [MAG]


# ---------------------------------------------------------------------------
# Adopt what was named -- and start ONLY that
# ---------------------------------------------------------------------------

GPS = SourceKey(type="usb", identifier="1dd2:2211:mini01")


def test_adopting_the_gpsdo_does_not_start_radiod(tmp_path):
    """The Fargo box has an RX888 and a miniGPS.  Adopting the GPSDO alone
    must start gpsdo-monitor and NOTHING else.

    Until this fix, `_hardware_for` unioned every hardware kind on the station
    for any usb source, so this adoption would have started radiod -- a unit
    nobody named, which is precisely the 2026-09-01 failure.
    """
    mod = _smd()
    started = _wire(mod, _station({"rx888", "gpsdo"},
                                  (RX, "rx888"), (GPS, "gpsdo")), tmp_path)
    assert mod.cmd_adopt(_args(str(GPS), tmp_path, yes=True)) == 0

    assert started == [f"lock:adopt {GPS}", ["gpsdo-monitor"]]
    assert "ka9q-radio" not in str(started)
    assert {p.name for p in tmp_path.iterdir()} == {"gpsdo-monitor.sources.toml"}


def test_the_synthesised_kinds_survive_into_the_plan(tmp_path, monkeypatch):
    """End to end from the USB bus: a real `_station_inventory()` over a fake
    sysfs tree, then adopt the GPSDO.  The kind has to travel all the way from
    the vid:pid table into `plan()`, or the box starts what it was not asked
    to."""
    mod = _smd()
    _no_discovery(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_USB_SYSFS_ROOT",
                        _fake_sysfs(tmp_path, [
                            ("1-3", "1dd2", "2211", "mini01"),
                            ("1-4", "04b4", "00f1", "0009061C028B1629"),
                        ]))
    monkeypatch.setattr(mod, "_detect_local_sdr", lambda: True)
    monkeypatch.setattr(mod, "_detect_gpsdo", lambda: True)
    monkeypatch.setattr(mod, "_detect_magnetometer", lambda: False)

    inv = mod._station_inventory()
    assert dict(inv.source_kinds) == {
        SourceKey(type="usb", identifier="1dd2:2211:mini01"): "gpsdo",
        SourceKey(type="usb", identifier="04b4:00f1:0009061C028B1629"): "rx888",
    }

    from sigmond.adoption import offers, plan
    gps = next(o for o in offers(inv, frozenset()) if o.name == str(GPS))
    assert set(plan(gps, inv, _identity()).components) == {"gpsdo-monitor"}


def test_a_discovered_lan_radiod_carries_no_local_kind(tmp_path, monkeypatch):
    """A radiod on someone else's machine stands for no hardware here, and
    must not inherit a kind from the station it was found from."""
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

    inv = mod._station_inventory()
    assert inv.kind_of(FARGO) is None
    assert inv.kind_of(
        SourceKey(type="usb", identifier="04b4:00f1:0009072C56")) == "rx888"


# ---------------------------------------------------------------------------
# One prompt, in the right order, and never on a read-only path
# ---------------------------------------------------------------------------

def test_elevation_happens_before_the_prompt_not_after(tmp_path, monkeypatch):
    """`_need_root` re-execs the whole verb under sudo with the ORIGINAL argv,
    which carries no --yes.  Prompting first would ask the operator, re-exec,
    and ask again."""
    mod = _smd()
    _wire(mod, _station({"rx888"}, (RX, "rx888")), tmp_path)
    order = []
    mod._need_root = lambda _n: order.append("elevate") or False
    monkeypatch.setattr("sys.stdin", _Stdin(True))
    monkeypatch.setattr("builtins.input",
                        lambda _p="": (order.append("prompt"), "y")[1])

    assert mod.cmd_adopt(_args(str(RX), tmp_path, yes=False)) == 0
    assert order == ["elevate", "prompt"]


def test_declining_after_elevation_still_changes_nothing(tmp_path, monkeypatch):
    """Elevation precedes the prompt (above), so a decline cannot assert that
    sudo was never asked for.  What it CAN assert is the property that
    matters: saying no leaves the station exactly as it was."""
    mod = _smd()
    started = _wire(mod, _station({"rx888"}, (RX, "rx888")), tmp_path)
    monkeypatch.setattr("sys.stdin", _Stdin(True))
    monkeypatch.setattr("builtins.input", lambda _p="": "n")

    assert mod.cmd_adopt(_args(str(RX), tmp_path, yes=False)) == 0
    assert started == []
    assert not list(tmp_path.iterdir())


# ---------------------------------------------------------------------------
# The no-local-hardware station: a radiod on the LAN (design §2)
# ---------------------------------------------------------------------------

def test_a_lan_radiod_is_adoptable_and_brings_the_decode_clients(tmp_path):
    """`smd status` offered it and `smd adopt` refused it -- one of the two
    situations the spec names as forcing this whole design."""
    from sigmond.sources import KNOWN_CLIENTS, ClientSources
    mod = _smd()
    # No local hardware at all -- just a radiod discovered on the LAN.
    started = _wire(mod, StationInventory(sources=(FARGO,)), tmp_path)
    rc = mod.cmd_adopt(_args(str(FARGO), tmp_path, yes=True))
    assert rc == 0
    assert [list(KNOWN_CLIENTS)] == [e for e in started if isinstance(e, list)]
    assert ClientSources.load("wspr-recorder", root=tmp_path).selected == [FARGO]


def test_every_offer_status_advertises_is_one_adopt_accepts(tmp_path):
    """The two surfaces must agree.  A station that says "run `smd adopt X`"
    and then answers "nothing adoptable named X" is worse than silent."""
    mod = _smd()
    inv = StationInventory(
        hardware=frozenset({"rx888"}),
        sources=(RX, FARGO),
        source_kinds=((RX, "rx888"),))
    started = _wire(mod, inv, tmp_path)

    from sigmond.adoption import offers
    names = [o.name for o in offers(inv, frozenset())]
    assert str(FARGO) in names and str(RX) in names
    for name in names:
        mod._adopted_sources = lambda **kw: frozenset()
        assert mod.cmd_adopt(_args(name, tmp_path, dry_run=True)) == 0, name
    assert started == []


# ---------------------------------------------------------------------------
# Say what actually happened
# ---------------------------------------------------------------------------

def test_a_component_that_cannot_start_is_named_not_claimed(tmp_path, capsys):
    mod = _smd()
    _wire(mod, _station({"rx888"}, (RX, "rx888")), tmp_path)
    mod._adopt_start_preview = lambda comps: (
        ["ka9q-radio"],
        [("ka9q-web", "downloaded — needs: smd install ka9q-web")])

    assert mod.cmd_adopt(_args(str(RX), tmp_path, yes=True)) == 0
    out = capsys.readouterr().out
    assert "started ka9q-radio" in out
    assert "NOT started ka9q-web" in out


def test_nothing_startable_is_a_refusal_not_a_success(tmp_path, capsys):
    mod = _smd()
    started = _wire(mod, _station({"rx888"}, (RX, "rx888")), tmp_path)
    mod._adopt_start_preview = lambda comps: (
        [], [(c, "downloaded — needs: smd install") for c in comps])

    rc = mod.cmd_adopt(_args(str(RX), tmp_path, yes=True))
    assert rc != 0
    assert started == []
    assert not list(tmp_path.iterdir())
    assert "nothing here can start" in capsys.readouterr().err


def test_a_running_radiod_is_disclosed_before_the_operator_says_yes(
        tmp_path, monkeypatch, capsys):
    """`cmd_start` re-checks radiod's CPU pinning and restarts an instance
    whose placement no longer matches -- the class of event that took this
    station's timing chain down twice on 2026-09-01."""
    mod = _smd()
    _wire(mod, _station({"rx888"}, (RX, "rx888")), tmp_path)
    mod._radiod_already_running = lambda: True
    seen = {}
    monkeypatch.setattr("sys.stdin", _Stdin(True))

    def _prompt(_p=""):
        seen["out"] = capsys.readouterr().out
        return "y"
    monkeypatch.setattr("builtins.input", _prompt)

    assert mod.cmd_adopt(_args(str(RX), tmp_path, yes=False)) == 0
    assert "already running" in seen["out"]
    assert "restarts the instance" in seen["out"]


# ---------------------------------------------------------------------------
# Adopt refuses to act on a picture it could not read
# ---------------------------------------------------------------------------

def test_an_unreadable_station_is_a_clear_refusal_not_a_traceback(
        tmp_path, capsys):
    mod = _smd()
    _wire(mod, StationInventory(), tmp_path)

    def _broken():
        raise ValueError("/etc/sigmond/clients/psk-recorder.sources.toml: "
                         "'selected' must be an array of strings")
    mod._station_inventory = _broken

    rc = mod.cmd_adopt(_args("dasi2", tmp_path, dry_run=True))
    assert rc == 1
    err = capsys.readouterr().err
    assert "psk-recorder.sources.toml" in err
    assert "Traceback" not in err


class TestStartPreview:
    """`_adopt_start_preview` must agree with `cmd_start`'s own gate: it is
    what the operator is shown before consenting, and what adopt reports
    afterwards."""

    def _patch(self, monkeypatch, states, dormant=None):
        import sigmond.catalog as catalog
        import sigmond.component_state as component_state
        import sigmond.harmonize as harmonize

        class _S:
            def __init__(self, can_start, cloned=True):
                self.can_start = can_start
                self.cloned = cloned
                self.display_status = "downloaded — needs: smd install"

        monkeypatch.setattr(catalog, "load_catalog", lambda *a, **k: {})
        monkeypatch.setattr(component_state, "compute_state",
                            lambda n, t=None, alias=None: _S(states[n]))
        monkeypatch.setattr(harmonize, "dormant_reason",
                            lambda n, enabled=True: (dormant or {}).get(n))

    def test_an_installed_component_is_startable(self, monkeypatch):
        mod = _smd()
        self._patch(monkeypatch, {"ka9q-radio": True})
        assert mod._adopt_start_preview(["ka9q-radio"]) == (["ka9q-radio"], [])

    def test_an_uninstalled_component_is_skipped_with_its_reason(
            self, monkeypatch):
        mod = _smd()
        self._patch(monkeypatch, {"ka9q-radio": False})
        startable, skipped = mod._adopt_start_preview(["ka9q-radio"])
        assert startable == []
        assert skipped == [("ka9q-radio",
                            "downloaded — needs: smd install")]

    def test_absent_hardware_is_dormant_not_broken(self, monkeypatch):
        mod = _smd()
        self._patch(monkeypatch, {"mag-recorder": True},
                    dormant={"mag-recorder": "magnetometer"})
        startable, skipped = mod._adopt_start_preview(["mag-recorder"])
        assert startable == []
        assert "dormant" in skipped[0][1]


def test_a_running_radiod_is_disclosed_on_the_unattended_path_too(
        tmp_path, capsys):
    """`--yes` means "do not ASK me", not "do not TELL me".  An unattended
    fleet run that silently bounces a running radiod is the 2026-09-01 event
    itself, and the run's log is the only trace left afterwards."""
    mod = _smd()
    started = _wire(mod, _station({"rx888"}, (RX, "rx888")), tmp_path)
    mod._radiod_already_running = lambda: True

    assert mod.cmd_adopt(_args(str(RX), tmp_path, yes=True)) == 0
    out = capsys.readouterr().out
    assert "already running" in out
    assert "restarts the instance" in out
    assert started                      # ... and it did not gate the start


def test_no_radiod_in_the_plan_means_no_restart_warning(tmp_path, capsys):
    """Adopting a magnetometer must not warn about radiod, even on a station
    where radiod happens to be running."""
    mod = _smd()
    _wire(mod, _station({"magnetometer"}, (MAG, "magnetometer")), tmp_path)
    probed = []
    mod._radiod_already_running = lambda: probed.append(1) or True

    assert mod.cmd_adopt(_args(str(MAG), tmp_path, yes=True)) == 0
    out = capsys.readouterr().out
    assert "already running" not in out
    assert probed == []                 # not even asked -- no radiod planned


def test_a_stopped_radiod_is_not_announced_as_running(tmp_path, capsys):
    mod = _smd()
    _wire(mod, _station({"rx888"}, (RX, "rx888")), tmp_path)
    mod._radiod_already_running = lambda: False

    assert mod.cmd_adopt(_args(str(RX), tmp_path, yes=True)) == 0
    assert "already running" not in capsys.readouterr().out
