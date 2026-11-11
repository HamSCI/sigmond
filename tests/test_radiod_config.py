"""Tests for `smd config init|edit radiod` (CONTRACT-v0.5 §14.4)."""

from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond.commands import client_config, radiod_config
from sigmond.environment import Observation


def _ns(**kwargs):
    base = dict(non_interactive=True, reconfig=False,
                instance=None, client="radiod")
    base.update(kwargs)
    return SimpleNamespace(**base)


def _sdr(sdr_type: str, serial: str = "", index: int = 0,
         bus: str = "003", device: str = "005",
         vid: str = "04b4", pid: str = "00bc") -> Observation:
    return Observation(
        source="usb_sdr", kind="sdr",
        id=f"usb:{vid}:{pid}:{index}",
        endpoint=f"bus {bus} dev {device}",
        fields={
            "sdr_type": sdr_type,
            "chip":     "Cypress FX3" if "RX-888" in sdr_type else "",
            "vid":      vid, "pid": pid,
            "bus":      bus, "device": device,
            "index":    index, "serial": serial,
            "usb_name": "",
        },
        observed_at=0.0, ok=True,
    )


# ---------------------------------------------------------------------------
# Profile lookup
# ---------------------------------------------------------------------------

class ProfileLookupTests(unittest.TestCase):
    def test_rx888_profile(self):
        p = radiod_config._profile_for("RX-888")
        self.assertEqual(p["section"], "rx888")
        self.assertIn("samprate", p["defaults"])

    def test_every_rx888_profile_sets_queuedepth_64(self):
        """radiod's built-in queuedepth of 16 holds only ~32 ms of URB ring.
        A KVM guest overruns that often enough to starve the RX888 into
        silence — AC0G-ND stalled every ~13 minutes on 2026-09-03 and lost
        every WSPR and FT8 decode for a day, while b4 (queuedepth 64, set by
        hand since the v3.31 soak) ran clean.  Pin the generator to 64 so no
        greenfield station ships on the 32 ms cliff again.
        """
        for label in ("RX888", "RX-888", "RX-888 Mk2"):
            with self.subTest(frontend=label):
                defaults = radiod_config._profile_for(label)["defaults"]
                self.assertIn("queuedepth  = 64", defaults)

    def test_non_rx888_profile_omits_queuedepth(self):
        """queuedepth names an RX888 driver knob; other front ends reject it."""
        for label in ("Airspy", "Airspy HF+", "SDRplay"):
            with self.subTest(frontend=label):
                self.assertNotIn(
                    "queuedepth", radiod_config._profile_for(label)["defaults"])

    def test_airspy_profile(self):
        p = radiod_config._profile_for("Airspy")
        self.assertEqual(p["section"], "airspy")

    def test_unknown_profile_falls_back(self):
        p = radiod_config._profile_for("MysteryRadio")
        self.assertEqual(p["section"], "frontend")


# ---------------------------------------------------------------------------
# Serial formatting
# ---------------------------------------------------------------------------

class SerialFormatTests(unittest.TestCase):
    def test_strip_leading_0x(self):
        self.assertEqual(
            radiod_config._format_serial("Airspy", "0x62CC68FF21146A17"),
            "62CC68FF21146A17",
        )

    def test_pass_through_bare_hex(self):
        self.assertEqual(
            radiod_config._format_serial("RX-888", "0123456789ABCDEF"),
            "0123456789ABCDEF",
        )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

class RenderTests(unittest.TestCase):
    def _plan(self, **overrides) -> dict:
        base = {
            "instance_id": "bee1-rx888",
            "status_dns":  "bee1-rx888-status.local",
            "description": "AC0G T3FD",
            "frontend":    "rx888",
            "frontend_defaults": "samprate    = 64800000",
            "serial":      "0123456789ABCDEF",
            "serial_line": 'serial      = "0123456789ABCDEF"',
            "iface":       "eth0",
            "target":      Path("/etc/radio/radiod@bee1-rx888.conf"),
            "sdr_type":    "RX-888",
        }
        base.update(overrides)
        return base

    def test_renders_all_substitutions(self):
        body = radiod_config._render(self._plan())
        self.assertIn("[global]", body)
        self.assertIn("hardware  = rx888", body)
        self.assertIn("status    = bee1-rx888-status.local", body)
        self.assertIn("[rx888]", body)
        self.assertIn('description = "AC0G T3FD"', body)
        self.assertIn('serial      = "0123456789ABCDEF"', body)
        self.assertIn("samprate    = 64800000", body)
        self.assertIn("iface     = eth0", body)

    def test_render_omits_serial_line_when_unknown(self):
        plan = self._plan(
            serial="",
            serial_line='# serial   = "<run with udev access for stable binding>"',
        )
        body = radiod_config._render(plan)
        self.assertIn("# serial", body)
        self.assertNotIn('serial      = "', body)

    def test_coordination_block_has_canonical_keys(self):
        block = radiod_config._coord_block(self._plan())
        self.assertIn('[radiod."bee1-rx888"]', block)
        self.assertIn('host        = "localhost"', block)
        self.assertIn('status_dns  = "bee1-rx888-status.local"', block)
        self.assertIn('radio_conf  = "/etc/radio/radiod@bee1-rx888.conf"', block)

    def test_coordination_block_carries_the_chosen_samprate(self):
        block = radiod_config._coord_block(self._plan(samprate_hz=64_800_000))
        self.assertIn("samprate_hz = 64800000", block)
        self.assertNotIn("# samprate_hz", block)

    def test_coordination_block_comments_samprate_when_unknown(self):
        block = radiod_config._coord_block(self._plan())
        self.assertIn("# samprate_hz", block)


