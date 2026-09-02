"""Status must show what is present but not adopted.

A station that has hardware attached and is doing nothing with it should say
so plainly.  Silence there is indistinguishable from having no hardware, and
this project has spent a day on the cost of that particular confusion.
"""

import importlib.util
import json
import os

import pytest

from sigmond.adoption import StationInventory
from sigmond.sources import SourceKey


def _smd():
    # bin/smd re-execs into <repo>/venv/bin/python (install.sh's layout,
    # distinct from this checkout's dev .venv/) at module scope, right after
    # __file__ becomes resolvable -- which our exec() below makes true for
    # the first time.  Left unset, importing this on an installed host would
    # replace the running pytest process with os.execv() mid-suite instead
    # of failing cleanly.  setdefault so a caller's own override wins.
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    spec = importlib.util.spec_from_loader("smd_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = "bin/smd"
    exec(compile(open("bin/smd").read(), "bin/smd", "exec"), mod.__dict__)
    return mod


RX = SourceKey(type="usb", identifier="04b4:00f1:serial")
KIWI = SourceKey(type="kiwisdr", identifier="192.0.2.7:8073")


def _rx_station():
    """A station that knows the RX888 key IS its rx888 -- as a real one does.
    Without the kind nothing claims the source, and `smd adopt` would refuse
    it; the section says so rather than advertising the verb."""
    return StationInventory(hardware=frozenset({"rx888"}), sources=(RX,),
                            source_kinds=((RX, "rx888"),))


def test_an_unadopted_source_is_listed():
    lines = _smd()._adoption_section(_rx_station(), adopted=frozenset())
    text = "\n".join(lines)
    assert str(RX) in text
    assert "not adopted" in text.lower()
    assert "smd adopt" in text


def test_an_adopted_source_is_not_offered():
    """It is not OFFERED again -- but it is still named, under `adopted:`.

    The assertion here used to be `str(RX) not in text`, which was true of a
    section that said nothing at all about an adopted device.  Spec section 6
    asks for detected, adopted and adoptable; a device that vanishes from
    `smd status` the moment it is claimed leaves no record it was ever
    claimed, which is the silence this section exists to end.
    """
    text = "\n".join(_smd()._adoption_section(_rx_station(),
                                              adopted=frozenset({RX})))
    assert "adoptable:" not in text
    assert "run `smd adopt" not in text


def test_an_adopted_source_is_still_named_as_adopted():
    text = "\n".join(_smd()._adoption_section(_rx_station(),
                                              adopted=frozenset({RX})))
    assert "adopted:" in text
    assert str(RX) in text


def test_an_adopted_device_that_is_gone_says_so():
    """Adopted and no longer on the bus is worth saying: the selection is
    still recorded, and the component still reads it."""
    text = "\n".join(_smd()._adoption_section(
        StationInventory(), adopted=frozenset({RX})))
    assert str(RX) in text
    assert "not detected" in text.lower()


def test_the_section_refuses_to_advertise_a_verb_that_will_refuse():
    """The last place status and adopt could disagree.  `cmd_adopt` decides
    this station's identity before it will act and refuses a DASI-named host
    the roster does not list; the section that tells the operator to run it
    has to know that too."""
    from sigmond.station_identity import UnrosteredDasiName

    mod = _smd()

    def _refuse():
        raise UnrosteredDasiName(
            "hostname 'DASI019' matches the DASI fleet pattern but is not in "
            "the roster")
    mod._station_identity = _refuse

    text = "\n".join(mod._adoption_section(_rx_station(),
                                           adopted=frozenset()))
    assert str(RX) in text                 # the device is still reported
    assert "run `smd adopt" not in text    # ... but the verb is not advertised
    assert "DASI019" in text               # ... and the reason is named


def test_a_source_nothing_claims_is_shown_without_an_adopt_instruction():
    """A KiwiSDR is real and worth reporting -- silence is the confusion this
    section exists to end.  But no component consumes one yet, so `smd adopt`
    would refuse it, and telling the operator to run it would be a lie."""
    lines = _smd()._adoption_section(
        StationInventory(sources=(KIWI,)), adopted=frozenset())
    text = "\n".join(lines)
    assert str(KIWI) in text                 # still reported
    assert "smd adopt" not in text           # but not advertised
    assert "consumes this source yet" in text


def test_a_claimable_and_an_unclaimable_source_are_told_apart():
    lines = _smd()._adoption_section(
        StationInventory(hardware=frozenset({"rx888"}), sources=(RX, KIWI),
                         source_kinds=((RX, "rx888"),)),
        adopted=frozenset())
    text = "\n".join(lines)
    adopt_at = text.index("run `smd adopt")
    assert text.index(str(RX)) < adopt_at        # the instruction follows RX
    assert text.index(str(KIWI)) > adopt_at      # ... and not the KiwiSDR


def test_a_recognised_kit_is_named_as_one_offer():
    lines = _smd()._adoption_section(
        StationInventory(
            hardware=frozenset({"rx888", "gpsdo", "magnetometer"}),
            sources=(RX,),
            # A kit claims the LOCAL devices it is made of, so the fixture has
            # to say which device this key is -- as a real station does.
            source_kinds=((RX, "rx888"),)),
        adopted=frozenset())
    assert "dasi2" in "\n".join(lines).lower()


def test_nothing_adoptable_renders_nothing():
    lines = _smd()._adoption_section(
        StationInventory(), adopted=frozenset())
    assert lines == []


class _StatusArgs:
    """Minimal stand-in for the argparse Namespace `cmd_status` reads."""

    def __init__(self, topology_path):
        self.topology = str(topology_path)
        self.names = []
        self.components = None


def test_status_survives_a_malformed_discovery_cache(tmp_path, monkeypatch, capsys):
    """A discovery cache that parses as JSON but has the wrong SHAPE must not
    take `smd status` down with it.

    `load_cache()` already tolerates missing files and unparseable JSON --
    this is the other failure mode: valid JSON whose 'observations' entries
    don't rehydrate cleanly (`dict_to_obs` raises deep inside the adoption
    wiring, not inside `load_cache`).  The `try/except` around the adoption
    call site in `cmd_status` is what has to catch this, and a future edit
    that narrows that except or moves a line outside the try would
    reintroduce a crash with nothing here to catch it -- so this asserts on
    the *surviving* output, not merely that nothing escaped.
    """
    bad_cache = tmp_path / "environment-cache.json"
    bad_cache.write_text(json.dumps({
        "probed_at": 0.0,
        "observations": [{
            "source": "mdns", "kind": "radiod", "id": "r1",
            "endpoint": "host:5006", "fields": {},
            # Wrong shape: observed_at must be numeric: dict_to_obs()'s
            # float(...) raises ValueError on this, inside the
            # rehydrate/inventory path -- never inside load_cache().
            "observed_at": "not-a-number",
            "ok": True, "error": "",
        }],
        "deltas": [],
    }))

    mod = _smd()

    import sigmond.discovery as discovery
    monkeypatch.setattr(discovery, "cache_path", lambda: bad_cache)

    # No topology file at this path -> nothing enabled, no systemctl calls;
    # isolates the assertion to the adoption wiring rather than host state.
    args = _StatusArgs(tmp_path / "topology.toml")

    rc = mod.cmd_status(args)

    out = capsys.readouterr().out
    assert isinstance(rc, int)          # cmd_status returned, didn't raise
    assert "status" in out              # other status content still rendered
    assert "adoptable" not in out.lower()  # the broken section rendered nothing


def test_the_adopt_instruction_does_not_promise_configuration():
    """`cmd_adopt` records sources and starts components.  It configures
    nothing -- `_apply_sources_to_wspr_recorder` is reachable only from
    `smd admin sources apply` -- so this line may not say it does."""
    text = "\n".join(_smd()._adoption_section(_rx_station(),
                                              adopted=frozenset()))
    assert "smd adopt" in text
    assert "configure" not in text.lower()


# ---------------------------------------------------------------------------
# `smd status` is the verb you run BECAUSE something is wedged
# ---------------------------------------------------------------------------

def test_the_adoption_probe_is_bounded():
    """`_station_inventory()` probes hardware by running `<client> inventory
    --json`, which `sigmond.hardware` bounds at 15 s EACH.  With
    gpsdo-monitor and mag-recorder both on PATH -- a real station -- `smd
    status` could block ~30 s, on exactly the wedged client the operator ran
    `smd status` to find.  The existing try/except catches errors, not
    latency."""
    import time
    mod = _smd()

    def _hang():
        time.sleep(30)
    mod._adoption_inventory = _hang

    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        mod._adoption_inventory_bounded(deadline=0.2)
    assert time.monotonic() - t0 < 5


def test_the_bounded_probe_returns_the_same_picture():
    mod = _smd()
    inv, adopted = _rx_station(), frozenset({RX})
    mod._adoption_inventory = lambda: (inv, adopted)
    assert mod._adoption_inventory_bounded(deadline=5) == (inv, adopted)


def test_the_bounded_probe_does_not_swallow_a_failure():
    """`cmd_status` decides how to degrade; the wrapper must not decide for
    it (a broken selection file still has to reach the existing handler)."""
    mod = _smd()

    def _boom():
        raise ValueError("bad selection file")
    mod._adoption_inventory = _boom
    with pytest.raises(ValueError):
        mod._adoption_inventory_bounded(deadline=5)


def test_status_says_the_probe_timed_out_rather_than_going_quiet(
        tmp_path, capsys):
    """Timing out is a fact about the station, not a reason for silence."""
    import time
    mod = _smd()
    mod._ADOPTION_PROBE_DEADLINE = 0.2
    mod._adoption_inventory = lambda: time.sleep(30)

    t0 = time.monotonic()
    rc = mod.cmd_status(_StatusArgs(tmp_path / "topology.toml"))
    assert time.monotonic() - t0 < 10
    assert isinstance(rc, int)
    out = capsys.readouterr().out.lower()
    assert "adoption" in out
    assert "did not answer" in out
