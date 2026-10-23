"""The site form of `smd sink` (spec §7.1-§7.3, tests 17 and 18; D19;
rulings C17-5, C19-1, C20-1).

With the feature word ``gate``, every verb of §7.1 changes the site switch,
in the order of ruling C20-1:

1. the lever step, for every client that reads below ``upload`` after the
   change;
2. the stale-writer restarts, only before a change to ``off``;
3. ``hs-uploader sink set site <state>``;
4. ``[uploads]`` in site-profile.toml and coordination.toml;
5. the manifest step (the only step that takes the install lock).

Without ``gate``, ``[uploads]`` alone, in part 1's bytes, and one line.

Each test runs on a scratch copy of the carry-over ``legacy`` fixture
(W1AW: psk-recorder direct, wspr-recorder with its shim on).  Every
``sink set`` and ``sink status`` call runs the branch hs-uploader
(``../hs-uploader/.venv``); the recorders' systemctl is the lever tests'
fake; the daemon's systemctl, migrate and the render are faked as
test_uploader_manifest_part2 fakes them.  Nothing touches a service.
"""

import os
import subprocess
import sys
import time
import tomllib
from collections import Counter
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sigmond import sink_state as ss
from sigmond import uploader_manifest as um
from sigmond.commands import config as cfg
from sigmond.commands import sink
from sigmond.coordination import load_coordination

from test_sink_carryover import RADIOD, needs_real
from test_uploader_manifest_part2 import NO_GATE, REAL, Part2Station, normalise

FILL_FIXTURE = (Path(__file__).resolve().parent / "fixtures"
                / "sink_fill_part2.txt")
PSK = "psk-recorder@W1AW.service"
WSPR = "wspr-recorder@W1AW.service"
METEOR = "meteor-scatter@W1AW.service"
HOST = '[host]\ncall = "W1AW"\ngrid = "FN31"\n'
OFF_WORDS = ("site sink: off — nothing ships and nothing new is stored for "
             "upload; data stored earlier waits and ships when the switch rises")
FILL_LIMIT = ("until hs-uploader cleans the sink itself, sigmond's trim keeps "
              "FT8 spots for one hour and WSPR spots for 24 hours, sent or not")
NOTHING_REFUSED = ("the identity check that can refuse this arrives with each "
                   "client's `sink check` (step 4); nothing refused")