# ---------------------------------------------------------------------------
# Coordination append
# ---------------------------------------------------------------------------

class AppendCoordinationTests(unittest.TestCase):
    def test_appends_when_block_absent(self):
        with tempfile.TemporaryDirectory() as d:
            coord = Path(d) / "coordination.toml"
            coord.write_text('[host]\ncall = "AC0G"\n')
            block = '[radiod."bee1-rx888"]\nhost = "localhost"\n'
            with mock.patch.object(radiod_config, "COORDINATION_PATH", coord):
                radiod_config._append_coordination([block], _ns())
            text = coord.read_text()
            self.assertIn('[host]', text)
            self.assertIn('[radiod."bee1-rx888"]', text)

    def test_skips_when_block_already_present(self):
        with tempfile.TemporaryDirectory() as d:
            coord = Path(d) / "coordination.toml"
            initial = (
                '[host]\ncall = "AC0G"\n'
                '\n[radiod."bee1-rx888"]\nhost = "localhost"\n'
            )
            coord.write_text(initial)
            block = '[radiod."bee1-rx888"]\nhost = "localhost"\n'
            with mock.patch.object(radiod_config, "COORDINATION_PATH", coord):
                radiod_config._append_coordination([block], _ns())
            self.assertEqual(coord.read_text(), initial)


# ---------------------------------------------------------------------------
# Init wizard end-to-end (non-interactive path)
# ---------------------------------------------------------------------------

class InitWizardTests(unittest.TestCase):
    def test_writes_one_config_per_sdr(self):
        with tempfile.TemporaryDirectory() as d:
            radio_dir = Path(d) / "radio"
            coord_path = Path(d) / "coordination.toml"
            sdrs = [
                _sdr("RX-888", serial="0123456789ABCDEF",
                     index=0, bus="003", device="005"),
                _sdr("Airspy HF+", serial="62CC68FF21146A17",
                     index=0, bus="003", device="008",
                     vid="03eb", pid="800c"),
            ]
            with mock.patch.object(radiod_config, "_discover_sdrs",
                                   return_value=sdrs), \
                 mock.patch.object(radiod_config, "RADIOD_CONFIG_DIR",
                                   radio_dir), \
                 mock.patch.object(radiod_config, "COORDINATION_PATH",
                                   coord_path), \
                 mock.patch.object(radiod_config, "_suggest_iface",
                                   return_value="eth0"), \
                 mock.patch("socket.gethostname",
                            return_value="bee1.local"):
                rc = radiod_config.cmd_radiod_init(_ns())

            self.assertEqual(rc, 0)
            written = sorted(p.name for p in radio_dir.glob("radiod@*.conf"))
            self.assertEqual(len(written), 2)
            # Default ids derive from hostname + sdr family.
            self.assertTrue(any("rx888" in n for n in written))
            self.assertTrue(any("airspyhfp" in n for n in written))
            # Each gets its own .conf.d/.
            for p in radio_dir.glob("radiod@*.conf"):
                self.assertTrue((radio_dir / f"{p.name}.d").is_dir())

            # coordination.toml gets one block per SDR.
            text = coord_path.read_text()
            self.assertEqual(text.count('[radiod."'), 2)
            # Each rendered config locks to the right serial.
            rx_text = (radio_dir / written[
                next(i for i, n in enumerate(written) if "rx888" in n)
            ]).read_text()
            self.assertIn('serial      = "0123456789ABCDEF"', rx_text)

    def test_sole_sdr_id_is_bare_hostname(self):
        """One SDR on the bus → the radiod id is the bare short hostname, so
        the mDNS status name is `<hostname>-status.local` with no SDR-family
        suffix (docs/install-redesign.md §4)."""
        with tempfile.TemporaryDirectory() as d:
            radio_dir = Path(d) / "radio"
            coord_path = Path(d) / "coordination.toml"
            sdrs = [_sdr("RX-888 Mk2", serial="0123456789ABCDEF",
                         index=0, bus="003", device="005")]
            with mock.patch.object(radiod_config, "_discover_sdrs",
                                   return_value=sdrs), \
                 mock.patch.object(radiod_config, "RADIOD_CONFIG_DIR",
                                   radio_dir), \
                 mock.patch.object(radiod_config, "COORDINATION_PATH",
                                   coord_path), \
                 mock.patch.object(radiod_config, "_suggest_iface",
                                   return_value="eth0"), \
                 mock.patch("socket.gethostname",
                            return_value="dasi2-13.local"):
                rc = radiod_config.cmd_radiod_init(_ns())

            self.assertEqual(rc, 0)
            written = [p.name for p in radio_dir.glob("radiod@*.conf")]
            self.assertEqual(written, ["radiod@dasi2-13.conf"])
            conf = (radio_dir / "radiod@dasi2-13.conf").read_text()
            self.assertIn("status    = dasi2-13-status.local", conf)
            coord = coord_path.read_text()
            self.assertIn('[radiod."dasi2-13"]', coord)
            self.assertIn('status_dns  = "dasi2-13-status.local"', coord)

    def test_no_sdrs_yields_actionable_error(self):
        with mock.patch.object(radiod_config, "_discover_sdrs",
                               return_value=[]):
            rc = radiod_config.cmd_radiod_init(_ns())
        self.assertEqual(rc, 1)

    def test_refuses_overwrite_without_reconfig(self):
        with tempfile.TemporaryDirectory() as d:
            radio_dir = Path(d) / "radio"
            radio_dir.mkdir()
            existing = radio_dir / "radiod@bee1-rx888.conf"
            existing.write_text("# existing\n")
            sdrs = [_sdr("RX-888", serial="abc")]
            with mock.patch.object(radiod_config, "_discover_sdrs",
                                   return_value=sdrs), \
                 mock.patch.object(radiod_config, "RADIOD_CONFIG_DIR",
                                   radio_dir), \
                 mock.patch.object(radiod_config, "COORDINATION_PATH",
                                   Path(d) / "coordination.toml"), \
                 mock.patch.object(radiod_config, "_suggest_iface",
                                   return_value="eth0"), \
                 mock.patch.object(radiod_config, "_default_instance_id",
                                   return_value="bee1-rx888"), \
                 mock.patch("socket.gethostname", return_value="bee1"):
                rc = radiod_config.cmd_radiod_init(_ns())
            self.assertEqual(rc, 1)
            self.assertEqual(existing.read_text(), "# existing\n")


