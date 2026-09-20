"""The instrument-id prompt has to teach the right answer.

PSWS issues an instrument id per instrument — a short number, `372` on
AC0G-B4, `131` in the shared-key fixture. It rides in the upload trigger:

    m<dataset>_#<instrument_id>_#<upload-time>

so a wrong value uploads successfully and lands somewhere PSWS cannot
match it to an instrument. Nothing on our side errors.

Two things made that easy to get wrong, and both were in the prompt:

1. The station-id prompt carried `(e.g. S000082)`; the instrument-id
   prompt carried no example at all, so nothing told an operator the
   field wants a number rather than a nickname or a sensor model.
2. For mag-recorder the prompt OFFERED `RM3100` as its default, so
   pressing Enter — the thing an unsure operator does — wrote the sensor
   model. The wizard taught the wrong answer and then accepted it.

`RM3100` is also the shipped template's value, and `upload_creds`
decides whether to prompt for credentials at all with `_is_placeholder()`,
true only for empty or `<YOUR_…>`. So the model name reads as configured
and suppresses the prompt on a station that configured nothing. See
mag-recorder issue #9.
"""
from __future__ import annotations

import inspect
import re

from sigmond import psws


def _instrument_prompt_source() -> str:
    """The `instrument = _prompt(...)` call, as written."""
    src = inspect.getsource(psws.cmd_edit)
    m = re.search(r"instrument\s*=\s*_prompt\((.*?)\)\n", src, re.S)
    assert m, "could not find the instrument prompt in cmd_edit"
    return m.group(1)


def test_prompt_says_the_id_is_a_number():
    """An operator who has never seen a PSWS id must learn the shape."""
    label = _instrument_prompt_source().lower()
    assert "digit" in label or re.search(r"e\.g\.?\s*\d", label), (
        "the instrument prompt gives no hint that a PSWS id is a short "
        f"number: {label!r}")


def test_prompt_does_not_offer_the_sensor_model_as_a_default():
    """Pressing Enter must not write RM3100."""
    assert "RM3100" not in _instrument_prompt_source(), (
        "the wizard still offers the sensor model as the default "
        "instrument id; Enter would write it")


def test_station_prompt_still_carries_its_example():
    """Guard the sibling prompt, so a fix here does not cost that one."""
    src = inspect.getsource(psws.cmd_edit)
    m = re.search(r"station\s*=\s*_prompt\((.*?)\)\n", src, re.S)
    assert m and "S000082" in m.group(1)
