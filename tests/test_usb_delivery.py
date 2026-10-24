"""`smd watch usb` — measuring what an RX888 actually delivers.

⛔ The failure this exists for is SILENT BY CONSTRUCTION.  An RX888 can send
fewer samples than its nominal rate while radiod's block-drop counter reads 0
(it only counts what radiod's own filters miss), the USB stack completes every
transfer with status 0, and the kernel logs nothing.  The WAVs are just short.
At KX4AZ-T one of three radios ran 4 % short for 11 h, with some 1-minute WAVs
13 % full, and nothing in any log said so.

So the only honest discriminator is delivered rate against nominal, read off
the bus, plus the status field to separate a sick radio from a sick cable.
Every test below is about one of the ways that arithmetic, or the joins that
feed it, could lie — because a wrong nominal turns a HEALTHY radio SHORT, and
a verdict nobody can trust is worse than no verdict.
"""
import importlib.util
import os
import unittest


def _smd():
    # See tests/test_adopt_cli.py: bin/smd re-execs at module scope unless
    # this is set, which would replace the running pytest process.
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    spec = importlib.util.spec_from_loader("smd_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = "bin/smd"
    exec(compile(open("bin/smd").read(), "bin/smd", "exec"), mod.__dict__)
    return mod


_SMD = _smd()


# A radiod conf carries TWO samprates and only one of them is the ADC's.
_CONF = """\
# comment line
[global]
hardware  = rx888
samprate  = 12000              # channel output rate the decoders ask for
mode      = usb

[rx888]
device      = "rx888"
description = "AI6VN RX-888 Mk2"
serial      = "0009042D028F1016"
samprate    = 129600000
"""

# KX4AZ-T's real line: the trailing comment carries digits of its own.
_CONF_COMMENTED_RATE = """\
[rx888]
samprate=64800000  # 2026-09-05: halved to 32.4 MHz BW (was 129600000)
"""


class ConfParsing(unittest.TestCase):
    """_radiod_conf_rx888 reads the ADC rate, not the channel rate."""

    def test_takes_the_rx888_samprate_not_the_global_one(self):
        # The [global] 12000 appears FIRST in the file.  Taking the first
        # match would make every radio read 5000 % over nominal.
        self.assertEqual(_SMD._radiod_conf_rx888(_CONF)["samprate"],
                         129600000)

    def test_serial_loses_its_quotes(self):
        self.assertEqual(_SMD._radiod_conf_rx888(_CONF)["serial"],
                         "0009042D028F1016")

    def test_trailing_comment_is_stripped_before_the_digits(self):
        # Strip non-digits first and this yields 648000002026090474242048.
        got = _SMD._radiod_conf_rx888(_CONF_COMMENTED_RATE)
        self.assertEqual(got["samprate"], 64800000)

    def test_a_commented_out_serial_is_not_a_serial(self):
        # sigmond writes the single-SDR case with the serial commented out
        # and says why.  Reading it anyway would bind the wrong radio.
        text = '[rx888]\n# serial = "DEADBEEF"\nsamprate = 64800000\n'
        self.assertEqual(_SMD._radiod_conf_rx888(text)["serial"], "")

    def test_keys_outside_the_rx888_section_are_ignored(self):
        text = 'serial = "LOOSE"\nsamprate = 1\n[rx888]\nsamprate = 64800000\n'
        got = _SMD._radiod_conf_rx888(text)
        self.assertEqual((got["samprate"], got["serial"]), (64800000, ""))

    def test_a_conf_with_no_rx888_section_reports_nothing(self):
        got = _SMD._radiod_conf_rx888("[global]\nsamprate = 12000\n")
        self.assertIsNone(got["samprate"])


def _lines(*rows):
    return list(rows)


class UsbmonParsing(unittest.TestCase):
    """_usbmon_parse counts completions of bulk-in, and nothing else."""

    WANT = {("2", "005")}

    def test_counts_bulk_in_completions(self):
        st = _SMD._usbmon_parse(_lines(
            "ffff 1000000 C Bi:2:005:1 0 524288 = aa",
            "ffff 1001000 C Bi:2:005:1 0 524288 = aa",
        ), self.WANT, 1.0)[("2", "005")]
        self.assertEqual((st["transfers"], st["bytes"], st["errors"]),
                         (2, 1048576, 0))

    def test_submissions_do_not_count(self):
        # An 'S' line says what was ASKED for.  This check is about what
        # arrived; counting submissions would report a sick radio healthy.
        st = _SMD._usbmon_parse(_lines(
            "ffff 1000000 S Bi:2:005:1 -115 524288 <",
            "ffff 1001000 C Bi:2:005:1 0 524288 = aa",
        ), self.WANT, 1.0)[("2", "005")]
        self.assertEqual(st["transfers"], 1)

    def test_other_transfer_types_do_not_count(self):
        st = _SMD._usbmon_parse(_lines(
            "ffff 1000000 C Ci:2:005:0 0 18 = aa",
            "ffff 1000500 C Bo:2:005:2 0 64 = aa",
        ), self.WANT, 1.0)[("2", "005")]
        self.assertEqual((st["transfers"], st["bytes"]), (0, 0))

    def test_another_device_on_the_same_bus_is_not_mine(self):
        # The KX4AZ-T fault was two radios on ONE bus.  Mixing them would
        # have averaged the sick one back to healthy.
        st = _SMD._usbmon_parse(_lines(
            "ffff 1000000 C Bi:2:006:1 0 524288 = aa",
        ), self.WANT, 1.0)[("2", "005")]
        self.assertEqual(st["transfers"], 0)

    def test_a_nonzero_status_is_an_error(self):
        st = _SMD._usbmon_parse(_lines(
            "ffff 1000000 C Bi:2:005:1 -71 0 = ",
            "ffff 1001000 C Bi:2:005:1 0 524288 = aa",
        ), self.WANT, 1.0)[("2", "005")]
        self.assertEqual((st["transfers"], st["errors"]), (2, 1))

    def test_longest_gap_is_between_successive_completions(self):
        # 89 ms was the sick radio at KX4AZ-T; 4 ms was its healthy sibling.
        st = _SMD._usbmon_parse(_lines(
            "ffff 1000000 C Bi:2:005:1 0 10 = aa",
            "ffff 1004000 C Bi:2:005:1 0 10 = aa",
            "ffff 1093000 C Bi:2:005:1 0 10 = aa",
            "ffff 1095000 C Bi:2:005:1 0 10 = aa",
        ), self.WANT, 1.0)[("2", "005")]
        self.assertEqual(st["max_gap_us"], 89000)

    def test_a_wanted_device_with_no_traffic_still_gets_a_row(self):
        # Silence is the finding, so it must not be a missing key.
        st = _SMD._usbmon_parse([], self.WANT, 1.0)
        self.assertEqual(st[("2", "005")]["transfers"], 0)

    def test_malformed_lines_do_not_raise(self):
        st = _SMD._usbmon_parse(_lines(
            "short line",
            "ffff notanumber C Bi:2:005:1 0 notanumber = aa",
            "ffff 1000000 C Bi 0 10 = aa",
        ), self.WANT, 1.0)[("2", "005")]
        self.assertEqual(st["bytes"], 0)


class Verdicts(unittest.TestCase):
    """_usb_delivery_verdict — the four answers, and their boundaries."""

    NOMINAL = 64_800_000

    def _st(self, msps, secs=10.0, errors=0, transfers=100):
        return {"transfers": transfers, "errors": errors,
                "bytes": int(msps * 1e6 * secs * 2), "max_gap_us": 0}

    def test_silence_is_no_data(self):
        v, _ = _SMD._usb_delivery_verdict(
            self._st(0, transfers=0), self.NOMINAL, 10.0)
        self.assertEqual(v, "NO DATA")

    def test_an_error_outranks_a_healthy_rate(self):
        v, _ = _SMD._usb_delivery_verdict(
            self._st(64.8, errors=1), self.NOMINAL, 10.0)
        self.assertEqual(v, "TRANSFER ERRORS")

    def test_an_error_outranks_a_SHORT_rate(self):
        # ⛔ The case that decides which box someone opens.  A failing cable
        # or port delivers short AND logs error statuses (-71 EPROTO, -75
        # EOVERFLOW).  A radio that is merely stuttering delivers short with
        # every status 0.  So the status check must come FIRST: order them
        # the other way and a cable fault reads SHORT, which says "the radio
        # itself is not sending" and sends someone to replace a good radio.
        # Testing this with a healthy rate alone passes either way.
        v, _ = _SMD._usb_delivery_verdict(
            self._st(10.0, errors=3), self.NOMINAL, 10.0)
        self.assertEqual(v, "TRANSFER ERRORS")

    def test_four_percent_short_is_short(self):
        # The measured KX4AZ-T fault: 53.96 Msps against 64.80 nominal.
        v, msps = _SMD._usb_delivery_verdict(
            self._st(53.96), self.NOMINAL, 10.0)
        self.assertEqual(v, "SHORT")
        self.assertAlmostEqual(msps, 53.96, places=2)

    def test_nominal_is_ok(self):
        v, _ = _SMD._usb_delivery_verdict(
            self._st(64.80), self.NOMINAL, 10.0)
        self.assertEqual(v, "ok")

    def test_just_inside_the_threshold_is_ok(self):
        # 98 % exactly.  A boundary that drifts turns healthy radios SHORT.
        v, _ = _SMD._usb_delivery_verdict(
            self._st(64.8 * 0.985), self.NOMINAL, 10.0)
        self.assertEqual(v, "ok")

    def test_just_outside_the_threshold_is_short(self):
        v, _ = _SMD._usb_delivery_verdict(
            self._st(64.8 * 0.97), self.NOMINAL, 10.0)
        self.assertEqual(v, "SHORT")

    def test_without_a_nominal_it_refuses_to_judge_the_rate(self):
        # No conf for this serial: report the rate, do not invent a verdict.
        v, _ = _SMD._usb_delivery_verdict(self._st(10.0), None, 10.0)
        self.assertEqual(v, "ok?")

    def test_without_a_nominal_silence_is_still_no_data(self):
        v, _ = _SMD._usb_delivery_verdict(
            self._st(0, transfers=0), None, 10.0)
        self.assertEqual(v, "NO DATA")

    def test_bytes_are_sixteen_bit_samples(self):
        # 2 bytes per sample.  Getting this wrong halves or doubles every
        # verdict on the table.
        _, msps = _SMD._usb_delivery_verdict(
            {"transfers": 1, "errors": 0, "bytes": 2_000_000,
             "max_gap_us": 0}, None, 1.0)
        self.assertAlmostEqual(msps, 1.0, places=6)


class SysfsEnumeration(unittest.TestCase):
    """_rx888_usb_devices finds the radios usbmon will name."""

    def _tree(self, tmp, devices):
        for name, fields in devices.items():
            d = tmp / name
            d.mkdir()
            for k, v in fields.items():
                (d / k).write_text(v + "\n")
        return _SMD._rx888_usb_devices(root=tmp)

    def test_finds_a_programmed_rx888(self):
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as t:
            got = self._tree(P(t), {
                "4-1.3": {"idVendor": "04b4", "idProduct": "00f1",
                          "serial": "0009042D028F1016",
                          "busnum": "4", "devnum": "5", "speed": "5000"},
            })
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["serial"], "0009042D028F1016")

    def test_devnum_is_zero_padded_the_way_usbmon_prints_it(self):
        # sysfs says 5, usbmon says 005.  An unpadded join silently matches
        # nothing and every radio reads NO DATA.
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as t:
            got = self._tree(P(t), {
                "4-1.3": {"idVendor": "04b4", "idProduct": "00f1",
                          "serial": "S", "busnum": "4", "devnum": "5"},
            })
        self.assertEqual(got[0]["key"], ("4", "005"))

    def test_an_unprogrammed_radio_is_not_listed(self):
        # 00f3 is the FX3's DFU identity: it moves no samples and has no
        # serial worth mapping.  Listing it would show a permanent NO DATA.
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as t:
            got = self._tree(P(t), {
                "2-1.3": {"idVendor": "04b4", "idProduct": "00f3",
                          "busnum": "2", "devnum": "7"},
            })
        self.assertEqual(got, [])

    def test_other_vendors_are_not_listed(self):
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as t:
            got = self._tree(P(t), {
                "2-1.2": {"idVendor": "1dd2", "idProduct": "2211",
                          "busnum": "2", "devnum": "4"},
            })
        self.assertEqual(got, [])

    def test_a_device_without_bus_numbers_is_skipped(self):
        # Interface directories carry idVendor but no busnum.
        import tempfile
        from pathlib import Path as P
        with tempfile.TemporaryDirectory() as t:
            got = self._tree(P(t), {
                "4-1.3:1.0": {"idVendor": "04b4", "idProduct": "00f1"},
            })
        self.assertEqual(got, [])

    def test_no_sysfs_at_all_is_empty_not_an_exception(self):
        self.assertEqual(
            _SMD._rx888_usb_devices(root="/nonexistent/usb/devices"), [])


