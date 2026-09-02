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
    lines = _smd()._adoption_section(_rx_station(), adopted=frozenset({RX}))
    assert str(RX) not in "\n".join(lines)


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