class SiteStation(Part2Station):
    """The legacy fixture, carried over by one manifest step unless
    ``carried`` is false, plus a site-profile.toml.  ``trace`` records, in
    order, each recorder restart, each ``sink set``, each ``[uploads]``
    write, each door closing and the install lock."""

    def __init__(self, test, *, carried=True, meteor=False):
        super().__init__(test)
        self.test = test
        self.profile = self.write("etc/sigmond/site-profile.toml",
                                  '[station]\ncallsign = "W1AW"\n')
        if meteor:
            # an enabled meteor-scatter whose own lever already reads off:
            # the lever step leaves it alone, so only the stale-writer
            # restarts can touch it.
            self.topology("radiod", "wspr-recorder", "psk-recorder",
                          "meteor-scatter")
            self.env("meteor-scatter", "W1AW",
                     "METEOR_SCATTER_DELIVERY_MODE=off\n")
        self.trace = []
        self.sc.on_restart = lambda unit: self.trace.append(("restart", unit))
        if carried:
            r = self.step()
            test.assertEqual(r.rc, 0, r.both)
            test.assertTrue(self.roots.marker.exists())
        self.trace.clear()

    @property
    def coord_path(self):
        return self.roots.coordination

    def mode(self, path=None):
        return load_coordination(path or self.coord_path).uploads.mode

    def make_stale(self):
        """The installed writer changed after every running instance."""
        os.utime(self.writer, (6000, 6000))

    def cmd(self, fn, *, features=REAL, fail=None, patch_fail=None,
            user="opal", extra=(), euid=0, patch_exc=None):
        trace = self.trace = []
        real_run = um.run_hs_uploader
        real_patch = cfg._patch_uploads_block

        def run(args, **kw):
            args = list(args)
            if args[:2] == ["sink", "set"]:
                trace.append(("set", args[2], args[3]))
            if fail and fail(args):
                return subprocess.CompletedProcess(args, 1, "",
                                                   "injected failure")
            return real_run(args, **kw)

        def patch(path, up):
            trace.append(("uploads", Path(path).name))
            if patch_fail and patch_fail(Path(path)):
                if patch_exc is not None:
                    raise patch_exc
                raise PermissionError(13, "Permission denied", str(path))
            return real_patch(path, up)

        def doors():
            trace.append(("doors",))
            return 0

        patches = [
            mock.patch.object(cfg, "COORDINATION_PATH", self.coord_path),
            mock.patch.object(cfg, "SITE_PROFILE_PATH", self.profile),
            mock.patch.object(cfg, "_close_doors", side_effect=doors),
            mock.patch.object(um, "run_hs_uploader", side_effect=run),
            mock.patch.object(cfg, "_patch_uploads_block", side_effect=patch),
            mock.patch.dict(os.environ, {"SUDO_USER": user}),
            mock.patch.object(cfg, "_euid", return_value=euid),
            *extra,
        ]
        return self.run(fn, features=features, extra=patches,
                        on_lock=lambda: trace.append(("lock",)))

    def sink(self, verb, *, reason=None, yes=True, **kw):
        args = types.SimpleNamespace(sink_command=verb, reason=reason, yes=yes)
        return self.cmd(lambda: sink.cmd_sink(args), **kw)

    def legacy(self, verb, *, reason=None, yes=True, **kw):
        """`smd upload <verb>` and `smd config uploads <verb>`."""
        args = types.SimpleNamespace(uploads_command=verb, reason=reason,
                                     yes=yes)
        return self.cmd(lambda: cfg.cmd_config_uploads(args), **kw)

    def kinds(self, kind):
        return [t for t in self.trace if t[0] == kind]

    def first(self, event):
        return next(i for i, t in enumerate(self.trace) if t[:len(event)] == event)

    def restarts(self):
        return [t[1] for t in self.trace if t[0] == "restart"]


# ---------------------------------------------------------------------------
# Test 17: each verb of §7.1 writes site.toml and both [uploads] blocks
# ---------------------------------------------------------------------------

# (how to run it, the site switch, the [uploads] mode)
VERBS = [
    ("smd sink upload", lambda st: st.sink("upload"), "upload", "upload"),
    ("smd upload on", lambda st: st.legacy("on"), "upload", "upload"),
    ("smd config uploads enable", lambda st: st.legacy("enable"), "upload",
     "upload"),
    ("smd sink fill", lambda st: st.sink("fill", reason="rig"), "fill", "hold"),
    ("smd upload hold", lambda st: st.legacy("hold", reason="rig"), "fill",
     "hold"),
    ("smd upload off", lambda st: st.legacy("off", reason="rig"), "fill",
     "hold"),
    ("smd config uploads disable", lambda st: st.legacy("disable", reason="rig"),
     "fill", "hold"),
    ("smd sink off", lambda st: st.sink("off", reason="rig"), "off", "discard"),
    ("smd upload discard", lambda st: st.legacy("discard", reason="rig"), "off",
     "discard"),
]