class Joining(unittest.TestCase):
    """_usb_watch_rows binds a radio to its conf — or admits it cannot."""

    def _dev(self, serial, bus="2", dev="005"):
        return {"serial": serial, "bus": bus, "dev": dev,
                "key": (bus, dev), "speed": "5000", "path": "x"}

    def _stats(self, key, msps, secs=10.0):
        return {key: {"transfers": 100, "errors": 0,
                      "bytes": int(msps * 1e6 * secs * 2), "max_gap_us": 0}}

    def test_binds_by_serial(self):
        rows = _SMD._usb_watch_rows(
            [self._dev("AAA"), self._dev("BBB", dev="006")],
            [{"instance": "ns-bev", "serial": "AAA", "samprate": 64_800_000},
             {"instance": "ew-bev", "serial": "BBB", "samprate": 64_800_000}],
            {**self._stats(("2", "005"), 64.8),
             **self._stats(("2", "006"), 53.96)},
            10.0)
        got = {r["instance"]: r["verdict"] for r in rows}
        self.assertEqual(got, {"ns-bev": "ok", "ew-bev": "SHORT"})

    def test_one_radio_and_one_unbound_conf_fall_back_to_position(self):
        # sigmond's single-SDR conf leaves the serial commented out and says
        # "sole SDR: unbound by design".  Refusing to bind it would report
        # '?' on every ordinary station.
        rows = _SMD._usb_watch_rows(
            [self._dev("AAA")],
            [{"instance": "AI6VN", "serial": "", "samprate": 64_800_000}],
            self._stats(("2", "005"), 64.8), 10.0)
        self.assertEqual(rows[0]["instance"], "AI6VN")
        self.assertEqual(rows[0]["verdict"], "ok")

    def test_two_radios_and_one_unbound_conf_do_NOT_fall_back(self):
        # ⛔ The dangerous case.  Binding by position with more than one of
        # anything can hand a radio the wrong nominal, and a wrong nominal
        # turns a healthy radio SHORT — the one lie this table must not tell.
        rows = _SMD._usb_watch_rows(
            [self._dev("AAA"), self._dev("BBB", dev="006")],
            [{"instance": "only", "serial": "", "samprate": 64_800_000}],
            {**self._stats(("2", "005"), 64.8),
             **self._stats(("2", "006"), 10.0)},
            10.0)
        self.assertEqual([r["instance"] for r in rows], ["?", "?"])
        self.assertEqual([r["verdict"] for r in rows], ["ok?", "ok?"])

    def test_one_radio_and_two_confs_do_NOT_fall_back(self):
        rows = _SMD._usb_watch_rows(
            [self._dev("AAA")],
            [{"instance": "a", "serial": "", "samprate": 64_800_000},
             {"instance": "b", "serial": "", "samprate": 129_600_000}],
            self._stats(("2", "005"), 64.8), 10.0)
        self.assertEqual(rows[0]["instance"], "?")

    def test_a_serial_that_matches_wins_over_the_lone_fallback(self):
        rows = _SMD._usb_watch_rows(
            [self._dev("AAA")],
            [{"instance": "named", "serial": "AAA", "samprate": 64_800_000}],
            self._stats(("2", "005"), 64.8), 10.0)
        self.assertEqual(rows[0]["instance"], "named")

    def test_a_radio_with_no_usbmon_rows_reads_no_data(self):
        rows = _SMD._usb_watch_rows(
            [self._dev("AAA")],
            [{"instance": "a", "serial": "AAA", "samprate": 64_800_000}],
            {}, 10.0)
        self.assertEqual(rows[0]["verdict"], "NO DATA")

    def test_gap_is_reported_in_milliseconds(self):
        rows = _SMD._usb_watch_rows(
            [self._dev("AAA")],
            [{"instance": "a", "serial": "AAA", "samprate": 64_800_000}],
            {("2", "005"): {"transfers": 10, "errors": 0,
                            "bytes": 64_800_000 * 2 * 10,
                            "max_gap_us": 89_000}},
            10.0)
        self.assertAlmostEqual(rows[0]["gap_ms"], 89.0, places=3)


