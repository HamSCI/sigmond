"""Screen <-> smd-verb parity lint.

The TUI drifted out of correspondence with `smd` for months without
anyone noticing, because nothing mechanically checked that the screens
still matched the CLI. This test is that check: it derives the *live*
`smd` verb tree by importing `bin/smd` and walking its argparse parser
(never executing a subcommand -- only building the parser), and
cross-checks it against the declared `SCREEN_VERBS` mapping in
`sigmond.tui.parity`. Its entire value is that it breaks CI when
someone renames an smd verb, deletes a screen, or a nav-tree leaf and
its dispatch branch fall out of sync -- so every assertion here must be
capable of failing for real; see `tasks/plan-tui-reconciliation.md`,
Task 3, for the parity-lint requirement this test implements.

Hermetic: no real host state is touched, and no smd subcommand ever
runs (argparse.ArgumentParser.parse_args is patched to raise before
dispatch happens).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

from tests.smd_tree import live_verb_paths

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LIB = _REPO_ROOT / 'lib'
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

_SCREENS_DIR = _LIB / 'sigmond' / 'tui' / 'screens'
_COMPONENT_TREE = _LIB / 'sigmond' / 'tui' / 'widgets' / 'component_tree.py'


def _screen_module_names() -> set:
    """Every screen module basename, excluding the empty __init__.py."""
    return {p.stem for p in _SCREENS_DIR.glob('*.py') if p.stem != '__init__'}


class ScreenVerbParityTests(unittest.TestCase):
    """Cross-checks sigmond.tui.parity.SCREEN_VERBS against reality."""

    @classmethod
    def setUpClass(cls):
        from sigmond.tui.parity import SCREEN_VERBS

        cls.mapping = SCREEN_VERBS
        cls.screens = _screen_module_names()
        cls.live_verbs = live_verb_paths()

    def test_every_screen_module_has_a_mapping_entry(self):
        missing = self.screens - set(self.mapping)
        self.assertEqual(
            missing, set(),
            f"screen module(s) with no SCREEN_VERBS entry (a new screen "
            f"with no entry must fail CI): {sorted(missing)}",
        )

    def test_every_mapping_entry_names_an_existing_screen_module(self):
        stale = set(self.mapping) - self.screens
        self.assertEqual(
            stale, set(),
            f"SCREEN_VERBS entrie(s) naming a screen module that no "
            f"longer exists (a deleted screen with a stale entry must "
            f"fail CI): {sorted(stale)}",
        )

    def test_every_declared_verb_resolves_against_live_smd(self):
        unresolved = []
        for screen, verbs in sorted(self.mapping.items()):
            if verbs is None:
                continue
            for verb in verbs:
                if verb not in self.live_verbs:
                    unresolved.append((screen, verb))
        self.assertEqual(
            unresolved, [],
            f"SCREEN_VERBS verb(s) that no longer resolve against the "
            f"live bin/smd argparse tree (an smd rename must fail CI): "
            f"{unresolved}",
        )


class NavTreeBijectionTests(unittest.TestCase):
    """component_tree.py: every emitted leaf has a dispatch branch and
    vice versa. Parsed as text (regex), not imported, so this runs
    without textual installed too.
    """

    _LEAF_RE = re.compile(r'data=\{"screen":\s*"([^"]+)"\}')
    # The dispatch chain opens with `if screen == "...":` and continues
    # with `elif screen == "...":` -- both are dispatch branches.
    _DISPATCH_RE = re.compile(r'(?:el)?if screen == "([^"]+)":')

    @classmethod
    def setUpClass(cls):
        cls.source = _COMPONENT_TREE.read_text()
        cls.leaves = set(cls._LEAF_RE.findall(cls.source))
        cls.branches = set(cls._DISPATCH_RE.findall(cls.source))

    def test_leaves_and_branches_were_actually_found(self):
        # Guards against the regexes silently matching nothing if the
        # widget's source shape changes out from under them.
        self.assertTrue(self.leaves, "no tree leaves found in component_tree.py")
        self.assertTrue(self.branches, "no dispatch branches found in component_tree.py")

    def test_every_emitted_leaf_has_a_dispatch_branch(self):
        orphan_leaves = self.leaves - self.branches
        self.assertEqual(
            orphan_leaves, set(),
            f"nav leaf(ves) emitted by populate() with no matching "
            f"`elif screen == ...` dispatch branch -- clicking them "
            f"silently does nothing: {sorted(orphan_leaves)}",
        )

    def test_every_dispatch_branch_has_an_emitted_leaf(self):
        dead_branches = self.branches - self.leaves
        self.assertEqual(
            dead_branches, set(),
            f"dispatch branch(es) in on_tree_node_selected with no "
            f"leaf ever emitting that screen id -- dead code: "
            f"{sorted(dead_branches)}",
        )


if __name__ == '__main__':
    unittest.main()