@needs_real
class VerbTests(unittest.TestCase):

    def test_each_verb_writes_site_toml_and_both_uploads_blocks(self):
        for name, run, state, mode in VERBS:
            with self.subTest(name):
                st = SiteStation(self)
                if state == "upload":
                    # start somewhere else, so the write shows
                    r = st.sink("fill", reason="before")
                    self.assertEqual(r.rc, 0, r.both)
                    self.assertEqual(st.switch("site")["state"], "fill")
                r = run(st)
                self.assertEqual(r.rc, 0, r.both)
                site = st.switch("site")
                self.assertEqual(site["state"], state)
                self.assertEqual(site["set_by"], "smd sink (opal)")
                self.assertEqual(st.mode(), mode)
                self.assertEqual(st.mode(st.profile), mode)
                self.assertEqual(st.kinds("set"), [("set", "site", state)])
                self.assertEqual(st.kinds("uploads"),
                                 [("uploads", "site-profile.toml"),
                                  ("uploads", "coordination.toml")])
                # the mirror agrees, so the manifest step repairs nothing
                self.assertNotIn("disagreed", r.both)

    def test_legacy_off_and_hold_mean_fill_and_discard_means_off(self):
        st = SiteStation(self)
        r = st.legacy("off", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st.switch("site")["state"], "fill")
        self.assertEqual(st.mode(), "hold")
        r = st.legacy("discard", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st.switch("site")["state"], "off")
        r = st.legacy("hold", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st.switch("site")["state"], "fill")

    def test_the_reason_reaches_site_toml(self):
        st = SiteStation(self)
        r = st.sink("fill", reason="rig checkout")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st.switch("site")["reason"], "rig checkout")


@needs_real
class FailureTests(unittest.TestCase):

    def test_a_failed_sink_set_exits_1_and_names_site_toml(self):
        st = SiteStation(self)
        r = st.sink("fill", reason="rig",
                    fail=lambda a: a[:3] == ["sink", "set", "site"])
        self.assertEqual(r.rc, 1, r.both)
        site = st.roots.sinks / "site.toml"
        self.assertIn(f"{site}", r.err)
        self.assertIn("injected failure", r.err)
        self.assertEqual(st.switch("site")["state"], "upload")
        # positive control: the same harness, no failure, exits 0
        r = st.sink("fill", reason="rig")
        self.assertEqual(r.rc, 0, r.both)

    def test_a_failed_uploads_write_exits_1_and_names_that_file(self):
        for name in ("coordination.toml", "site-profile.toml"):
            with self.subTest(name):
                st = SiteStation(self)
                r = st.sink("fill", reason="rig",
                            patch_fail=lambda p: p.name == name)
                self.assertEqual(r.rc, 1, r.both)
                path = st.coord_path.parent / name
                self.assertIn(f"{path}", r.err)
                # site.toml holds the order all the same
                self.assertEqual(st.switch("site")["state"], "fill")

    def test_a_lever_failure_still_writes_the_site_and_exits_1(self):
        # C20-1: an off order must land; the lever failure is named.
        st = SiteStation(self)
        env = st.tmp / "etc/psk-recorder/env/W1AW.env"
        real = st.tmp / "elsewhere.env"
        real.write_text(env.read_text())
        env.unlink()
        env.symlink_to(real)
        r = st.sink("off", reason="rig")
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual(st.switch("site")["state"], "off")
        self.assertEqual(st.mode(), "discard")
        self.assertIn("FAILED", r.err)
        self.assertIn(str(env), r.err)
        self.assertEqual(real.read_text(), "PSK_DELIVERY_PIPELINES=direct\n")

    def test_an_unreadable_status_still_writes_the_site_and_exits_1(self):
        st = SiteStation(self)
        r = st.sink("off", reason="rig",
                    fail=lambda a: "status" in a)
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual(st.switch("site")["state"], "off")
        self.assertIn("cannot read hs-uploader sink status", r.err)
        self.assertEqual(st.restarts(), [])


# ---------------------------------------------------------------------------
# Test 18: leaving off closes the doors; nothing else does
# ---------------------------------------------------------------------------

@needs_real
class DoorTests(unittest.TestCase):

    def test_leaving_off_closes_the_doors_and_nothing_else_does(self):
        st = SiteStation(self)
        steps = [("off", False), ("fill", True), ("upload", False),
                 ("fill", False), ("off", False), ("upload", True)]
        for verb, closes in steps:
            r = st.sink(verb, reason="rig")
            self.assertEqual(r.rc, 0, r.both)
            self.assertEqual(bool(st.kinds("doors")), closes, verb)
            self.assertEqual(st.switch("site")["state"], verb)

    def test_the_doors_close_before_anything_is_written(self):
        st = SiteStation(self)
        st.sink("off", reason="rig")
        st.sink("fill", reason="rig")
        self.assertEqual(st.trace[0], ("doors",))

    def test_a_door_refusal_leaves_the_switch_off(self):
        st = SiteStation(self)
        st.sink("off", reason="rig")
        r = st.sink("upload", extra=[mock.patch.object(
            cfg, "_close_doors", return_value=1)])
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual(st.switch("site")["state"], "off")
        self.assertEqual(st.kinds("set"), [])