# ---------------------------------------------------------------------------
# Dispatcher routing
# ---------------------------------------------------------------------------

class DispatcherRoutingTests(unittest.TestCase):
    def test_init_radiod_routes_to_radiod_config(self):
        called = {}

        def fake_init(args):
            called["init"] = True
            return 0

        ns = SimpleNamespace(client="radiod", instance=None)
        with mock.patch.object(radiod_config, "cmd_radiod_init", fake_init):
            rc = client_config.cmd_config_init(ns)
        self.assertEqual(rc, 0)
        self.assertTrue(called.get("init"))

    def test_edit_radiod_routes_to_radiod_config(self):
        called = {}

        def fake_edit(args):
            called["edit"] = True
            return 0

        ns = SimpleNamespace(client="radiod", instance="bee1")
        with mock.patch.object(radiod_config, "cmd_radiod_edit", fake_edit):
            rc = client_config.cmd_config_edit_client(ns)
        self.assertEqual(rc, 0)
        self.assertTrue(called.get("edit"))


if __name__ == "__main__":
    unittest.main()


class SoleSdrSerialBindingTests(unittest.TestCase):
    """Sole-SDR configs omit the serial binding (upstream ka9q review
    2026-07: serials belong in configs only on multi-SDR systems)."""

    class _Sdr:
        def __init__(self):
            self.fields = {"sdr_type": "RX-888 Mk2",
                           "serial": "0123456789ABCDEF"}

    class _Args:
        non_interactive = True

    def test_sole_sdr_serial_commented(self):
        plan = radiod_config._collect_per_sdr_values(
            self._Sdr(), self._Args(), "bee1", "eth0", sole=True)
        self.assertTrue(plan["serial_line"].lstrip().startswith("#"))
        self.assertIn("0123456789ABCDEF", plan["serial_line"])
        self.assertEqual(plan["serial"], "0123456789ABCDEF")

    def test_multi_sdr_serial_bound(self):
        plan = radiod_config._collect_per_sdr_values(
            self._Sdr(), self._Args(), "bee1", "eth0", sole=False)
        self.assertTrue(plan["serial_line"].startswith("serial"))


class MultiCardSerialTests(unittest.TestCase):
    """A host with more than one RX888 must pin every conf to a serial."""

    @staticmethod
    def _sdr(serial, bus="001", device="004"):
        return SimpleNamespace(fields={"sdr_type": "RX-888", "serial": serial,
                                       "bus": bus, "device": device,
                                       "index": 0})

    def test_two_cards_one_without_serial_refuses(self):
        with self.assertRaises(radiod_config.UnpinnedMultiCard):
            radiod_config._collect_per_sdr_values(
                self._sdr(""), _ns(non_interactive=True), "host", "eth0",
                known_ids=set(), sole=False)

    def test_sole_card_without_serial_still_renders_unpinned(self):
        plan = radiod_config._collect_per_sdr_values(
            self._sdr(""), _ns(non_interactive=True), "host", "eth0",
            known_ids=set(), sole=True)
        self.assertTrue(plan["serial_line"].startswith("# serial"))

    def test_init_exits_1_and_names_identify_when_refused(self):
        import contextlib
        import io
        sdrs = [self._sdr("AAAA1111", device="004"),
                self._sdr("", bus="001", device="005")]
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            conf_dir = Path(td) / "radio"
            with mock.patch.object(radiod_config, "RADIOD_CONFIG_DIR", conf_dir), \
                 mock.patch.object(radiod_config, "_discover_sdrs",
                                   return_value=sdrs), \
                 mock.patch.object(radiod_config, "load_coordination",
                                   side_effect=FileNotFoundError), \
                 mock.patch.object(radiod_config, "_suggest_iface",
                                   return_value="eth0"), \
                 mock.patch.object(radiod_config, "SYSFS_USB_ROOT",
                                   Path(td) / "no-sysfs"), \
                 contextlib.redirect_stdout(out), \
                 contextlib.redirect_stderr(out):
                rc = radiod_config.cmd_radiod_init(
                    _ns(non_interactive=True, force=False))
            self.assertEqual(rc, 1)
            self.assertIn("smd admin sdr list", out.getvalue())
            self.assertNotIn("once that exists", out.getvalue())
            self.assertEqual(list(conf_dir.glob("radiod@*.conf")), [])

    def test_sysfs_serial_fills_in_when_lsusb_gave_none(self):
        with tempfile.TemporaryDirectory() as td:
            sysfs = Path(td) / "sys"
            dev = sysfs / "1-2"
            dev.mkdir(parents=True)
            (dev / "busnum").write_text("1\n")
            (dev / "devnum").write_text("5\n")
            (dev / "serial").write_text("bbbb2222\n")
            with mock.patch.object(radiod_config, "SYSFS_USB_ROOT", sysfs):
                plan = radiod_config._collect_per_sdr_values(
                    self._sdr("", bus="001", device="005"),
                    _ns(non_interactive=True), "host", "eth0",
                    known_ids=set(), sole=False)
        self.assertEqual(plan["serial"], "BBBB2222")
        self.assertEqual(plan["serial_line"], 'serial      = "BBBB2222"')