class Rendering(unittest.TestCase):
    """_render_usb_rows — the table says what the verdict is."""

    def _rows(self, verdict="SHORT"):
        return [{"serial": "AAA", "usb": "2:5", "instance": "ew-bev",
                 "msps": 53.96, "nominal": 64_800_000, "errors": 0,
                 "transfers": 1405, "gap_ms": 89.0, "verdict": verdict}]

    def test_the_verdict_appears(self):
        text = "\n".join(_SMD._render_usb_rows(self._rows(), 10.0))
        self.assertIn("SHORT", text)
        self.assertIn("ew-bev", text)

    def test_delivered_and_nominal_are_both_shown(self):
        # One number alone is not actionable: 53.96 is only a fault next to
        # the 64.80 it was supposed to be.
        text = "\n".join(_SMD._render_usb_rows(self._rows(), 10.0))
        self.assertIn("53.96", text)
        self.assertIn("64.80", text)

    def test_no_radios_says_so_rather_than_printing_an_empty_table(self):
        text = "\n".join(_SMD._render_usb_rows([], 10.0))
        self.assertIn("no programmed RX888", text)

    def test_an_unknown_nominal_renders_without_raising(self):
        rows = self._rows("ok?")
        rows[0]["nominal"] = None
        text = "\n".join(_SMD._render_usb_rows(rows, 10.0))
        self.assertIn("ok?", text)


