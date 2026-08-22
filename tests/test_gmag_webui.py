"""gmag_webui (HamSCI magnetometer real-time dashboard) packaged as a
NON-contract catalog component — the ka9q-web shape.  mjh 2026-08-22:
"review, advise, package"; decision: catalog `server`, smd-native recipe,
WebSocket feed from mag-usb on the same machine first, MQTT later."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from sigmond import gmag_webui as gw
from sigmond.catalog import load_catalog
from sigmond.lifecycle import resolve_units

REPO = Path(__file__).resolve().parent.parent
REPO_CATALOG = REPO / "etc" / "catalog.toml"


class CatalogEntryTests(unittest.TestCase):
    def test_entry_is_a_non_contract_server_gated_on_the_magnetometer(self):
        entries = load_catalog(REPO_CATALOG)
        e = entries["gmag-webui"]
        self.assertEqual(e.kind, "server")
        self.assertFalse(e.contract)   # "" normalises to None: not contract-conformant
        self.assertFalse(e.install_script)   # smd-native recipe, no install.sh
        self.assertEqual(e.repo, "https://github.com/HamSCI/gmag_webui")
        self.assertIn("mag-recorder", e.requires)
        self.assertTrue(e.hardware_gated and "magnetometer" in e.hardware_gated.lower())
        self.assertGreater(e.start_priority, entries["mag-recorder"].start_priority)

    def test_dasi2_profile_installs_it_as_a_core_client(self):
        import tomllib
        prof = tomllib.loads(REPO_CATALOG.read_text())["profile"]["dasi2"]
        self.assertIn("gmag-webui", prof["clients"])
        self.assertLess(prof["clients"].index("mag-recorder"), prof["clients"].index("gmag-webui"))


class DeployStubTests(unittest.TestCase):
    def test_stub_resolves_the_single_concrete_unit(self):
        stub = REPO / "etc" / "clients" / "gmag-webui.deploy.toml"
        self.assertTrue(stub.is_file())
        with mock.patch("sigmond.lifecycle._find_deploy_toml",
                        lambda comp: stub if comp == "gmag-webui" else None):
            units = resolve_units(["gmag-webui"], ["gmag-webui"])
        self.assertEqual([u.unit for u in units], ["gmag-webui.service"])


class DenoPinTests(unittest.TestCase):
    def test_pin_constants_are_coherent(self):
        self.assertTrue(gw.DENO_VERSION.startswith("v"))
        self.assertEqual(len(gw.DENO_SHA256), 64)
        self.assertIn(gw.DENO_VERSION, gw.deno_zip_url())
        self.assertTrue(gw.deno_zip_url().endswith("deno-x86_64-unknown-linux-gnu.zip"))
        self.assertEqual(gw.deno_install_dir(),
                         Path("/usr/local/lib/deno") / gw.DENO_VERSION)
        self.assertEqual(gw.DENO_BIN, Path("/usr/local/bin/deno"))

    def test_verify_sha256(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.zip"; p.write_bytes(b"hello")
            good = hashlib.sha256(b"hello").hexdigest()
            self.assertTrue(gw.verify_sha256(p, good))
            self.assertFalse(gw.verify_sha256(p, "0" * 64))
            self.assertFalse(gw.verify_sha256(Path(d) / "missing", good))


class RenderTests(unittest.TestCase):
    def test_env_file(self):
        self.assertEqual(gw.render_env(), 'HOST="0.0.0.0"\nPORT="8082"\n')
        self.assertEqual(gw.PORT, 8082)   # 8000 = timestd web-api, 8081 = ka9q-web

    def test_unit_is_ours_not_upstreams(self):
        unit = gw.render_unit(checkout=Path("/opt/git/sigmond/gmag-webui"))
        self.assertIn("User=gmagweb", unit)
        self.assertIn("WorkingDirectory=/opt/git/sigmond/gmag-webui", unit)
        self.assertIn("EnvironmentFile=/opt/git/sigmond/gmag-webui/.env", unit)
        self.assertIn("Environment=DENO_DIR=/var/lib/gmag-webui/deno", unit)
        self.assertIn("ExecStart=/usr/local/bin/deno run --allow-net "
                      "--allow-read=/opt/git/sigmond/gmag-webui "
                      "--allow-env ts/main.ts", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("WantedBy=multi-user.target", unit)
        # upstream's auto-update timer (git reset --hard every 5 min) is never ours
        self.assertNotIn("OnUnitActiveSec", unit)
        self.assertNotIn("git reset", unit)


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_gmag", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test_gmag", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class InstallDispatchTests(unittest.TestCase):
    def test_dry_run_reaches_the_native_recipe(self):
        smd = _load_smd()
        args = types.SimpleNamespace(client="gmag-webui", dry_run=True, yes=True,
                                     force=False, no_enable=False)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(smd, "load_catalog", return_value=load_catalog(REPO_CATALOG)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = smd._install_single_client(args)
        text = out.getvalue() + err.getvalue()
        self.assertEqual(rc, 0, text)
        self.assertIn("gmag-webui", text)
        self.assertIn(gw.DENO_VERSION, text)
        self.assertIn("8082", text)
        self.assertNotIn("has no install_script", text)