class DfuBootstrapSerialTests(unittest.TestCase):
    """The bootstrap waits for a NEW programmed serial, not a DFU count drop."""

    @staticmethod
    def _dfu(index=0):
        return _sdr("RX-888 DFU", "", index=index, vid="04b4", pid="00f3")

    @staticmethod
    def _prog(serial, index=1):
        return _sdr("RX-888", serial, index=index, vid="04b4", pid="00f1")

    def _run(self, dfu_sdrs, script):
        """Run the bootstrap with discovery scripted; the last state repeats."""
        states = list(script)

        def discover():
            return states.pop(0) if len(states) > 1 else states[0]

        clock = iter(range(0, 100000, 5))
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(radiod_config, "RADIOD_CONFIG_DIR", Path(td)), \
             mock.patch.object(radiod_config, "_discover_sdrs", discover), \
             mock.patch("subprocess.run",
                        return_value=SimpleNamespace(returncode=0, stdout="",
                                                     stderr="")), \
             mock.patch("time.sleep"), \
             mock.patch("time.monotonic", lambda: next(clock)), \
             mock.patch.object(radiod_config, "err") as err_m:
            got = radiod_config._bootstrap_dfu_sdrs(dfu_sdrs, "eth0")
        return got, err_m

    def test_waits_for_a_new_programmed_serial_not_a_count_drop(self):
        a, x, y = self._dfu(), self._prog("0001"), self._prog("0002", 2)
        got, _ = self._run([a], [[a, x], [a, x], [x, y]])
        self.assertEqual(got, ["0002"])

    def test_dfu_count_drop_alone_is_not_success(self):
        a, x = self._dfu(), self._prog("0001")
        # The DFU card vanishes but no new serial appears.
        got, err_m = self._run([a], [[a, x], [x]])
        self.assertEqual(got, [])
        self.assertIn("did not come up with a serial", str(err_m.call_args_list))

    def test_two_cards_bootstrap_in_order_and_report_both(self):
        a, b = self._dfu(0), self._dfu(1)
        x, y, z = (self._prog("0001"), self._prog("0002", 2),
                   self._prog("0003", 3))
        got, _ = self._run([a, b], [[a, b, x], [a, b, x, y], [b, x, y],
                                    [b, x, y, z], [x, y, z]])
        self.assertEqual(got, ["0002", "0003"])

    def test_timeout_reports_the_cards_that_did_not_come_up(self):
        a, x = self._dfu(), self._prog("0001")
        got, err_m = self._run([a], [[a, x]])
        self.assertEqual(got, [])
        self.assertIn("did not come up with a serial", str(err_m.call_args_list))

    def test_new_programmed_serials_is_case_and_space_insensitive(self):
        now = [self._prog(" abc1 "), self._dfu(), self._prog("0001", 2)]
        self.assertEqual(radiod_config._new_programmed_serials({"0001"}, now),
                         {"ABC1"})
        self.assertEqual(radiod_config._new_programmed_serials({"ABC1"}, now),
                         {"0001"})


