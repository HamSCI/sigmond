"""Two gateways, two tunnels — the redundant RAC a HamSCI station must hold.

⛔ WHY THESE TESTS EXIST

Three faults put AI6VN on an unsecured legacy tunnel and kept it there, and
every one of them was invisible from inside the code that caused it:

1. ONE CA FILE FOR EVERY GATEWAY.  `/etc/sigmond/frps-ca.crt` held gw2's
   certificate (CN=GW2.WSPRDAEMON.ORG) while the config pointed at
   vpn.hamsci.org, which presents a different one.  TLS could never succeed.
2. THE FETCH AND THE USE DISAGREED.  `_fetch_frps_ca_cert` always asked gw2;
   `_write_frpc_config` could name a HamSCI gateway.  Nothing compared them.
3. A SILENT FALLBACK.  The failure was not an error — the tier ladder simply
   walked down to the open port 35735 and reported success.

So the tests below are written against the two things that actually bit:
a pin that does not match the server it is used against, and a session set
that is quietly smaller than it should be.  See tasks/plan-rac-dual-gateway.md.
"""
import importlib.util
import os
import re
import subprocess
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WIZARD = (REPO / "scripts/proxmox/sigmond-wizard.sh").read_text()


def _smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    spec = importlib.util.spec_from_loader("smd_mod", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = "bin/smd"
    exec(compile((REPO / "bin/smd").read_text(), "bin/smd", "exec"), mod.__dict__)
    return mod


_SMD = _smd()


class GatewaySet(unittest.TestCase):
    def test_a_hamsci_channel_gets_BOTH_gateways(self):
        self.assertEqual(_SMD._rac_gateways_for(229), ("vpn1", "vpn2"))

    def test_the_whole_hamsci_band_gets_both(self):
        for n in (200, 223, 299):
            self.assertEqual(len(_SMD._rac_gateways_for(n)), 2, n)

    def test_a_wsprdaemon_channel_gets_one(self):
        for n in (47, 199, 300, 507):
            self.assertEqual(_SMD._rac_gateways_for(n), ("gw2",), n)

    def test_an_unset_number_does_not_crash_into_the_hamsci_band(self):
        # -1 is "not configured".  Reading it as HamSCI would point an
        # unregistered station at gateways it has no claim on.
        self.assertEqual(_SMD._rac_gateways_for(-1), ("gw2",))
        self.assertEqual(_SMD._rac_gateways_for("not a number"), ("gw2",))

    def test_each_gateway_has_its_own_config_path(self):
        paths = {g: _SMD._frpc_config_path(g) for g in ("vpn1", "vpn2", "gw2")}
        self.assertEqual(len(set(paths.values())), 3, paths)


class PinnedCerts(unittest.TestCase):
    def test_the_hamsci_gateways_do_NOT_pin_gw2s_certificate(self):
        # The exact defect found on AI6VN 2026-10-24.
        for gw in ("vpn1", "vpn2"):
            _host, _port, ca, _sni, _admin = _SMD._RAC_GATEWAYS[gw]
            self.assertNotEqual(ca, _SMD._FRPS_CA_GW2, gw)

    def test_gw2_does_not_pin_the_hamsci_certificate_either(self):
        _host, _port, ca, _sni, _admin = _SMD._RAC_GATEWAYS["gw2"]
        self.assertNotEqual(ca, _SMD._FRPS_CA_HAMSCI)

    def test_every_gateway_pins_SOMETHING(self):
        # An empty trustedCaFile makes frpc verify nothing at all, which is
        # the tempting wrong answer to vpn2's hostname mismatch.
        for gw, (_h, _p, ca, _s, _a) in _SMD._RAC_GATEWAYS.items():
            self.assertTrue(str(ca), gw)

    def test_vpn2_sends_the_servername_the_certificate_actually_carries(self):
        host, _port, _ca, sni, _admin = _SMD._RAC_GATEWAYS["vpn2"]
        self.assertEqual(host, "vpn2.hamsci.org")
        self.assertEqual(sni, "vpn.hamsci.org")


class AdminPorts(unittest.TestCase):
    """⛔ frpc's [webServer] is a real listener, one per session.

    Two sessions sharing 7500 means the second frpc exits at startup with
    "address already in use" — and the station holds ONE tunnel while its
    config, its units and its status all say two.  Found before AI6VN was
    migrated, 2026-10-25: the host template hardcoded 7500 for every session.
    """

    def test_sessions_that_run_together_have_different_admin_ports(self):
        for station in (_SMD._RAC_GATEWAYS_HAMSCI, _SMD._RAC_GATEWAYS_WD):
            ports = [_SMD._RAC_GATEWAYS[g][4] for g in station]
            self.assertEqual(len(ports), len(set(ports)), station)

    def test_the_rendered_configs_carry_those_ports(self):
        seen = set()
        for gw in _SMD._RAC_GATEWAYS_HAMSCI:
            text = _SMD._render_frpc_config(
                "DASI-009", gw, ssh_port=1, web_port=2, user_field="u",
                ssh_user="root", local_ssh_port=22)
            m = re.search(r"\[webServer\].*?port = (\d+)", text, re.S)
            self.assertIsNotNone(m, gw)
            seen.add(m.group(1))
        self.assertEqual(len(seen), 2, seen)

    def test_the_wizard_rows_carry_distinct_admin_ports_too(self):
        rows = _run_wizard_fn("rac_gateway_set vpn1.hamsci.org").strip().splitlines()
        ports = [r.split("|")[5] for r in rows]
        self.assertEqual(len(set(ports)), len(ports), ports)

    def test_the_switch_reads_each_sessions_own_port(self):
        # Counting channels on one fixed port would report the first
        # session's health as the whole station's.
        m = re.search(r"cat > /usr/local/sbin/sigmond-rac-switch <<'RACSWEOF'\n(.*?)\nRACSWEOF\n",
                      WIZARD, re.S)
        body = m.group(1)
        self.assertIn("rac_admin_ports()", body)
        self.assertIn("for _p in $(rac_admin_ports); do", body)


class RenderedConfig(unittest.TestCase):
    def _render(self, gw):
        return _SMD._render_frpc_config(
            "DASI-009", gw, ssh_port=36029, web_port=46029,
            user_field="DASI-009", ssh_user="root", local_ssh_port=22)

    def test_the_vpn2_config_carries_servername(self):
        self.assertIn('serverName = "vpn.hamsci.org"', self._render("vpn2"))

    def test_the_gw2_config_carries_no_servername(self):
        # gw2's cert matches its own name; an SNI override there would be
        # cargo-culted noise that hides why vpn2 needs one.
        self.assertNotIn("serverName", self._render("gw2"))

    def test_every_config_enables_tls_and_pins(self):
        for gw in ("vpn1", "vpn2", "gw2"):
            text = self._render(gw)
            self.assertIn("enable = true", text, gw)
            self.assertRegex(text, r'trustedCaFile = "/\S+\.crt"', gw)

    def test_the_two_hamsci_configs_differ_ONLY_in_the_server_address(self):
        # The gateways file a station by TOFU and merge their registries both
        # ways; a DIFFERENT key or user for the same station is refused as an
        # impersonation attempt.  So everything but the address must match.
        a = self._render("vpn1").splitlines()
        b = self._render("vpn2").splitlines()
        differing = [(x, y) for x, y in zip(a, b) if x != y]
        self.assertTrue(all("serverAddr" in x or "gateway" in x or "port = 75" in x
                            for x, _y in differing), differing)

    def test_both_publish_the_same_remote_ports(self):
        for port in ("remotePort = 36029", "remotePort = 46029"):
            self.assertIn(port, self._render("vpn1"))
            self.assertIn(port, self._render("vpn2"))


class CaFetch(unittest.TestCase):
    """⛔ The cert must be fetched from the gateway it will be used against."""

    def setUp(self):
        self.asked = []
        self.saved = []
        self._pem = _SMD._fetch_tls_cert_pem
        self._inst = _SMD._install_ca_pem
        self._exists = _SMD.Path.exists
        _SMD._fetch_tls_cert_pem = lambda host, port: (
            self.asked.append((host, port)) or "PEM")
        _SMD._install_ca_pem = lambda pem, dest: (
            self.saved.append(dest) or True)
        _SMD.Path.exists = lambda self: False

    def tearDown(self):
        _SMD._fetch_tls_cert_pem = self._pem
        _SMD._install_ca_pem = self._inst
        _SMD.Path.exists = self._exists

    def test_a_hamsci_station_fetches_from_a_hamsci_gateway(self):
        _SMD._fetch_frps_ca_cert(("vpn1", "vpn2"))
        self.assertTrue(self.asked, "nothing was fetched at all")
        for host, _port in self.asked:
            self.assertIn("hamsci.org", host)

    def test_a_hamsci_station_never_asks_gw2(self):
        # This is the bug, stated directly.
        _SMD._fetch_frps_ca_cert(("vpn1", "vpn2"))
        self.assertFalse([h for h, _p in self.asked if "wsprdaemon" in h])

    def test_a_wsprdaemon_station_asks_gw2(self):
        _SMD._fetch_frps_ca_cert(("gw2",))
        self.assertEqual([h for h, _p in self.asked], ["gw2.wsprdaemon.org"])

    def test_the_shared_hamsci_cert_is_fetched_ONCE(self):
        # vpn1 and vpn2 present the same cert and share one path; fetching
        # twice would overwrite a good pin with a second download.
        _SMD._fetch_frps_ca_cert(("vpn1", "vpn2"))
        self.assertEqual(len(self.saved), 1, self.saved)


class HostUnits(unittest.TestCase):
    def test_the_wizard_gateway_set_gives_two_rows_for_hamsci(self):
        rows = _run_wizard_fn("rac_gateway_set vpn1.hamsci.org")
        self.assertEqual(len(rows.strip().splitlines()), 2, rows)

    def test_the_wizard_gateway_set_gives_one_row_for_gw2(self):
        rows = _run_wizard_fn("rac_gateway_set gw2.wsprdaemon.org")
        self.assertEqual(len(rows.strip().splitlines()), 1, rows)

    def test_each_hamsci_row_names_its_own_host_and_the_shared_servername(self):
        rows = _run_wizard_fn("rac_gateway_set vpn1.hamsci.org").strip().splitlines()
        hosts = [r.split("|")[1] for r in rows]
        snis = {r.split("|")[4] for r in rows}
        self.assertEqual(hosts, ["vpn1.hamsci.org", "vpn2.hamsci.org"])
        self.assertEqual(snis, {"vpn.hamsci.org"})

    def test_each_row_has_a_DISTINCT_name(self):
        """⛔ The name is the config path and the unit instance.

        Two rows sharing a name is not a cosmetic slip: both sessions write
        `frpc-host-<name>.toml`, the second overwrites the first, and the
        station ends up with ONE tunnel while every surface still says two.
        A mutation that dropped vpn2 from the printf produced exactly this —
        `printf` reuses its format string, so the row COUNT stayed at two and
        only the names collided.  Counting rows did not catch it.
        """
        rows = _run_wizard_fn("rac_gateway_set vpn1.hamsci.org").strip().splitlines()
        names = [r.split("|")[0] for r in rows]
        self.assertEqual(sorted(names), ["vpn1", "vpn2"], names)

    def test_a_rows_name_matches_the_host_it_carries(self):
        # vpn2's row naming itself vpn1 would silently point the "vpn2"
        # unit at vpn1 and leave the second gateway unused.
        for row in _run_wizard_fn("rac_gateway_set vpn1.hamsci.org").strip().splitlines():
            name, host = row.split("|")[0], row.split("|")[1]
            self.assertTrue(host.startswith(name + "."), row)

    def test_the_unsecured_rungs_are_gone_from_the_ladder(self):
        self.assertNotIn("HamSCI unsecured (direct)|", WIZARD)
        self.assertNotIn("wsprdaemon (UNSECURED)|", WIZARD)

    def test_the_switch_acts_on_every_session(self):
        # An operator who turns remote access off and leaves one gateway up
        # has not turned it off.
        m = re.search(r"cat > /usr/local/sbin/sigmond-rac-switch <<'RACSWEOF'\n(.*?)\nRACSWEOF\n",
                      WIZARD, re.S)
        body = m.group(1)
        self.assertIn("rac_units()", body)
        for verb in ("disable --now", "enable --now"):
            self.assertRegex(body, rf"for _u in \$\(rac_units\); do systemctl {re.escape(verb)}")


def _run_wizard_fn(call: str) -> str:
    """Run one wizard shell function in isolation."""
    pre = textwrap.dedent("""
        set -u
        RAC_PORT_SECURE=35736
        RAC_CA_GW2=/etc/sigmond/frps-ca-gw2.crt
        RAC_CA_HAMSCI=/etc/sigmond/frps-ca-hamsci.crt
        _hs=vpn.hamsci.org; _hs1=vpn1.hamsci.org; _hs2=vpn2.hamsci.org
        _hs_sni=$_hs; _wd=gw2.wsprdaemon.org
    """)
    m = re.search(r"^rac_gateway_set\(\)\{.*?^\}", WIZARD, re.S | re.M)
    assert m, "rac_gateway_set is gone from the wizard"
    r = subprocess.run(["bash", "-c", pre + m.group(0) + "\n" + call],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


class Payload(unittest.TestCase):
    """The sigmond-rac payload beside this checkout, when there is one."""

    def setUp(self):
        self.payload = REPO.parent / "sigmond-rac"
        if not (self.payload / "install-host.sh").exists():
            self.skipTest("no sigmond-rac checkout beside this one")

    def test_it_ships_a_certificate_for_each_gateway(self):
        for name in ("frps-ca-gw2.crt", "frps-ca-hamsci.crt"):
            self.assertTrue((self.payload / name).exists(), name)

    def test_it_never_clobbers_an_existing_pinned_certificate(self):
        # ⛔ It used to install gw2's cert over /etc/sigmond/frps-ca.crt on
        # EVERY run, so correcting a station's cert by hand did not survive
        # the next payload run.
        text = (self.payload / "install-host.sh").read_text()
        self.assertRegex(
            text, r"\[ -e /etc/sigmond/frps-ca\.crt \] \|\|")

    def test_the_host_unit_is_templated_per_gateway(self):
        unit = self.payload / "systemd/sigmond-rac-host@.service"
        self.assertTrue(unit.exists(), "no per-gateway unit template")
        text = unit.read_text()
        self.assertIn("frpc -c /etc/sigmond/frpc-host-%i.toml", text)
        self.assertIn("ConditionPathExists=/etc/sigmond/frpc-host-%i.toml", text)


if __name__ == "__main__":
    unittest.main()