class CliSurface(unittest.TestCase):
    """The verb is wired, and it is read-only."""

    def test_watch_usb_is_a_subverb(self):
        # bin/smd builds its parser inside main(), so the surface is checked
        # the way tests/test_docs_cli_table.py checks it: by running it.
        import os as _os
        import subprocess
        import sys as _sys
        from pathlib import Path as P
        repo = P(__file__).resolve().parents[1]
        env = dict(_os.environ, PYTHONPATH=str(repo / "lib"),
                   SIGMOND_NO_VENV_REEXEC="1")
        out = subprocess.run(
            [_sys.executable, str(repo / "bin" / "smd"), "watch", "usb",
             "--help"], capture_output=True, text=True, env=env,
            timeout=60).stdout
        self.assertIn("--once", out)
        self.assertIn("--seconds", out)

    def test_nothing_in_the_usb_watch_code_resets_a_port(self):
        # ⛔ At KX4AZ-T, cutting a live RX888's port in software to "test" it
        # wedged a HEALTHY sibling with -71 until its VBUS was pulled by
        # hand: port disable and xHCI rebind never remove VBUS.  This check
        # is an observer.  If it ever grows a recovery action, that action
        # does not belong in the same function as the measurement.
        import re
        src = open("bin/smd").read()
        start = src.index("def cmd_usb_watch(")
        end = src.index("def _add_usb_watch_args(")
        body = src[start:end]
        for forbidden in ("authorized", "unbind", "bind", "remove",
                          "/sys/bus/usb/drivers", "uhubctl", "reset"):
            self.assertNotRegex(
                body, re.compile(r"\b" + re.escape(forbidden) + r"\b"),
                f"cmd_usb_watch must not {forbidden} anything")