class SecondCardArrivesTests(unittest.TestCase):
    """C1: a host that grows from one RX888 to two pins the first conf too."""

    OLD_CONF = ('[global]\nstatus = host-status.local\n\n[rx888]\n'
                'device = "rx888"\n'
                '# serial  = "AAA"   # sole SDR: unbound by design; uncomment to pin\n')

    def _run(self, coord_serial, sdrs, extra_coord="", held=None,
             coord_text=None):
        import contextlib
        import io
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            radio_dir = Path(d) / "radio"
            radio_dir.mkdir()
            old = radio_dir / "radiod@host.conf"
            old.write_text(self.OLD_CONF)
            coord_path = Path(d) / "coordination.toml"
            serial_line = (f'sdr_serial  = "{coord_serial}"\n'
                           if coord_serial else "")
            if coord_text is None:
                coord_text = (
                    '[radiod."host"]\nhost        = "localhost"\n'
                    'status_dns  = "host-status.local"\n'
                    f'radio_conf  = "{old}"\n' + serial_line + extra_coord)
            coord_path.write_text(coord_text)
            with mock.patch.object(radiod_config, "_discover_sdrs",
                                   return_value=sdrs), \
                 mock.patch.object(radiod_config, "RADIOD_CONFIG_DIR",
                                   radio_dir), \
                 mock.patch.object(radiod_config, "COORDINATION_PATH",
                                   coord_path), \
                 mock.patch.object(radiod_config, "COORDINATION_ENV",
                                   Path(d) / "coordination.env"), \
                 mock.patch.object(radiod_config, "_suggest_iface",
                                   return_value="eth0"), \
                 mock.patch.object(radiod_config,
                                   "_apply_fragments_for_new_instances"), \
                 mock.patch.object(radiod_config, "_held_serials",
                                   return_value=dict(held or {})), \
                 mock.patch("socket.gethostname", return_value="host"), \
                 contextlib.redirect_stdout(out), \
                 contextlib.redirect_stderr(out):
                rc = radiod_config.cmd_radiod_init(_ns())
            confs = {p.name: p.read_text() for p in radio_dir.glob("radiod@*.conf")}
            self.coord_after = coord_path.read_text()
        return rc, out.getvalue(), confs

    def test_second_card_pins_the_formerly_sole_conf(self):
        rc, out, confs = self._run("AAA", [
            _sdr("RX-888", serial="AAA", index=0, device="005"),
            _sdr("RX-888", serial="BBB", index=1, device="006")])
        self.assertEqual(rc, 0, out)
        self.assertIn('serial      = "AAA"', confs["radiod@host.conf"])
        self.assertIn("radiod@host", out)
        new = [t for n, t in confs.items() if n != "radiod@host.conf"]
        self.assertEqual(len(new), 1)
        self.assertIn('serial      = "BBB"', new[0])

    def test_already_registered_second_card_still_repins(self):
        # Both serials already in coordination, radiod@host still unpinned.
        rc, out, confs = self._run("AAA", [
            _sdr("RX-888", serial="AAA", index=0, device="005"),
            _sdr("RX-888", serial="BBB", index=1, device="006")],
            extra_coord='\n[radiod."host-rx888-bbb"]\nhost = "localhost"\n'
                        'sdr_serial  = "BBB"\n')
        self.assertEqual(rc, 0, out)
        self.assertIn('serial      = "AAA"', confs["radiod@host.conf"])
        self.assertEqual(list(confs), ["radiod@host.conf"])

    def test_refuses_when_formerly_sole_conf_has_no_serial_anywhere(self):
        rc, out, confs = self._run("", [
            _sdr("RX-888", serial="AAA", index=0, device="005"),
            _sdr("RX-888", serial="BBB", index=1, device="006")])
        self.assertEqual(rc, 1)
        self.assertIn('serial = "<SERIAL>"', out)
        self.assertIn("radiod@host.conf", out)
        self.assertIn("smd admin sdr list", out)
        self.assertNotIn("identify", out)
        self.assertEqual(list(confs), ["radiod@host.conf"])
        self.assertEqual(confs["radiod@host.conf"], self.OLD_CONF)

    def test_sole_card_rerun_leaves_conf_unpinned(self):
        rc, out, confs = self._run("AAA", [
            _sdr("RX-888", serial="AAA", index=0, device="005")])
        self.assertEqual(rc, 0)
        self.assertEqual(confs["radiod@host.conf"], self.OLD_CONF)


class SecondCardOnRobsStationTests(unittest.TestCase):
    """fix2: coordination keyed by the status name, no sdr_serial; the
    running radiod holds the first card."""

    OLD_CONF = ('[global]\nstatus = AI6VN-status.local\n\n[rx888]\n'
                'device = "rx888"\n'
                '# serial  = "0009002807411037"   # sole SDR: unbound by design; '
                'uncomment to pin\n')
    CARDS = [_sdr("RX-888", serial="0009002807411037", index=0, device="016"),
             _sdr("RX-888", serial="0009061C028B352B", index=1, device="017")]
    COORD = ('[radiod."AI6VN-status.local"]\nhost = "localhost"\n'
             'radio_conf = "/etc/radio/radiod@AI6VN.conf"\n')

    def _rob(self, held):
        import contextlib
        import io
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            radio_dir = Path(d) / "radio"
            radio_dir.mkdir()
            (radio_dir / "radiod@AI6VN.conf").write_text(self.OLD_CONF)
            coord_path = Path(d) / "coordination.toml"
            coord_path.write_text(self.COORD)
            with mock.patch.object(radiod_config, "_discover_sdrs",
                                   return_value=[copy.deepcopy(s) for s in self.CARDS]), \
                 mock.patch.object(radiod_config, "RADIOD_CONFIG_DIR", radio_dir), \
                 mock.patch.object(radiod_config, "COORDINATION_PATH", coord_path), \
                 mock.patch.object(radiod_config, "COORDINATION_ENV",
                                   Path(d) / "coordination.env"), \
                 mock.patch.object(radiod_config, "_suggest_iface",
                                   return_value="eth0"), \
                 mock.patch.object(radiod_config,
                                   "_apply_fragments_for_new_instances"), \
                 mock.patch.object(radiod_config, "_held_serials",
                                   return_value=dict(held)), \
                 mock.patch("socket.gethostname", return_value="AI6VN"), \
                 contextlib.redirect_stdout(out), \
                 contextlib.redirect_stderr(out):
                rc = radiod_config.cmd_radiod_init(_ns())
            confs = {p.name: p.read_text() for p in radio_dir.glob("radiod@*.conf")}
            coord_after = coord_path.read_text()
        return rc, out.getvalue(), confs, coord_after

    def test_pins_from_the_held_card_and_registers_only_the_new_one(self):
        rc, out, confs, coord = self._rob({"AI6VN": "0009002807411037"})
        self.assertEqual(rc, 0, out)
        self.assertIn('serial      = "0009002807411037"',
                      confs["radiod@AI6VN.conf"])
        self.assertIn("pinned from the card radiod@AI6VN has open", out)
        self.assertTrue(coord.startswith(
            '[radiod."AI6VN-status.local"]\n'
            'sdr_serial  = "0009002807411037"\n'), coord)
        new = [t for n, t in confs.items() if n != "radiod@AI6VN.conf"]
        self.assertEqual(len(new), 1, list(confs))
        self.assertIn('serial      = "0009061C028B352B"', new[0])
        self.assertNotIn("0009002807411037", new[0])
        self.assertEqual(coord.count("[radiod."), 2, coord)

    def test_refuses_with_hand_pin_remedy_when_nothing_known(self):
        rc, out, confs, coord = self._rob({})
        self.assertEqual(rc, 1, out)
        self.assertIn('serial = "<SERIAL>"', out)
        self.assertIn("radiod@AI6VN.conf", out)
        self.assertIn("smd admin sdr list", out)
        self.assertNotIn("identify", out)
        self.assertEqual(list(confs), ["radiod@AI6VN.conf"])
        self.assertEqual(confs["radiod@AI6VN.conf"], self.OLD_CONF)
        self.assertEqual(coord, self.COORD)


