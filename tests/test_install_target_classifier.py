"""Every component the profile promises must reach an install path.

⛔ Why this check exists.  On 2026-09-03 the v3.37 golden-VM build failed its
capture gate on five hamsci-physics rows: no client binary, and none of
`hamsci-physics-fusion.service`, `grape-daily.timer`,
`hamsci-physics-reanalysis.timer` or `hamsci-physics-ionex-download.timer`
installed.  `smd install --yes` had exited 0.

Two faults stacked, and this file holds the second.

The first lived in the appliance builder, which hand-wrote
`/etc/sigmond/topology.toml` restating the dasi2 profile — so a client added to
the catalog profile never reached the topology `smd install` actually reads.
That drift had already been patched twice, for meteor-scatter and gmag-webui.

The second lives here.  Fixing the topology alone changed nothing, because
`_install_full_suite`'s classifier sorted a catalogued client carrying no
`install_script` into its `unknown` bucket and skipped it:

    ⚠  unknown:      hamsci-physics (not in catalog, skipping)

The message was false — hamsci-physics sits in `etc/catalog.toml` and has since
the 2026-08-24 split.  It declares `install_script = ""` on purpose, because its
`deploy.toml` drives the build and it owns no legacy `install.sh`.  The
single-component path (`smd install hamsci-physics`) installs it correctly: it
falls through to `install_client()`, which discovers the script post-clone —
the mag-recorder pattern.  Only the full-suite classifier dropped it, and it
dropped it with a warning rather than an error, so the run still reported
success.

So a station whose profile promises GRAPE could install cleanly, exit 0, and
produce no GRAPE.  AC0G-B4 had GRAPE only because someone built the venv by
hand; AC0G-ND had the checkout, no venv, and no GRAPE at all.

Two rules follow, and the tests below hold both:

  * a component the operator asked for never disappears into a warning — if no
    path can install it, that ends the run;
  * "not in the catalog" and "in the catalog but unbuildable" name different
    faults and must not share a message.
"""
import importlib.machinery
import importlib.util
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond.catalog import (  # noqa: E402  (after the sys.path bootstrap)
    CatalogEntry, load_catalog, load_profiles,
)


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader("smd_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()


def _entry(name, kind="client", *, script=None, repo="https://example.invalid/x"):
    return CatalogEntry(name=name, kind=kind, description=name,
                        repo=repo, install_script=script)


def _classify(entries, components=None):
    catalog = {e.name: e for e in entries}
    return smd._classify_install_targets(components or list(catalog), catalog)


class ClassifierRoutesEveryRequestedComponent(unittest.TestCase):

    def test_a_client_with_an_install_script_goes_to_the_catalog_queue(self):
        t = _classify([_entry("psk-recorder", script="/opt/x/install.sh")])
        self.assertEqual([e.name for e in t.catalog_queue], ["psk-recorder"])
        self.assertEqual(t.unknown, [])
        self.assertEqual(t.uninstallable, [])

    def test_a_client_with_no_install_script_still_reaches_the_queue(self):
        """The regression.  hamsci-physics discovers its script post-clone."""
        t = _classify([_entry("hamsci-physics", script="")])
        self.assertEqual([e.name for e in t.catalog_queue], ["hamsci-physics"],
                         "a catalogued client whose deploy.toml drives the "
                         "build must install, not vanish into a warning")
        self.assertEqual(t.unknown, [])
        self.assertEqual(t.uninstallable, [])

    def test_a_server_with_no_install_script_also_reaches_the_queue(self):
        t = _classify([_entry("some-server", kind="server", script=None)])
        self.assertEqual([e.name for e in t.catalog_queue], ["some-server"])

    def test_a_name_absent_from_the_catalog_is_unknown(self):
        t = _classify([_entry("psk-recorder", script="/opt/x/install.sh")],
                      components=["psk-recorder", "not-a-component"])
        self.assertEqual(t.unknown, ["not-a-component"])
        self.assertEqual([e.name for e in t.catalog_queue], ["psk-recorder"])

    def test_no_script_and_no_repo_reads_as_uninstallable_not_unknown(self):
        """Different fault, different message: it IS in the catalog."""
        t = _classify([_entry("stub", script="", repo="")])
        self.assertEqual(t.uninstallable, ["stub"])
        self.assertEqual(t.unknown, [], "'not in catalog' would be a lie here")
        self.assertEqual(t.catalog_queue, [])

    def test_the_natively_built_components_keep_their_own_paths(self):
        t = _classify([_entry("ka9q-radio", kind="server", script=""),
                       _entry("ka9q-web", kind="server", script=""),
                       _entry("gmag-webui", kind="server", script="")])
        self.assertEqual(t.natives, {"ka9q-radio", "ka9q-web", "gmag-webui"})
        self.assertEqual(t.catalog_queue, [],
                         "sigmond builds these itself; install_client() must "
                         "not also try to")

    def test_radiod_resolves_to_ka9q_radio_through_its_alias(self):
        catalog = {"ka9q-radio": CatalogEntry(
            name="ka9q-radio", kind="server", description="radiod",
            repo="https://example.invalid/k", install_script="",
            topology_alias="radiod")}
        t = smd._classify_install_targets(["radiod"], catalog)
        self.assertEqual(t.natives, {"ka9q-radio"})

    def test_a_library_or_infra_entry_with_no_script_needs_no_path(self):
        """sigmond installs these itself (ka9q-python's venv, wd-rac's frpc)."""
        t = _classify([_entry("ka9q-python", kind="library", script=""),
                       _entry("wd-rac", kind="infra", script="")])
        self.assertEqual(t.catalog_queue, [])
        self.assertEqual(t.unknown, [])
        self.assertEqual(t.uninstallable, [])
        self.assertEqual(t.natives, set())


class TheRealDasi2ProfileInstallsEndToEnd(unittest.TestCase):
    """The assertion that would have stopped the v3.37 build at its cause.

    Reads the shipped catalog, not a fixture: a client added to the profile
    with no way to install it must fail here, in a second, rather than six
    minutes into a golden-VM build or on a live station months later.
    """

    def test_every_dasi2_client_and_infra_component_has_an_install_path(self):
        catalog = load_catalog(REPO / "etc" / "catalog.toml")
        profile = load_profiles()["dasi2"]
        wanted = list(profile.clients) + list(profile.local_radiod_infra)
        t = smd._classify_install_targets(wanted, catalog)

        routed = {e.name for e in t.catalog_queue} | set(t.natives)
        stranded = [c for c in wanted if c not in routed]
        self.assertEqual(
            stranded, [],
            f"dasi2 promises these but nothing installs them: {stranded} "
            f"(unknown={t.unknown}, uninstallable={t.uninstallable})")

    def test_hamsci_physics_is_one_of_them(self):
        """Named explicitly, so a future profile edit cannot quietly drop it."""
        catalog = load_catalog(REPO / "etc" / "catalog.toml")
        profile = load_profiles()["dasi2"]
        self.assertIn("hamsci-physics", profile.clients,
                      "the dasi2 description promises GRAPE and "
                      "hamsci-physics is what produces it")
        t = smd._classify_install_targets(["hamsci-physics"], catalog)
        self.assertEqual([e.name for e in t.catalog_queue], ["hamsci-physics"])


if __name__ == "__main__":
    unittest.main()
