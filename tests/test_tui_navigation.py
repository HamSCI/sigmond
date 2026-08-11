"""Navigation tests — every tree node mounts its screen without errors.

Catches placeholder wiring bugs and binding-vs-action mismatches that
unit tests on individual screens can't see.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

try:
    import textual  # noqa: F401
    _HAS_TEXTUAL = True
except ImportError:
    _HAS_TEXTUAL = False


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class TreeNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_binding_mounts_a_screen(self):
        from sigmond.tui.app import SigmondApp

        app = SigmondApp()
        async with app.run_test(size=(120, 60)) as pilot:
            # Every main binding, in order.  A crash in any action_show_*
            # method fails the test with a clear traceback.
            for key in ('o', 'c', 'r', 'v'):
                await pilot.press(key)
                await pilot.pause()
                self.assertIsNotNone(app.query_one("#center"))

    async def test_actions_mount_expected_screen_classes(self):
        """Every surviving non-binding action mounts its own screen
        class.  Phase 2 of the TUI reconciliation deleted the 8 dead
        screens (including PlaceholderScreen itself), so the old
        "not a placeholder" assertion is no longer meaningful — this
        pins the actual expected class per action instead, which is a
        strictly stronger claim."""
        from unittest import mock
        from sigmond.tui.app import SigmondApp
        from sigmond.topology import Topology
        from sigmond.tui.screens.overview import OverviewScreen
        from sigmond.tui.screens.cpu_affinity import CPUAffinityScreen
        from sigmond.tui.screens.cpu_freq import CPUFreqScreen
        from sigmond.tui.screens.radiod import RadiodScreen
        from sigmond.tui.screens.gpsdo import GpsdoScreen
        from sigmond.tui.screens.logs import LogsScreen
        from sigmond.tui.screens.validate import ValidateScreen
        from sigmond.tui.screens.diag_net import DiagNetScreen
        from sigmond.tui.screens.lifecycle import LifecycleScreen
        from sigmond.tui.screens.apply import ApplyScreen
        from sigmond.tui.screens.install import InstallScreen
        from sigmond.tui.screens.components import ComponentsScreen

        action_to_screen = (
            ("show_overview", OverviewScreen),
            ("show_cpu_affinity", CPUAffinityScreen),
            ("show_cpu_freq", CPUFreqScreen),
            ("show_radiod", RadiodScreen),
            ("show_gpsdo", GpsdoScreen),
            ("show_logs", LogsScreen),
            ("show_validate", ValidateScreen),
            ("show_diag_net", DiagNetScreen),
            ("show_lifecycle", LifecycleScreen),
            ("show_apply", ApplyScreen),
            ("show_install", InstallScreen),
            # show_update is a kept alias for show_components.
            ("show_update", ComponentsScreen),
        )

        with mock.patch.object(
                Topology, "enabled_components",
                lambda self, only=None: ["wspr-recorder"]):
            app = SigmondApp()
            async with app.run_test(size=(120, 60)) as pilot:
                for action, screen_cls in action_to_screen:
                    getattr(app, f"action_{action}")()
                    await pilot.pause()
                    center = app.query_one("#center")
                    self.assertTrue(
                        any(isinstance(c, screen_cls)
                            for c in center.children),
                        f"{action} did not mount {screen_cls.__name__}",
                    )


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class OverviewScreenMountTests(unittest.IsolatedAsyncioTestCase):
    """Greenfield-aware landing (app.on_mount): a host with at least one
    enabled component lands on Overview; a blank host leads with the
    guided Greenfield bring-up.  Both branches are pinned here with the
    topology mocked, so the tests are independent of the host they run
    on (CI has no /etc/sigmond; a production box has real state)."""

    async def test_overview_is_default_landing_when_configured(self):
        from unittest import mock
        from sigmond.tui.app import SigmondApp
        from sigmond.tui.screens.overview import OverviewScreen
        from sigmond.topology import Topology

        with mock.patch.object(
                Topology, "enabled_components",
                lambda self, only=None: ["wspr-recorder"]):
            app = SigmondApp()
            async with app.run_test(size=(120, 50)) as pilot:
                # Let the worker complete and the screen re-render.
                for _ in range(3):
                    await pilot.pause()
                center = app.query_one("#center")
                self.assertTrue(
                    any(isinstance(c, OverviewScreen)
                        for c in center.children),
                    "OverviewScreen should be the default landing",
                )

    async def test_greenfield_is_default_landing_when_blank(self):
        from unittest import mock
        from sigmond.tui.app import SigmondApp
        from sigmond.tui.screens.greenfield import GreenfieldScreen
        from sigmond.topology import Topology

        with mock.patch.object(
                Topology, "enabled_components",
                lambda self, only=None: []):
            app = SigmondApp()
            async with app.run_test(size=(120, 50)) as pilot:
                for _ in range(3):
                    await pilot.pause()
                center = app.query_one("#center")
                self.assertTrue(
                    any(isinstance(c, GreenfieldScreen)
                        for c in center.children),
                    "GreenfieldScreen should lead on a blank host",
                )


@unittest.skipUnless(_HAS_TEXTUAL, "textual not installed")
class ComponentTreeStructureTests(unittest.TestCase):
    def test_tree_has_grouped_categories(self):
        """The tree exposes the four operator-workflow groups
        (Monitoring / Maintenance / Debugging / Installation) plus
        Overview as a root-level leaf.  This pins the IA so category
        drift is visible in diffs.  See docs/TUI-FUNCTION-INVENTORY.md
        for the category rationale."""
        from sigmond.tui.widgets.component_tree import ComponentTree
        from sigmond.topology import load_topology

        tree = ComponentTree()
        tree.populate(load_topology(), {})

        labels = [str(n.label) for n in tree.root.children]
        self.assertIn("Monitoring", labels)
        self.assertIn("Maintenance", labels)
        self.assertIn("Debugging", labels)
        self.assertIn("Installation", labels)
        self.assertIn("Advanced", labels)
        # Overview is a leaf at root level, not a group.
        self.assertTrue(any("Overview" in lbl for lbl in labels))

    def test_installation_is_the_three_step_arc(self):
        """Installation collapses to the guided + ①②③ arc, plus PSWS
        enrolment (bring-up work, not part of the numbered per-component
        arc -- placed after step ③ since it depends on a component
        already being enabled/started).  Topology is no longer a leaf
        (derived state, surfaced by step ③).  See
        docs/install-redesign.md Stage 3 and
        tasks/plan-tui-reconciliation.md Task 5."""
        from sigmond.tui.widgets.component_tree import ComponentTree
        from sigmond.topology import load_topology

        tree = ComponentTree()
        tree.populate(load_topology(), {})

        inst = next(n for n in tree.root.children
                    if str(n.label) == "Installation")
        screens = [leaf.data.get("screen") for leaf in inst.children]
        self.assertEqual(
            screens,
            ["greenfield", "install", "configuration", "lifecycle", "psws"])
        # Topology must not appear as a primary nav leaf anywhere.
        all_screens = [leaf.data.get("screen")
                       for grp in tree.root.children
                       for leaf in grp.children if leaf.data]
        self.assertNotIn("topology", all_screens)

    def test_killed_screens_are_not_tree_leaves(self):
        """Phase 2 of the TUI reconciliation removed KiwiSDR live, FFT
        Wisdom, and Sources as tree leaves (their screens were deleted
        outright), plus the already-dead ids that populate() never
        set. None of the 8 killed screens' ids should appear."""
        from sigmond.tui.widgets.component_tree import ComponentTree
        from sigmond.topology import load_topology

        tree = ComponentTree()
        tree.populate(load_topology(), {})

        all_screens = [leaf.data.get("screen")
                       for grp in tree.root.children
                       for leaf in grp.children if leaf.data]
        for dead_id in ("kiwisdr", "fft_wisdom", "sources", "topology",
                        "instance", "config_show", "client_config"):
            self.assertNotIn(dead_id, all_screens)


if __name__ == '__main__':
    unittest.main()
