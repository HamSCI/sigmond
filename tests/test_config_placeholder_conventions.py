"""Bring-up and the clients must agree on what "configured" means.

⛔ W3USR-019, 2026-09-18.  Bring-up logged

    » configure mag-recorder (non-interactive) — already configured
      (mag-recorder: /etc/mag-recorder/mag-recorder-config.toml present);
      skipping

and mag-recorder then refused to start on the very file sigmond had just
called configured:

    ERROR:mag_recorder.daemon:config still at template placeholders
    (station.psws_station_id=<YOUR_PSWS_STATION_ID>) — run
    `mag-recorder config init` ... exiting EX_CONFIG

Both were reading the same file and disagreeing about it, because the clients
do not share one placeholder spelling.  psk-recorder and meteor-scatter ship
`"<configure-via-config-init>"`; mag-recorder ships `"<YOUR_CALL>"` and
`"<YOUR_PSWS_STATION_ID>"`.  `_bringup_probe` matched only the first, so a
mag-recorder template read as finished work and the config-init that would
have filled in the callsign never ran — with `STATION_CALLSIGN=W3USR`
sitting in coordination.env, unused.

The probe now matches the *convention* — a TOML value that is entirely an
angle-bracketed token — which is the rule mag-recorder's own
`unconfigured_placeholders()` already applied.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_placeholders", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_placeholders", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()

# The real opening of mag-recorder's shipped template.  instrument_id ships
# EMPTY as of 2026-09-20: PSWS issues it, and defaulting it to the sensor
# model made an unconfigured station ship a well-formed wrong upload trigger
# (mag-recorder#9).  Empty is also what makes bring-up prompt for it.
MAG_TEMPLATE = '''\
[station]
psws_station_id  = "<YOUR_PSWS_STATION_ID>"  # e.g. "S000082"
instrument_id    = ""
callsign         = "<YOUR_CALL>"             # e.g. "AC0G"
grid_square      = "<YOUR_GRID>"
elevation_m      = 0.0
'''

# What W3USR-019 actually had: location filled in by the location authority,
# identity still a template.  This is the case that read as "configured".
# ⛔ Do NOT modernise the instrument_id here.  This fixture is a RECORD of a
# real file on a real station on 2026-09-18, and "RM3100" is what was in it.
# Editing it to match today's template would falsify the incident.
MAG_PARTIAL = '''\
[station]
psws_station_id  = "<YOUR_PSWS_STATION_ID>"  # e.g. "S000082"
instrument_id    = "RM3100"
callsign         = "<YOUR_CALL>"             # e.g. "AC0G"
grid_square      = "FN21ej"
latitude         = 41.4053820
longitude        = -75.6581743
'''

PSK_TEMPLATE = '''\
[radiod]
status = "<configure-via-config-init>"
'''

CONFIGURED = '''\
[station]
psws_station_id  = "S000082"
instrument_id    = "372"
callsign         = "W3USR"
grid_square      = "FN21ej"

[mag]
device           = "/dev/ttyMAG0"
i2c_address      = 0x23
description      = "RM3100 magnetometer via Pololu USB-I2C"
'''


class PlaceholderConventionTests(unittest.TestCase):

    def test_optional_placeholders_do_NOT_make_a_config_a_template(self):
        """⛔ The regression that killed a station.

        hf-timestd ships three OPTIONAL <YOUR_...> values (PSWS station id,
        instrument id, GNSS host).  A station with no PSWS enrolment and no
        external GNSS leaves them alone, and hf-timestd runs fine.  Matching
        them made its HARD checkpoint fail and aborted first-run bring-up on
        AI6VN's v3.47 install; ka9q-web and station-web never started.
        """
        hf_timestd_real = (
            '[station]\n'
            'id = "<YOUR_STATION_ID>"               # e.g., S000001\n'
            'instrument_id = "<YOUR_INSTRUMENT_ID>" # e.g., 172\n'
            'callsign = "AI6VN"\n'
            'grid = "CM87"\n\n'
            '[gnss]\n'
            'host = "<YOUR_GNSS_HOST>"              # optional\n')
        self.assertFalse(
            smd._config_is_placeholder(hf_timestd_real),
            "a configured hf-timestd with optional fields unset read as an "
            "unfilled TEMPLATE -- its hard checkpoint then aborts bring-up")

    def test_counting_placeholders_cannot_separate_the_cases(self):
        """Why this is not fixable by a threshold.

        mag-recorder had 2 placeholders among ~20 settings; hf-timestd has 3.
        Any count or fraction rule puts them on the same side.  The
        distinction is semantic -- which fields THAT client needs -- so the
        generic probe must not try to make it.
        """
        self.assertEqual(MAG_PARTIAL.count('"<'), 2)

    def test_the_original_sentinel_still_matches(self):
        self.assertTrue(smd._config_is_placeholder(PSK_TEMPLATE))

    def test_a_real_config_is_not_flagged(self):
        """Must not force --reconfig over a configured host's edits."""
        self.assertFalse(
            smd._config_is_placeholder(CONFIGURED),
            "a filled-in config was called a placeholder — bring-up would "
            "regenerate it and lose operator edits")

    def test_empty_text_is_not_a_placeholder(self):
        # _read_text_safe returns '' for an unreadable file; that is a
        # different condition and must not be reported as a template.
        self.assertFalse(smd._config_is_placeholder(""))

    def test_comparison_operators_are_not_mistaken_for_placeholders(self):
        """`<` in a real value must not trip the match."""
        for line in (
            'expr = "a < b > c"',          # angle brackets, not a lone token
            'note = "see <docs>, then go"',  # bracketed word mid-string
            'threshold = 5',
            'name = "<partial"',
        ):
            with self.subTest(line=line):
                self.assertFalse(
                    smd._config_is_placeholder(f"[x]\n{line}\n"),
                    f"false positive on: {line}")

    def test_a_bracketed_token_alone_is_NOT_enough(self):
        """The narrowing, stated as a rule.

        `<YOUR_CALL>` on its own does not make a config a template -- see
        test_optional_placeholders_do_NOT_make_a_config_a_template for the
        station this cost.  Only the explicit sentinel does.
        """
        self.assertFalse(smd._config_is_placeholder(
            '[station]\ncallsign = "<YOUR_CALL>"   # e.g. "AC0G"\n'))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