if __name__ == "__main__":
    unittest.main()


class DebugfsProbe(unittest.TestCase):
    """⛔ The readiness probe must ask ROOT, not the caller.

    `/sys/kernel/debug` is mode 0700 root:root.  For the unprivileged account
    smd runs as, `Path.is_dir()` on anything beneath it raises
    PermissionError — it does NOT return False.  The first draft of this
    command used `is_dir()` and died with a traceback on a healthy station
    (AI6VN, 2026-10-24) where the directory was present and perfectly
    readable by the identity that reads usbmon.  A unit test over a fake
    path cannot see this, so the guard is on the SHAPE of the probe.
    """

    def test_the_probe_runs_through_sudo(self):
        seen = []

        class _R:
            returncode = 0
            stderr = ""

        def _fake_run(cmd, **kw):
            seen.append((list(cmd), kw.get("sudo", False)))
            return _R()

        orig = _SMD._run
        try:
            _SMD._run = _fake_run
            self.assertTrue(_SMD._usbmon_dir_ready())
        finally:
            _SMD._run = orig
        self.assertEqual(len(seen), 1)
        cmd, sudo = seen[0]
        self.assertTrue(sudo, "the probe must elevate; 0700 root:root")
        self.assertEqual(cmd[:2], ["test", "-d"])
        self.assertTrue(cmd[2].endswith("usb/usbmon"))

    def test_no_unprivileged_stat_of_debugfs_anywhere_in_the_command(self):
        # The regression in one line: any Path(...).is_dir()/.exists() under
        # /sys/kernel/debug raises rather than answering.
        src = open("bin/smd").read()
        start = src.index("def _usbmon_dir_ready(")
        end = src.index("def cmd_usb_watch(")
        body = src[start:end]
        for forbidden in ("_USBMON_DIR.is_dir()", "_USBMON_DIR.exists()",
                          "node.exists()", "node.is_dir()"):
            self.assertNotIn(forbidden, body)

    def test_capture_asks_root_which_buses_are_readable(self):
        seen = []

        class _R:
            returncode = 0
            stderr = ""

        def _fake_run(cmd, **kw):
            seen.append(list(cmd))
            return _R()

        orig = _SMD._run
        try:
            _SMD._run = _fake_run
            got = _SMD._usbmon_buses(["2", "4", "2"])
        finally:
            _SMD._run = orig
        self.assertEqual(got, ["2", "4"])        # deduped and ordered
        self.assertTrue(all(c[:2] == ["test", "-r"] for c in seen))