# ---------------------------------------------------------------------------
# Per-CPU RX888 sample rate (lib/sigmond/cpu_profiles.py)
# ---------------------------------------------------------------------------

class CpuProfileSamprateTests(unittest.TestCase):
    """On a 5560U the first RX888 radiod runs 129.6 Msps and every other one
    64.8 Msps (KFS-OMNI, 2026-11-06: two at 129.6 saturate both FFT
    threads).  Unknown CPUs keep 129.6 everywhere."""

    CEZANNE = "AMD Ryzen 5 5560U with Radeon Graphics"

    def _run(self, model, sdrs, existing=None, coord_text="",
             timestd_text="", **ns):
        import contextlib
        import io
        from sigmond import cpu_profiles
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            radio_dir = Path(d) / "radio"
            radio_dir.mkdir()
            for name, text in (existing or {}).items():
                (radio_dir / name).write_text(text)
            coord_path = Path(d) / "coordination.toml"
            if coord_text:
                coord_path.write_text(coord_text.replace("@DIR@", str(radio_dir)))
            # Never the dev box's own /etc/hf-timestd.
            timestd_conf = Path(d) / "timestd-config.toml"
            if timestd_text:
                timestd_conf.write_text(timestd_text)
            with mock.patch.object(radiod_config, "_discover_sdrs",
                                   return_value=sdrs), \
                 mock.patch.object(radiod_config, "RADIOD_CONFIG_DIR",
                                   radio_dir), \
                 mock.patch.object(radiod_config, "COORDINATION_PATH",
                                   coord_path), \
                 mock.patch.object(radiod_config, "COORDINATION_ENV",
                                   Path(d) / "coordination.env"), \
                 mock.patch.object(radiod_config, "_suggest_iface",
                                   return_value="eth0"), \
                 mock.patch.object(radiod_config,
                                   "_apply_fragments_for_new_instances"), \
                 mock.patch.object(radiod_config, "_held_serials",
                                   return_value={}), \
                 mock.patch.object(radiod_config, "_host_cpu_profile",
                                   return_value=cpu_profiles.profile_for(model)), \
                 mock.patch.object(radiod_config, "HF_TIMESTD_CONF",
                                   timestd_conf), \
                 mock.patch("socket.gethostname", return_value="omni"), \
                 contextlib.redirect_stdout(out), \
                 contextlib.redirect_stderr(out):
                rc = radiod_config.cmd_radiod_init(_ns(**ns))
            confs = {p.name: p.read_text()
                     for p in radio_dir.glob("radiod@*.conf")}
            self.coord_text = (coord_path.read_text()
                               if coord_path.exists() else "")
        return rc, out.getvalue(), confs

    @staticmethod
    def _rate(text):
        from sigmond import sdr_map
        return sdr_map.conf_samprate_hz(text)

    def _two_cards(self):
        return [_sdr("RX-888 Mk2", serial="AAA", index=0, device="005"),
                _sdr("RX-888 Mk2", serial="BBB", index=1, device="006")]

    def test_5560u_two_new_cards_first_full_second_reduced(self):
        rc, out, confs = self._run(self.CEZANNE, self._two_cards())
        self.assertEqual(rc, 0, out)
        by_serial = {('AAA' if 'serial      = "AAA"' in t else 'BBB'): t
                     for t in confs.values()}
        self.assertEqual(self._rate(by_serial["AAA"]), 129_600_000)
        self.assertEqual(self._rate(by_serial["BBB"]), 64_800_000)
        self.assertIn("5560U", by_serial["BBB"])
        # Exactly one samprate line in the [rx888] section.
        rx = by_serial["BBB"].split("[rx888]", 1)[1]
        self.assertEqual(
            sum(1 for l in rx.splitlines() if l.startswith("samprate")), 1)

    def test_5560u_coordination_records_each_rate(self):
        # The frequency_coverage rule reads samprate_hz from coordination,
        # so init writes the rate it chose for each radiod.
        rc, out, confs = self._run(self.CEZANNE, self._two_cards())
        self.assertEqual(rc, 0, out)
        from sigmond.coordination import parse_coordination
        import tomllib
        coord = parse_coordination(tomllib.loads(self.coord_text))
        self.assertEqual(sorted(r.samprate_hz for r in coord.radiods.values()),
                         [64_800_000, 129_600_000])

    def test_unknown_cpu_keeps_129_6_for_both(self):
        rc, out, confs = self._run("Some Unknown CPU", self._two_cards())
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(self._rate(t) for t in confs.values()),
                         [129_600_000, 129_600_000])

    def test_long_comment_rx888_profile_also_drops_to_64_8(self):
        # The "RX888" profile's samprate line carries a multi-line comment;
        # the reduced rate must replace all of it, not leave a stale tail.
        sdrs = [_sdr("RX888", serial="AAA", index=0, device="005"),
                _sdr("RX888", serial="BBB", index=1, device="006")]
        rc, out, confs = self._run(self.CEZANNE, sdrs)
        self.assertEqual(rc, 0, out)
        bbb = next(t for t in confs.values() if 'serial      = "BBB"' in t)
        self.assertEqual(self._rate(bbb), 64_800_000)
        self.assertNotIn("Drop to", bbb)
        self.assertIn("queuedepth  = 64", bbb)

    def test_5560u_card_added_beside_existing_radiod_gets_reduced(self):
        old = ('[global]\nstatus = omni-status.local\n\n[rx888]\n'
               'device = "rx888"\nserial = "AAA"\nsamprate = 129600000\n')
        rc, out, confs = self._run(
            self.CEZANNE, self._two_cards(),
            existing={"radiod@omni.conf": old},
            coord_text=('[radiod."omni"]\nhost        = "localhost"\n'
                        'status_dns  = "omni-status.local"\n'
                        'radio_conf  = "@DIR@/radiod@omni.conf"\n'
                        'sdr_serial  = "AAA"\n'))
        self.assertEqual(rc, 0, out)
        self.assertEqual(confs["radiod@omni.conf"], old)
        new = [t for n, t in confs.items() if n != "radiod@omni.conf"]
        self.assertEqual(len(new), 1)
        self.assertEqual(self._rate(new[0]), 64_800_000)

    def test_reconfig_keeps_the_operators_samprate(self):
        # A hand-set rate survives --reconfig, even where the profile would
        # choose another.
        old = ('[global]\nstatus = omni-status.local\n\n[rx888]\n'
               'device = "rx888"\nsamprate = 64800000  # hand-set\n')
        rc, out, confs = self._run(
            "Some Unknown CPU",
            [_sdr("RX-888 Mk2", serial="AAA", index=0, device="005")],
            existing={"radiod@omni.conf": old}, reconfig=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._rate(confs["radiod@omni.conf"]), 64_800_000)

    def test_sole_card_on_5560u_is_full_rate(self):
        rc, out, confs = self._run(
            self.CEZANNE,
            [_sdr("RX-888 Mk2", serial="AAA", index=0, device="005")])
        self.assertEqual(rc, 0, out)
        self.assertEqual([self._rate(t) for t in confs.values()],
                         [129_600_000])

    def test_reconfig_keeps_a_ka9q_spelled_samprate(self):
        # `64.8M ; hand-set` is how radiod's parse_frequency reads 64.8 MHz
        # after iniparser cuts the comment.  It must survive --reconfig,
        # though the profile would give a sole card 129.6.
        old = ('[global]\nstatus = omni-status.local\n\n[rx888]\n'
               'device = "rx888"\nsamprate = 64.8M ; hand-set\n')
        rc, out, confs = self._run(
            self.CEZANNE,
            [_sdr("RX-888 Mk2", serial="AAA", index=0, device="005")],
            existing={"radiod@omni.conf": old}, reconfig=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._rate(confs["radiod@omni.conf"]), 64_800_000)

    def test_reconfig_keeps_an_unparseable_samprate_verbatim(self):
        # A samprate sigmond cannot read is still the operator's: keep the
        # value as written and say so, never recompute it.
        old = ('[global]\nstatus = omni-status.local\n\n[rx888]\n'
               'device = "rx888"\nsamprate = $RATE\n')
        rc, out, confs = self._run(
            self.CEZANNE,
            [_sdr("RX-888 Mk2", serial="AAA", index=0, device="005")],
            existing={"radiod@omni.conf": old}, reconfig=True)
        self.assertEqual(rc, 0, out)
        from sigmond import sdr_map
        self.assertEqual(sdr_map.conf_samprate_raw(confs["radiod@omni.conf"]),
                         "$RATE")
        self.assertIn("$RATE", out)
        self.assertIn("cannot read", out)


