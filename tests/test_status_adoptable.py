"""Status must show what is present but not adopted.

A station that has hardware attached and is doing nothing with it should say
so plainly.  Silence there is indistinguishable from having no hardware, and
this project has spent a day on the cost of that particular confusion.
"""

import importlib.util
import pytest

from sigmond.adoption import StationInventory
from sigmond.sources import SourceKey


def _smd():
    spec = importlib.util.spec_from_loader("smd_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = "bin/smd"
    exec(compile(open("bin/smd").read(), "bin/smd", "exec"), mod.__dict__)
    return mod


RX = SourceKey(type="usb", identifier="04b4:00f1:serial")


def test_an_unadopted_source_is_listed():
    lines = _smd()._adoption_section(
        StationInventory(hardware=frozenset({"rx888"}), sources=(RX,)),
        adopted=frozenset())
    text = "\n".join(lines)
    assert str(RX) in text
    assert "not adopted" in text.lower()


def test_an_adopted_source_is_not_offered():
    lines = _smd()._adoption_section(
        StationInventory(hardware=frozenset({"rx888"}), sources=(RX,)),
        adopted=frozenset({RX}))
    assert str(RX) not in "\n".join(lines)


def test_a_recognised_kit_is_named_as_one_offer():
    lines = _smd()._adoption_section(
        StationInventory(
            hardware=frozenset({"rx888", "gpsdo", "magnetometer"}),
            sources=(RX,)),
        adopted=frozenset())
    assert "dasi2" in "\n".join(lines).lower()


def test_nothing_adoptable_renders_nothing():
    lines = _smd()._adoption_section(
        StationInventory(), adopted=frozenset())
    assert lines == []