# ---------------------------------------------------------------------------
# §7.3: off, fill, raising
# ---------------------------------------------------------------------------

@needs_real
class OffTests(unittest.TestCase):

    def test_off_needs_a_reason(self):
        st = SiteStation(self)
        r = st.sink("off", reason="")
        self.assertEqual(r.rc, 2, r.both)
        self.assertEqual(st.switch("site")["state"], "upload")
        self.assertEqual(st.trace, [])

    def test_off_needs_confirmation_off_a_terminal(self):
        st = SiteStation(self)
        with mock.patch("sys.stdin.isatty", return_value=False):
            r = st.sink("off", reason="rig", yes=False)
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual(st.switch("site")["state"], "upload")
        self.assertEqual(st.trace, [])

    def test_with_gate_off_never_asks_about_discard(self):
        st = SiteStation(self)
        r = st.sink("off", reason="rig", extra=[mock.patch.object(
            um, "hs_uploader_supports_discard",
            side_effect=AssertionError("asked about discard"))])
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st.switch("site")["state"], "off")

    def test_off_says_what_it_does(self):
        st = SiteStation(self)
        r = st.sink("off", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertIn(OFF_WORDS, r.both)
        self.assertNotIn("no backlog builds", r.both)


@needs_real
class FillTests(unittest.TestCase):

    def test_fill_prints_the_trims_limits(self):
        st = SiteStation(self)
        r = st.sink("fill", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertIn(FILL_LIMIT, r.both)
        # positive control: upload does not say it
        r = st.sink("upload")
        self.assertNotIn(FILL_LIMIT, r.both)

    def test_fill_needs_no_reason(self):
        st = SiteStation(self)
        r = st.sink("fill")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st.switch("site")["state"], "fill")


@needs_real
class RaiseTests(unittest.TestCase):

    def test_raising_names_each_lifted_client_and_who_stays_below(self):
        st = SiteStation(self)
        r = st.sink("fill", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        # psk-recorder's own switch keeps it at fill
        res = um.run_hs_uploader(["sink", "set", "psk-recorder", "fill",
                                  "--reason", "test", "--by", "test",
                                  "--sinks", str(st.roots.sinks)],
                                 python=st.roots.python, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        r = st.sink("upload")
        self.assertEqual(r.rc, 0, r.both)
        self.assertIn("wspr-recorder W1AW: callsign W1AW, reporter id W1AW, "
                      "grid FN31, PSWS ids none", r.both)
        self.assertNotIn("psk-recorder W1AW: callsign", r.both)
        self.assertIn(NOTHING_REFUSED, r.both)
        self.assertIn("psk-recorder stays at fill: its own switch reads fill",
                      r.both)
        self.assertLess(r.both.index("wspr-recorder W1AW"),
                        r.both.index(NOTHING_REFUSED))

    def test_lowering_lists_no_identities(self):
        st = SiteStation(self)
        r = st.sink("fill", reason="rig")
        self.assertNotIn(NOTHING_REFUSED, r.both)
        self.assertNotIn("callsign W1AW", r.both)


# ---------------------------------------------------------------------------
# The lever step and the stale-writer restarts (C17-5, C20-1)
# ---------------------------------------------------------------------------

@needs_real
class LeverTests(unittest.TestCase):

    def test_lowering_runs_the_lever_step_once_and_never_radiod(self):
        st = SiteStation(self)
        radiod = dict(st.units[RADIOD])
        r = st.sink("fill", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(sorted(st.restarts()), [PSK, WSPR])
        self.assertIn("PSK_DELIVERY_PIPELINES=server-raw",
                      (st.tmp / "etc/psk-recorder/env/W1AW.env").read_text())
        self.assertIn("WSPR_USE_HS_UPLOADER=0",
                      (st.tmp / "etc/wspr-recorder/env/W1AW.env").read_text())
        self.assertEqual(st.units[RADIOD], radiod)
        self.assertFalse(any(RADIOD in " ".join(c) for c in st.sc.verbs()))
        # the manifest step's own lever step then found nothing to do
        lock = st.first(("lock",))
        self.assertEqual([t for t in st.trace[lock:] if t[0] == "restart"], [])

    def test_raising_restarts_nothing(self):
        st = SiteStation(self)
        st.sink("fill", reason="rig")
        self.assertTrue(st.restarts())          # positive control
        r = st.sink("upload")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st.restarts(), [])

    def test_stale_writers_restart_before_off_and_not_for_fill(self):
        st = SiteStation(self, meteor=True)
        st.make_stale()
        r = st.sink("fill", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertNotIn(METEOR, st.restarts())
        r = st.sink("off", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st.restarts(), [METEOR])

    def test_off_restarts_each_instance_once(self):
        st = SiteStation(self, meteor=True)
        st.make_stale()
        r = st.sink("off", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(sorted(st.restarts()), [METEOR, PSK, WSPR])


@needs_real
class OrderTests(unittest.TestCase):
    """Ruling C20-1: levers, stale restarts, site.toml, [uploads], then the
    manifest step, the only step under the install lock."""

    def test_the_order_of_an_off_change(self):
        st = SiteStation(self, meteor=True)
        st.make_stale()
        r = st.sink("off", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        lever = max(st.first(("restart", PSK)), st.first(("restart", WSPR)))
        stale = st.first(("restart", METEOR))
        site = st.first(("set", "site", "off"))
        uploads = [i for i, t in enumerate(st.trace) if t[0] == "uploads"]
        lock = st.first(("lock",))
        self.assertLess(lever, stale)
        self.assertLess(stale, site)
        self.assertLess(site, min(uploads))
        self.assertLess(max(uploads[:2]), lock)
        self.assertEqual(len(st.kinds("lock")), 1)

    def test_the_order_of_a_fill_change(self):
        st = SiteStation(self)
        r = st.sink("fill", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        lever = max(st.first(("restart", PSK)), st.first(("restart", WSPR)))
        site = st.first(("set", "site", "fill"))
        self.assertLess(lever, site)
        self.assertLess(site, st.first(("uploads",)))
        self.assertLess(st.first(("uploads",)), st.first(("lock",)))


# ---------------------------------------------------------------------------
# gate, no marker yet: the carry-over runs inside the manifest step
# ---------------------------------------------------------------------------

@needs_real
class BeforeTheMarkerTests(unittest.TestCase):

    def test_the_carry_over_keeps_the_operators_order(self):
        for verb, state, mode in (("off", "off", "discard"),
                                  ("fill", "fill", "hold")):
            with self.subTest(verb):
                st = SiteStation(self, carried=False)
                self.assertFalse(st.roots.marker.exists())
                r = st.sink(verb, reason="rig")
                self.assertEqual(r.rc, 0, r.both)
                self.assertTrue(st.roots.marker.exists())   # carried over
                site = st.switch("site")
                self.assertEqual(site["state"], state)
                self.assertEqual(site["set_by"], "smd sink (opal)")
                self.assertEqual(st.mode(), mode)
                # the carry-over found site.toml already matching [uploads]
                self.assertEqual(st.kinds("set")[0], ("set", "site", state))
                self.assertNotIn(("set", "site", "upload"), st.trace)
                self.assertIn(f"carry-over: site {state}, kept", r.both)
                self.assertNotIn("carry-over: site missing", r.both)
                # a later manifest step leaves the order standing
                again = st.step()
                self.assertEqual(again.rc, 0, again.both)
                self.assertEqual(st.switch("site")["state"], state)

    def test_a_failed_coordination_write_skips_the_manifest_step(self):
        # Without the marker the carry-over reads coordination.toml; run
        # now, it would carry the old mode back into site.toml.
        st = SiteStation(self, carried=False)
        r = st.sink("off", reason="rig",
                    patch_fail=lambda p: p.name == "coordination.toml")
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual(st.kinds("lock"), [])
        self.assertEqual(st.switch("site")["state"], "off")
        self.assertFalse(st.roots.marker.exists())
        self.assertIn("carry-over", r.err)
        # positive control: a clean run reaches the manifest step
        r = st.sink("off", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(len(st.kinds("lock")), 1)


# ---------------------------------------------------------------------------
# Without gate: [uploads] alone, part 1's bytes, one line
# ---------------------------------------------------------------------------

@needs_real
class NoGateTests(unittest.TestCase):
    WAITS = "waits for an hs-uploader that offers the gate"

    def check(self, features):
        st = SiteStation(self, carried=False)
        r = st.sink("fill", reason="rig", features=features)
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st.coord_path.read_text(),
                         HOST + '\n[uploads]\nenabled = false\nreason = "rig"\n')
        self.assertEqual(st.profile.read_text(),
                         '[station]\ncallsign = "W1AW"\n\n[uploads]\n'
                         'enabled = false\nreason = "rig"\n')
        self.assertFalse((st.roots.sinks / "site.toml").exists())
        self.assertEqual(st.kinds("set"), [])
        self.assertEqual(st.restarts(), [])
        self.assertEqual(r.both.count(self.WAITS), 1)
        # part 1's own words for the legacy hold
        self.assertIn("site sink: hold (legacy)", r.both)
        return st

    def test_a_feature_set_without_gate(self):
        self.check(NO_GATE)

    def test_the_empty_feature_set(self):
        self.check(frozenset())

    def test_a_failed_probe(self):
        self.check(None)

    def test_without_gate_off_still_asks_about_discard(self):
        st = SiteStation(self, carried=False)
        r = st.sink("off", reason="rig", features=NO_GATE,
                    extra=[mock.patch.object(um, "hs_uploader_supports_discard",
                                             return_value=False)])
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual(st.mode(), "upload")

    def test_with_gate_the_waiting_line_is_absent(self):
        st = SiteStation(self)
        r = st.sink("fill", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertNotIn(self.WAITS, r.both)


# ---------------------------------------------------------------------------
# The capture Task 23 reads (global constraint 4, ruling X6)
# ---------------------------------------------------------------------------

def fill_transcript(case: unittest.TestCase) -> str:
    """`smd sink fill --reason rig` on the carried legacy station, stdout
    and stderr in the order printed, the scratch root reading @ROOT@."""
    st = SiteStation(case)
    r = st.sink("fill", reason="rig")
    case.assertEqual(r.rc, 0, r.both)
    return normalise(r.both, st)


@needs_real
class CaptureTests(unittest.TestCase):

    def test_the_captured_fill_transcript_matches(self):
        """Regenerate with tests/fixtures/capture_sink_fill_part2.py."""
        self.assertEqual(fill_transcript(self), FILL_FIXTURE.read_text())


# ---------------------------------------------------------------------------
# Fix round 1 (Task 20 review): C20-2, the door and skip paths, M-3..M-5
# ---------------------------------------------------------------------------

@needs_real
class FixRound1Tests(unittest.TestCase):

    def test_off_before_marker_restarts_each_once(self):
        # Ruling C20-2: before the marker, the carry-over alone levers and
        # restarts; steps 1 and 2 wait.
        st = SiteStation(self, carried=False, meteor=True)
        st.make_stale()
        r = st.sink("off", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        c = Counter(st.restarts())
        self.assertEqual(c, Counter({PSK: 1, WSPR: 1, METEOR: 1}))
        self.assertNotIn(RADIOD, c)
        self.assertTrue(st.roots.marker.exists())
        # every restart came from the carry-over, under the lock
        self.assertLess(st.first(("lock",)), st.first(("restart",)))

    def test_merge_gate_orders_survive_later_manifest_steps(self):
        for verb, state, mode in (("fill", "fill", "hold"),
                                  ("off", "off", "discard")):
            with self.subTest(verb):
                st = SiteStation(self)
                r = st.sink(verb, reason="rig")
                self.assertEqual(r.rc, 0, r.both)
                for _ in range(2):
                    again = st.step()
                    self.assertEqual(again.rc, 0, again.both)
                    self.assertNotIn("disagreed", again.both)
                self.assertEqual(st.switch("site")["state"], state)
                self.assertEqual(st.mode(), mode)
                self.assertEqual(st.mode(st.profile), mode)

    def test_leaving_off_by_the_uploads_record_alone_closes_the_doors(self):
        # No site.toml yet; only [uploads] says discard.
        st = SiteStation(self, carried=False)
        st.coordination(HOST + '\n[uploads]\nenabled = false\n'
                        'mode = "discard"\nreason = "bench"\n')
        self.assertFalse((st.roots.sinks / "site.toml").exists())
        r = st.sink("fill", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st.trace[0], ("doors",))
        # positive control the other way: [uploads] at hold closes nothing
        st2 = SiteStation(self, carried=False)
        st2.coordination(HOST + '\n[uploads]\nenabled = false\n')
        r = st2.sink("upload")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st2.kinds("doors"), [])

    def test_a_writer_newer_than_the_lever_restarts_restarts_nothing_twice(self):
        # The writer's time lies ahead of every restart this run stamps, so
        # each instance the lever step restarts still reads stale: only
        # skip= keeps the stale step off it.
        st = SiteStation(self, meteor=True)
        future = time.time() + 100000
        os.utime(st.writer, (future, future))
        r = st.sink("off", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(Counter(st.restarts()),
                         Counter({PSK: 1, WSPR: 1, METEOR: 1}))

    def test_radiod_moving_during_the_levers(self):
        st = SiteStation(self, meteor=True)
        st.make_stale()

        def bump(unit):
            st.trace.append(("restart", unit))
            st.units[RADIOD]["ActiveEnterTimestamp"] = "@99999"
        st.sc.on_restart = bump
        r = st.sink("off", reason="rig")
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual(st.switch("site")["state"], "off")
        self.assertNotIn(METEOR, st.restarts())
        self.assertIn("skips its stale-writer restarts", r.err)
        self.assertNotIn("no further restart runs", r.err)

    def test_a_non_oserror_from_the_uploads_writer_still_runs_the_manifest_step(self):
        st = SiteStation(self)
        r = st.sink("fill", reason="rig",
                    patch_fail=lambda p: p.name == "site-profile.toml",
                    patch_exc=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"))
        self.assertEqual(r.rc, 1, r.both)
        self.assertIn(str(st.profile), r.err)
        self.assertEqual(len(st.kinds("lock")), 1)
        self.assertEqual(st.switch("site")["state"], "fill")

    def test_a_failed_raise_report_still_runs_the_manifest_step(self):
        st = SiteStation(self)
        r = st.sink("fill", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        real = cfg.load_coordination
        calls = []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 2:          # the report's own read
                raise ValueError("coordination.toml went bad")
            return real(*a, **k)
        r = st.sink("upload", extra=[mock.patch.object(
            cfg, "load_coordination", side_effect=flaky)])
        self.assertEqual(r.rc, 1, r.both)
        self.assertIn("cannot list the clients this raise lifts", r.err)
        self.assertEqual(len(st.kinds("lock")), 1)
        self.assertEqual(st.switch("site")["state"], "upload")

    def test_a_non_root_caller_changes_nothing(self):
        st = SiteStation(self)
        r = st.sink("fill", reason="rig", euid=1000)
        self.assertEqual(r.rc, 1, r.both)
        self.assertIn("run it as root", r.err)
        self.assertEqual(st.trace, [])
        self.assertEqual(st.switch("site")["state"], "upload")
        # positive control: as root, the same call writes
        r = st.sink("fill", reason="rig")
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st.switch("site")["state"], "fill")


if __name__ == "__main__":
    unittest.main()