class FullRateRadiodChoiceTests(unittest.TestCase):
    """Which RX888 radiod keeps 129.6 Msps on a 5560U.  hf-timestd needs it
    (the TS-1 T6 pilot's alias plan is built for 129.6 Msps), so the
    radiod hf-timestd is bound to wins, then one the operator names; bus
    order decides only when neither is known, and then says so loudly."""

    CEZANNE = CpuProfileSamprateTests.CEZANNE
    _run = CpuProfileSamprateTests._run
    _rate = staticmethod(CpuProfileSamprateTests._rate)
    _two_cards = CpuProfileSamprateTests._two_cards

    # Non-interactive ids for _two_cards(): AAA is index 0, BBB index 1.
    AAA_ID, BBB_ID = "omni-rx888mk2", "omni-rx888mk2-1"
    OLD = ('[global]\nstatus = omni-status.local\n\n[rx888]\n'
           'device = "rx888"\nserial = "AAA"\nsamprate = 129600000\n')
    OLD_COORD = ('[radiod."omni"]\nhost        = "localhost"\n'
                 'status_dns  = "omni-status.local"\n'
                 'radio_conf  = "@DIR@/radiod@omni.conf"\n'
                 'sdr_serial  = "AAA"\n')

    def _by_serial(self, confs):
        out = {}
        for t in confs.values():
            for s in ("AAA", "BBB"):
                if f'serial      = "{s}"' in t or f'serial = "{s}"' in t:
                    out[s] = t
        return out

    @staticmethod
    def _timestd_binding(radiod_id):
        return ('[[clients.hf-timestd]]\ninstance = "default"\n'
                f'radiod_id = "{radiod_id}"\n')

    def test_hf_timestd_binding_gives_the_second_card_full_rate(self):
        rc, out, confs = self._run(
            self.CEZANNE, self._two_cards(),
            coord_text=self._timestd_binding(self.BBB_ID))
        self.assertEqual(rc, 0, out)
        by = self._by_serial(confs)
        self.assertEqual(self._rate(by["BBB"]), 129_600_000)
        self.assertEqual(self._rate(by["AAA"]), 64_800_000)
        self.assertNotIn("bus order", out)

    def test_hf_timestd_binding_by_status_name(self):
        rc, out, confs = self._run(
            self.CEZANNE, self._two_cards(),
            coord_text=self._timestd_binding(f"{self.BBB_ID}-status.local"))
        self.assertEqual(rc, 0, out)
        by = self._by_serial(confs)
        self.assertEqual(self._rate(by["BBB"]), 129_600_000)
        self.assertEqual(self._rate(by["AAA"]), 64_800_000)

    def test_hf_timestd_config_status_address(self):
        rc, out, confs = self._run(
            self.CEZANNE, self._two_cards(),
            timestd_text=('[ka9q]\nstatus_address = '
                          f'"{self.BBB_ID}-status.local"\n'))
        self.assertEqual(rc, 0, out)
        by = self._by_serial(confs)
        self.assertEqual(self._rate(by["BBB"]), 129_600_000)
        self.assertEqual(self._rate(by["AAA"]), 64_800_000)

    def test_operator_names_the_full_rate_card_by_serial(self):
        rc, out, confs = self._run(self.CEZANNE, self._two_cards(),
                                   full_rate="bbb")
        self.assertEqual(rc, 0, out)
        by = self._by_serial(confs)
        self.assertEqual(self._rate(by["BBB"]), 129_600_000)
        self.assertEqual(self._rate(by["AAA"]), 64_800_000)

    def test_operator_names_the_full_rate_radiod_by_id(self):
        rc, out, confs = self._run(self.CEZANNE, self._two_cards(),
                                   full_rate=self.BBB_ID)
        self.assertEqual(rc, 0, out)
        by = self._by_serial(confs)
        self.assertEqual(self._rate(by["BBB"]), 129_600_000)
        self.assertEqual(self._rate(by["AAA"]), 64_800_000)

    def test_operator_name_beats_the_hf_timestd_binding_and_says_so(self):
        rc, out, confs = self._run(
            self.CEZANNE, self._two_cards(), full_rate="AAA",
            coord_text=self._timestd_binding(self.BBB_ID))
        self.assertEqual(rc, 0, out)
        by = self._by_serial(confs)
        self.assertEqual(self._rate(by["AAA"]), 129_600_000)
        self.assertEqual(self._rate(by["BBB"]), 64_800_000)
        self.assertIn("hf-timestd", out)

    def test_fallback_to_bus_order_warns_naming_both(self):
        rc, out, confs = self._run(self.CEZANNE, self._two_cards())
        self.assertEqual(rc, 0, out)
        by = self._by_serial(confs)
        self.assertEqual(self._rate(by["AAA"]), 129_600_000)
        self.assertEqual(self._rate(by["BBB"]), 64_800_000)
        self.assertIn("bus order", out)
        self.assertIn(f"radiod@{self.AAA_ID}", out)
        self.assertIn(f"radiod@{self.BBB_ID}", out)
        self.assertIn("--full-rate", out)

    def test_no_warning_on_unknown_cpu(self):
        rc, out, confs = self._run("Some Unknown CPU", self._two_cards())
        self.assertEqual(rc, 0, out)
        self.assertNotIn("bus order", out)

    def test_unknown_operator_name_warns_and_falls_back(self):
        rc, out, confs = self._run(self.CEZANNE, self._two_cards(),
                                   full_rate="ZZZ")
        self.assertEqual(rc, 0, out)
        self.assertIn("ZZZ", out)
        self.assertIn("bus order", out)
        by = self._by_serial(confs)
        self.assertEqual(self._rate(by["AAA"]), 129_600_000)

    def test_new_card_bound_to_hf_timestd_beats_an_existing_conf(self):
        # The existing radiod@omni keeps its 129.6 (never rewritten), the
        # new card hf-timestd uses gets 129.6 too, and the operator hears
        # that two full-rate radiods now share this CPU.
        rc, out, confs = self._run(
            self.CEZANNE, self._two_cards(),
            existing={"radiod@omni.conf": self.OLD},
            coord_text=self.OLD_COORD + self._timestd_binding(self.BBB_ID))
        self.assertEqual(rc, 0, out)
        self.assertEqual(confs["radiod@omni.conf"], self.OLD)
        new = [t for n, t in confs.items() if n != "radiod@omni.conf"]
        self.assertEqual(len(new), 1)
        self.assertEqual(self._rate(new[0]), 129_600_000)
        self.assertIn("radiod@omni", out)
        self.assertIn("two", out.lower())

    def test_existing_conf_bound_to_hf_timestd_keeps_full_rate(self):
        rc, out, confs = self._run(
            self.CEZANNE, self._two_cards(),
            existing={"radiod@omni.conf": self.OLD},
            coord_text=self.OLD_COORD + self._timestd_binding("omni"))
        self.assertEqual(rc, 0, out)
        new = [t for n, t in confs.items() if n != "radiod@omni.conf"]
        self.assertEqual(self._rate(new[0]), 64_800_000)
        self.assertNotIn("bus order", out)
