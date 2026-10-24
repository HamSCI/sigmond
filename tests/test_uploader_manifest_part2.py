"""The manifest step, part 2 (spec §6.1, §6.4, §7.2, §7.4; plan Rulings 9,
10, 13, 14; V1, V2, V6).

Under the install lock the write step now runs, in order: the features
probe; with ``gate`` and no marker the carry-over; with ``gate`` and the
marker standing the mirror repair, late clients and the lever step; with a
feature set that lacks ``gate`` and the marker standing, the move-aside;
then the render and steps 3-8, landing ``pipelines.d/sigmond.toml`` beside
the legacy manifest.

Each test runs on a scratch copy of a carry-over fixture tree.  Every
``sink set``, ``sink mark-carried`` and ``sink status`` call runs the
branch hs-uploader (``../hs-uploader/.venv``), as the carry-over's own
tests do; the recorders' systemctl is the lever tests' fake; the daemon's
systemctl, migrate and the render are faked as test_uploader_manifest_step
fakes them.  Nothing here touches a service.
"""

import contextlib
import hashlib
import io
import os
import re
import stat
import sys
import tomllib
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sigmond import sink_carryover as co
from sigmond import sink_state as ss
from sigmond import uploader_manifest as um
from sigmond.commands import uploader as up_cmd

from test_sink_carryover import REAL_PY, RADIOD, Runner, Station, needs_real

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "manifest_step_part2.txt"
MANIFEST = (Path(__file__).resolve().parent / "fixtures" / "carryover" / "legacy"
            / "etc" / "hs-uploader" / "pipelines.toml").read_text()
# Part 1's render of the legacy fixture: the manifest it already holds.
PART1_TEXT = MANIFEST
# The gate render: every pipeline names its client (a real change, so the
# daemon restarts once), and the daemon still reads it.
GATE_TEXT = (MANIFEST
             .replace('name = "wspr-wsprnet"\n',
                      'name = "wspr-wsprnet"\nclient = "wspr-recorder"\n')
             .replace('name = "psk-pskreporter"\n',
                      'name = "psk-pskreporter"\nclient = "psk-recorder"\n'))
SIGMOND_TEXT = ('# heartbeat\n[[pipeline]]\nname = "heartbeat"\n'
                'client = "sigmond"\ngoverned = false\n')
NO_GATE = frozenset({"pipelines.d", "send-log-v3", "switch-files"})
REAL = object()           # run the real features probe


def tree_sum(root: Path) -> dict:
    """Every entry under `root`: its type, mode, and content hash (or link
    target).  Equal before and after means nothing was written."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        for name in sorted(dirs + files):
            p = Path(dirpath) / name
            st = os.lstat(p)
            rel = str(p.relative_to(root))
            if stat.S_ISLNK(st.st_mode):
                out[rel] = ("link", os.readlink(p))
            elif stat.S_ISDIR(st.st_mode):
                out[rel] = ("dir", stat.S_IMODE(st.st_mode))
            else:
                out[rel] = ("file", stat.S_IMODE(st.st_mode), st.st_mtime_ns,
                            hashlib.sha256(p.read_bytes()).hexdigest())
    return out


class _Tee(io.StringIO):
    def __init__(self, both):
        super().__init__()
        self.both = both

    def write(self, s):
        self.both.write(s)
        return super().write(s)


class Part2Station(Station):
    """A carry-over Station plus the daemon's fakes, kept across runs."""

    def __init__(self, test, fixture="legacy"):
        super().__init__(test, fixture)
        self.pipelines_d = self.roots.manifest.parent / "pipelines.d"
        self.sigmond_toml = self.pipelines_d / um.SIGMOND_FILE
        self.daemon = {"active": True}
        self.rendered = []

    def coordination_text(self):
        return (self.tmp / "etc/sigmond/coordination.toml").read_text()

    def step(self, *, write=True, enable=False, features=REAL, euid=0,
             carry_apply=None):
        return self.run(lambda: up_cmd.cmd_uploader_manifest(
            types.SimpleNamespace(write=write, enable=enable)),
            features=features, euid=euid, carry_apply=carry_apply)

    def run(self, fn, *, features=REAL, euid=0, carry_apply=None, extra=(),
            on_lock=None):
        """Run ``fn()`` with the daemon's fakes in place (the manifest step
        or any command that reaches it).  ``extra`` adds patches;
        ``on_lock()`` runs as the install lock is taken."""
        calls, order = [], []
        st = self

        def fake_forms(topology=None, coord=None, *, features, marker_stands,
                       supports_discard=None, store_version=None):
            gate = um.gate_form(features, marker_stands)
            st.rendered.append("gate" if gate else "part1")
            order.append("render")
            if gate:
                return um.RenderResult(GATE_TEXT, SIGMOND_TEXT)
            return um.RenderResult(PART1_TEXT, None)

        @contextlib.contextmanager
        def fake_lock(*a, **k):
            order.append("lock")
            if on_lock is not None:
                on_lock()
            try:
                yield
            finally:
                order.append("unlock")

        def fake_run(cmd):
            verb = " ".join(cmd[:2])
            calls.append(verb)
            if verb == "systemctl stop":
                st.daemon["active"] = False
            elif verb in ("systemctl start", "systemctl restart") or (
                    verb == "systemctl enable" and "--now" in cmd):
                st.daemon["active"] = True
            return 0

        def fake_migrate(*, check=False, **k):
            calls.append("check" if check else "migrate")
            return 0

        both = io.StringIO()
        out, err = _Tee(both), _Tee(both)
        roots = self.roots
        patches = [
            mock.patch.object(up_cmd, "_step_roots", return_value=roots),
            mock.patch.object(up_cmd, "_lever_systemctl", self.sc),
            mock.patch.object(up_cmd, "_euid", return_value=euid),
            mock.patch.object(up_cmd.install_lock, "held",
                              side_effect=fake_lock),
            mock.patch.object(um, "generate_forms", side_effect=fake_forms),
            mock.patch.object(um, "suppressed_pipelines", return_value=[]),
            mock.patch.object(um, "MANIFEST_PATH", roots.manifest),
            mock.patch.object(up_cmd, "_ensure_daemon_installed",
                              return_value=True),
            mock.patch.object(up_cmd, "_run_migrate", side_effect=fake_migrate),
            mock.patch.object(up_cmd, "_service_active",
                              side_effect=lambda: st.daemon["active"]),
            mock.patch.object(up_cmd, "_started_before_manifest",
                              return_value=False),
            mock.patch.object(up_cmd, "_daemon_store_path",
                              return_value=roots.store),
            mock.patch.object(up_cmd, "_run", side_effect=fake_run),
            # the daemon's environment (Task 27 M-3): the unit's is empty,
            # and uploader.env sits in the scratch tree, absent until a
            # test writes it
            mock.patch.object(up_cmd, "_unit_environment", return_value={}),
            mock.patch.object(up_cmd, "_UPLOADER_ENV",
                              roots.manifest.parent / "uploader.env"),
        ]
        if features is not REAL:
            patches.append(mock.patch.object(um, "hs_uploader_features",
                                             return_value=features))
        if carry_apply is not None:
            patches.append(mock.patch.object(co, "apply",
                                             side_effect=carry_apply))
        patches.extend(extra)
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            rc = fn()
        return types.SimpleNamespace(
            rc=rc, calls=calls, order=order, out=out.getvalue(),
            err=err.getvalue(), both=both.getvalue(),
            manifest=(roots.manifest.read_text()
                      if roots.manifest.exists() else None),
            render=self.rendered[-1] if self.rendered else None)


def daemon_restarts(r):
    return [c for c in r.calls if c in ("systemctl stop", "systemctl start",
                                        "systemctl restart")]


def normalise(text: str, st: Station) -> str:
    return text.replace(str(st.tmp), "@ROOT@")


def v1_transcript(case: unittest.TestCase) -> str:
    """The V1 run's whole output, stdout and stderr in the order printed,
    the scratch root reading @ROOT@.  The capture script writes it to
    tests/fixtures/manifest_step_part2.txt."""
    st = Part2Station(case)
    r = st.step()
    return normalise(r.both, st)


@needs_real
class V1Tests(unittest.TestCase):
    """Both part 2: carry-over, marker, gate render, sigmond.toml, one
    daemon restart, radiod untouched."""

    def test_first_run_carries_over_and_renders_the_gate_form(self):
        st = Part2Station(self)
        self.assertFalse(st.roots.marker.exists())
        radiod_before = dict(st.units[RADIOD])
        r = st.step()
        self.assertEqual(r.rc, 0, r.both)
        self.assertTrue(st.roots.marker.exists())
        self.assertEqual(st.files(), ["psk-recorder", "site", "wspr-recorder"])
        self.assertEqual(st.switch("site")["state"], "upload")
        self.assertEqual(r.render, "gate")
        self.assertEqual(r.manifest, GATE_TEXT)
        self.assertEqual(st.sigmond_toml.read_text(), SIGMOND_TEXT)
        self.assertEqual(stat.S_IMODE(st.sigmond_toml.stat().st_mode), 0o644)
        self.assertEqual(daemon_restarts(r), ["systemctl stop",
                                              "systemctl start"])
        self.assertTrue(st.daemon["active"])
        # radiod untouched; positive control: the fake records a restart.
        self.assertEqual(st.units[RADIOD], radiod_before)
        self.assertFalse(any(RADIOD in c for c in st.sc.verbs()))
        # every step of the write path ran under the one lock
        self.assertEqual(r.order, ["lock", "render", "unlock"])
        self.assertIn("uploader: carry-over: marker written\n", r.out)

    def test_carry_over_comes_before_the_render(self):
        st = Part2Station(self)
        seen = []
        real = co.apply

        def spy(plan, **kw):
            seen.append(("apply", list(st.rendered)))
            return real(plan, **kw)

        r = st.step(carry_apply=spy)
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(seen, [("apply", [])])     # nothing rendered yet

    def test_second_run_writes_and_restarts_nothing(self):
        st = Part2Station(self)
        first = st.step()
        self.assertEqual(first.rc, 0, first.both)
        before = tree_sum(st.tmp)
        restarts = list(st.sc.restarts())
        runner_calls = []
        real_run = um.run_hs_uploader

        def counting(args, **kw):
            runner_calls.append(list(args))
            return real_run(args, **kw)

        with mock.patch.object(um, "run_hs_uploader", side_effect=counting):
            r = st.step()
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(tree_sum(st.tmp), before)
        self.assertEqual(daemon_restarts(r), [])
        self.assertEqual(st.sc.restarts(), restarts)
        self.assertFalse(any(a[:2] in (["sink", "set"], ["sink", "mark-carried"])
                             for a in runner_calls), runner_calls)
        self.assertIn("already current", r.out)
        self.assertNotIn("carry-over", r.out)
        # positive control: the counter sees the hs-uploader calls it makes
        self.assertTrue(any("status" in a for a in runner_calls),
                        runner_calls)

    def test_every_line_carries_the_uploader_prefix(self):
        st = Part2Station(self)
        for r in (st.step(), st.step()):
            self.assertTrue(r.out)
            for line in r.out.splitlines():
                self.assertTrue(line.startswith("uploader: "), line)
            for line in r.err.splitlines():
                self.assertTrue(line.startswith("smd: uploader: "), line)

    def test_the_captured_transcript_matches(self):
        """The fixture Task 24 reads stays what this run prints.  Regenerate
        with tests/fixtures/capture_manifest_step_part2.py."""
        self.assertEqual(v1_transcript(self), FIXTURE.read_text())


@needs_real
class FillLeverTests(unittest.TestCase):
    """V1 at a held site: the lever step restarts recorders, never radiod,
    and once only."""

    def test_levers_restart_recorders_once_and_never_radiod(self):
        st = Part2Station(self)
        st.coordination('[host]\ncall = "W1AW"\ngrid = "FN31"\n\n'
                        '[uploads]\nenabled = false\n')
        radiod_before = dict(st.units[RADIOD])
        r = st.step()
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(sorted(st.sc.restarts()),
                         ["psk-recorder@W1AW.service",
                          "wspr-recorder@W1AW.service"])
        self.assertEqual(st.units[RADIOD], radiod_before)
        again = st.step()
        self.assertEqual(again.rc, 0, again.both)
        self.assertEqual(len(st.sc.restarts()), 2)       # nothing new


@needs_real
class CarryFailureTests(unittest.TestCase):

    def test_a_failed_carry_over_renders_part_1_and_exits_1(self):
        st = Part2Station(self)
        real = co.apply

        def failing(plan, **kw):
            return real(plan, runner=Runner(st.log, fail_on=lambda a: a[1] ==
                                            "mark-carried"), **kw)

        r = st.step(carry_apply=failing)
        self.assertEqual(r.rc, 1, r.both)
        self.assertFalse(st.roots.marker.exists())
        self.assertEqual(r.render, "part1")
        self.assertEqual(r.manifest, PART1_TEXT)
        self.assertFalse(st.sigmond_toml.exists())
        self.assertIn("FAILED", r.err)
        # positive control: the same run without the failure renders gate
        ok = st.step()
        self.assertEqual(ok.rc, 0, ok.both)
        self.assertEqual(ok.render, "gate")


@needs_real
class MirrorRepairTests(unittest.TestCase):
    """Ruling 9: with the marker standing, site.toml holds the truth."""

    def setUp(self):
        self.st = Part2Station(self)
        self.st.write("etc/sigmond/site-profile.toml",
                      '[host]\ncall = "W1AW"\n\n[uploads]\nenabled = true\n')
        first = self.st.step()
        self.assertEqual(first.rc, 0, first.both)
        self.coord = self.st.tmp / "etc/sigmond/coordination.toml"
        self.profile = self.st.tmp / "etc/sigmond/site-profile.toml"

    def set_site(self, state):
        r = Runner()(["sink", "set", "site", state, "--reason", "bench",
                      "--by", "test", "--sinks", str(self.st.roots.sinks)])
        self.assertEqual(r.returncode, 0, r.stderr)

    def uploads(self, path):
        return tomllib.loads(path.read_text()).get("uploads", {})

    def test_site_fill_rewrites_uploads_in_both_files(self):
        self.set_site("fill")
        r = self.st.step()
        self.assertEqual(r.rc, 0, r.both)
        for path in (self.coord, self.profile):
            self.assertIs(self.uploads(path)["enabled"], False, path)
            self.assertEqual(self.uploads(path).get("reason"), "bench")
        self.assertEqual(self.st.switch("site")["state"], "fill")
        said = [ln for ln in r.out.splitlines() if "[uploads]" in ln]
        self.assertEqual(len(said), 1, r.out)
        self.assertIn("smd sink", said[0])
        self.assertIn("coordination.toml", said[0])
        self.assertIn("site-profile.toml", said[0])
        # the next run finds them agreeing and says nothing
        again = self.st.step()
        self.assertNotIn("[uploads]", again.out)

    def test_agreeing_files_are_left_alone(self):
        before = (self.coord.read_bytes(), self.profile.read_bytes())
        r = self.st.step()
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual((self.coord.read_bytes(), self.profile.read_bytes()),
                         before)

    def test_missing_site_toml_is_recreated_from_uploads(self):
        self.st.coordination('[host]\ncall = "W1AW"\ngrid = "FN31"\n\n'
                             '[uploads]\nenabled = false\n')
        (self.st.roots.sinks / "site.toml").unlink()
        r = self.st.step()
        self.assertEqual(r.rc, 0, r.both)
        site = self.st.switch("site")
        self.assertEqual(site["state"], "fill")
        self.assertEqual(site["set_by"], "smd (site file missing)")
        self.assertTrue(any("missing" in ln for ln in r.out.splitlines()))

    def test_broken_site_toml_leaves_both_files_and_exits_1(self):
        site = self.st.roots.sinks / "site.toml"
        site.write_text("state = [broken\n")
        self.set_uploads_differently()
        before = (site.read_bytes(), self.coord.read_bytes(),
                  self.profile.read_bytes())
        restarts = list(self.st.sc.restarts())
        r = self.st.step()
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual((site.read_bytes(), self.coord.read_bytes(),
                          self.profile.read_bytes()), before)
        self.assertIn(str(site), r.err)
        self.assertIn("smd sink", r.err)
        self.assertEqual(self.st.sc.restarts(), restarts)

    def set_uploads_differently(self):
        self.st.coordination('[host]\ncall = "W1AW"\ngrid = "FN31"\n\n'
                             '[uploads]\nenabled = false\n')

    def test_the_repair_never_reenters_the_manifest_step(self):
        """C14-3: the step holds the install lock; re-entering it would
        deadlock on flock."""
        self.set_site("off")
        with mock.patch.object(up_cmd, "cmd_uploader_manifest",
                               wraps=up_cmd.cmd_uploader_manifest) as entry:
            r = self.st.step()
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(entry.call_count, 1)
        self.assertEqual(self.uploads(self.coord).get("mode"), "discard")


@needs_real
class LateClientTests(unittest.TestCase):

    def test_a_client_new_since_the_carry_over_gets_a_file(self):
        st = Part2Station(self)
        self.assertEqual(st.step().rc, 0)
        st.topology("wspr-recorder", "psk-recorder", "meteor-scatter")
        r = st.step()
        self.assertEqual(r.rc, 0, r.both)
        self.assertIn("meteor-scatter", st.files())
        self.assertEqual(st.switch("meteor-scatter")["state"], "upload")
        self.assertTrue(any("meteor-scatter has no switch file" in ln
                            for ln in r.out.splitlines()), r.out)


@needs_real
class V6Tests(unittest.TestCase):
    """Ruling 10: a probe without ``gate`` (a set, not None) moves the
    marker aside; ``None`` moves nothing."""

    def setUp(self):
        self.st = Part2Station(self)
        first = self.st.step()
        self.assertEqual(first.rc, 0, first.both)
        self.assertTrue(self.st.sigmond_toml.exists())

    def aside(self):
        return sorted(p.name for p in self.st.roots.marker.parent.iterdir()
                      if p.name.startswith(ss.MARKER_NAME + ".rolled-back-"))

    def test_no_gate_moves_the_marker_aside(self):
        for features in (NO_GATE, frozenset()):
            with self.subTest(features=features):
                if not self.st.roots.marker.exists():
                    self.st.step()                  # gate again: carry over
                    self.assertTrue(self.st.roots.marker.exists())
                n = len(self.aside())
                r = self.st.step(features=features)
                self.assertEqual(r.rc, 0, r.both)
                self.assertFalse(self.st.roots.marker.exists())
                self.assertEqual(len(self.aside()), n + 1)
                self.assertRegex(self.aside()[-1],
                                 r"\.rolled-back-\d{8}T\d{6}Z(\.\d+)?$")
                self.assertFalse(self.st.sigmond_toml.exists())
                self.assertEqual(r.render, "part1")
                self.assertEqual(r.manifest, PART1_TEXT)
                self.assertTrue(any("moved the carry-over marker aside" in ln
                                    for ln in r.out.splitlines()), r.out)

    def test_a_failed_probe_moves_nothing_and_warns_once(self):
        r = self.st.step(features=None)
        self.assertTrue(self.st.roots.marker.exists())
        self.assertEqual(self.aside(), [])
        self.assertEqual(len(r.err.splitlines()), 1, r.err)
        self.assertEqual(r.render, "part1")

    def test_gate_returning_carries_over_again(self):
        self.st.step(features=NO_GATE)
        self.assertFalse(self.st.roots.marker.exists())
        r = self.st.step()
        self.assertEqual(r.rc, 0, r.both)
        self.assertTrue(self.st.roots.marker.exists())
        self.assertIn("uploader: carry-over: marker written\n", r.out)


@needs_real
class Ruling14Tests(unittest.TestCase):
    """The legacy manifest unchanged, sigmond.toml alone changes: landed or
    removed in the no-change branch, with no daemon restart."""

    def test_sigmond_toml_removed_without_a_restart(self):
        st = Part2Station(self)
        st.pipelines_d.mkdir()
        st.sigmond_toml.write_text(SIGMOND_TEXT)
        r = st.step(features=NO_GATE)          # part 1 render == installed
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(r.manifest, PART1_TEXT)
        self.assertFalse(st.sigmond_toml.exists())
        self.assertEqual(daemon_restarts(r), [])
        self.assertIn("removed", r.out)
        # positive control: the fake daemon records a restart when the
        # legacy manifest changes
        st.roots.manifest.write_text(GATE_TEXT)
        r2 = st.step(features=NO_GATE)
        self.assertEqual(daemon_restarts(r2), ["systemctl stop",
                                               "systemctl start"])

    def test_sigmond_toml_written_without_a_restart(self):
        st = Part2Station(self)
        self.assertEqual(st.step().rc, 0)
        st.sigmond_toml.unlink()
        r = st.step()
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(st.sigmond_toml.read_text(), SIGMOND_TEXT)
        self.assertEqual(daemon_restarts(r), [])

    def test_a_refused_sigmond_toml_fails_the_gate_step(self):
        st = Part2Station(self)
        self.assertEqual(st.step().rc, 0)
        st.sigmond_toml.unlink()
        target = st.tmp / "elsewhere"
        target.write_text("keep\n")
        st.sigmond_toml.symlink_to(target)
        r = st.step()
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual(target.read_text(), "keep\n")


@needs_real
class ReadOnlyTests(unittest.TestCase):
    """Ruling 13: without --write, marker absent, gate offered: print the
    carry-over and diff the gate render; write nothing."""

    def test_read_only_prints_the_carry_over_and_the_gate_diff(self):
        st = Part2Station(self)
        before = tree_sum(st.tmp)
        r = st.step(write=False, euid=1000)
        self.assertEqual(tree_sum(st.tmp), before)
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual(r.render, "gate")
        self.assertIn("uploader: carry-over: site missing -> upload", r.out)
        self.assertIn('+client = "psk-recorder"', r.out)
        self.assertIn("sigmond.toml", r.out)
        self.assertNotIn("lock", r.order)
        self.assertFalse(st.roots.marker.exists())
        # positive control: the checksum sees a write
        (st.roots.sinks).mkdir(parents=True, exist_ok=True)
        self.assertNotEqual(tree_sum(st.tmp), before)

    def test_read_only_as_root_writes_nothing_either(self):
        st = Part2Station(self)
        before = tree_sum(st.tmp)
        r = st.step(write=False, euid=0)
        self.assertEqual(tree_sum(st.tmp), before)
        self.assertEqual(r.render, "gate")

    def test_read_only_without_gate_and_with_the_marker_moves_nothing(self):
        st = Part2Station(self)
        self.assertEqual(st.step().rc, 0)
        before = tree_sum(st.tmp)
        r = st.step(write=False, features=NO_GATE)
        self.assertEqual(tree_sum(st.tmp), before)
        self.assertIn("would move", r.out)


@needs_real
class StandingLeverTests(unittest.TestCase):
    """The lever step with the marker standing: an operator who lowers the
    site by `hs-uploader sink set` alone (bench step 6, D6) gets the
    in-process senders stopped at the next manifest step, once."""

    def test_a_site_lowered_since_the_carry_over_levers_at_the_next_step(self):
        st = Part2Station(self)
        self.assertEqual(st.step().rc, 0)
        self.assertEqual(st.sc.restarts(), [])
        r = Runner()(["sink", "set", "site", "fill", "--reason", "bench",
                      "--by", "test", "--sinks", str(st.roots.sinks)])
        self.assertEqual(r.returncode, 0, r.stderr)
        radiod_before = dict(st.units[RADIOD])
        step = st.step()
        self.assertEqual(step.rc, 0, step.both)
        self.assertEqual(sorted(st.sc.restarts()),
                         ["psk-recorder@W1AW.service",
                          "wspr-recorder@W1AW.service"])
        self.assertIn("PSK_DELIVERY_PIPELINES=server-raw",
                      (st.tmp / "etc/psk-recorder/env/W1AW.env").read_text())
        self.assertTrue(any(ln.startswith("uploader: lever: restarted ")
                            for ln in step.out.splitlines()), step.out)
        self.assertEqual(st.units[RADIOD], radiod_before)
        again = st.step()
        self.assertEqual(again.rc, 0, again.both)
        self.assertEqual(len(st.sc.restarts()), 2)
        self.assertNotIn("lever:", again.out)


@needs_real
class LandOrderTests(unittest.TestCase):
    """Task 16's forward note: the gate form lands sigmond.toml first, then
    the legacy manifest; part 1's form lands the legacy manifest first,
    then removes sigmond.toml.  Either way one heartbeat stands."""

    def spy(self, st, **kw):
        seen = []
        real_land, real_sig = up_cmd._land, um.land_sigmond_file

        def land(*a, **k):
            seen.append("legacy")
            return real_land(*a, **k)

        def sig(text, *a, **k):
            seen.append("sigmond" if text is not None else "remove sigmond")
            return real_sig(text, *a, **k)

        with mock.patch.object(up_cmd, "_land", side_effect=land), \
             mock.patch.object(um, "land_sigmond_file", side_effect=sig):
            r = st.step(**kw)
        self.assertEqual(r.rc, 0, r.both)
        return seen

    def test_gate_form_lands_sigmond_toml_first(self):
        st = Part2Station(self)
        self.assertEqual(self.spy(st), ["sigmond", "legacy"])

    def test_part_1_form_removes_sigmond_toml_last(self):
        st = Part2Station(self)
        self.assertEqual(st.step().rc, 0)
        self.assertEqual(self.spy(st, features=NO_GATE),
                         ["legacy", "remove sigmond"])


def runner_spy():
    """(calls, side_effect) counting every hs-uploader call the step makes."""
    calls = []
    real = um.run_hs_uploader

    def counting(args, **kw):
        calls.append(list(args))
        return real(args, **kw)
    return calls, counting


@needs_real
class FixRound1Tests(unittest.TestCase):
    """Task 19 review, fix round 1."""

    def standing(self):
        st = Part2Station(self)
        first = st.step()
        self.assertEqual(first.rc, 0, first.both)
        return st

    def set_site(self, st, state):
        r = Runner()(["sink", "set", "site", state, "--reason", "bench",
                      "--by", "test", "--sinks", str(st.roots.sinks)])
        self.assertEqual(r.returncode, 0, r.stderr)

    # I-1 (C19-1) --------------------------------------------------------------
    def test_unreadable_status_exits_1_after_landing_with_one_line(self):
        st = self.standing()
        self.set_site(st, "fill")            # a readable status would lever
        st.roots.manifest.write_text(PART1_TEXT)      # stale legacy manifest
        st.sigmond_toml.unlink()

        def unreadable(**kw):
            print("sigmond: cannot read hs-uploader sink status: exit 2: "
                  "boom", file=sys.stderr)
            return None

        with mock.patch.object(ss, "read_state", side_effect=unreadable):
            r = st.step()
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual(r.manifest, GATE_TEXT)
        self.assertEqual(st.sigmond_toml.read_text(), SIGMOND_TEXT)
        self.assertEqual(len(r.err.splitlines()), 1, r.err)
        self.assertTrue(r.err.startswith("smd: uploader: lever step: "), r.err)
        self.assertIn("exit 2: boom", r.err)
        self.assertEqual(st.sc.restarts(), [])
        # positive control: the same station with a readable status levers
        r2 = st.step()
        self.assertEqual(r2.rc, 0, r2.both)
        self.assertEqual(sorted(st.sc.restarts()),
                         ["psk-recorder@W1AW.service",
                          "wspr-recorder@W1AW.service"])

    # I-2 ------------------------------------------------------------------------
    def test_a_gate_land_failure_in_step_7_puts_the_daemon_back(self):
        st = self.standing()
        st.roots.manifest.write_text(PART1_TEXT)      # changed: steps 5-8
        st.sigmond_toml.unlink()
        target = st.tmp / "elsewhere"
        target.write_text("keep\n")
        st.sigmond_toml.symlink_to(target)
        for active in (True, False):
            with self.subTest(active=active):
                st.daemon["active"] = active
                # a link planted after the preflight: the landing itself
                # must refuse it
                with mock.patch.object(up_cmd, "_sigmond_preflight",
                                       return_value=None):
                    r = st.step()
                self.assertEqual(r.rc, 1, r.both)
                self.assertIn("systemctl stop", r.calls)
                self.assertEqual(st.daemon["active"], active)
                self.assertEqual(target.read_text(), "keep\n")
                self.assertTrue(st.sigmond_toml.is_symlink())
                self.assertEqual(r.manifest, PART1_TEXT)

    # M-4 ------------------------------------------------------------------------
    def test_a_symlink_refuses_before_anything_stops(self):
        st = self.standing()
        st.roots.manifest.write_text(PART1_TEXT)
        for where in ("file", "dir"):
            with self.subTest(where=where):
                target = st.tmp / f"elsewhere-{where}"
                if st.pipelines_d.is_symlink():
                    st.pipelines_d.unlink()
                if where == "file":
                    st.pipelines_d.mkdir(exist_ok=True)
                    if os.path.lexists(st.sigmond_toml):
                        st.sigmond_toml.unlink()
                    target.write_text("keep\n")
                    st.sigmond_toml.symlink_to(target)
                else:
                    for p in st.pipelines_d.iterdir():
                        p.unlink()
                    st.pipelines_d.rmdir()
                    target.mkdir()
                    st.pipelines_d.symlink_to(target)
                r = st.step()
                self.assertEqual(r.rc, 1, r.both)
                self.assertEqual(daemon_restarts(r), [])
                self.assertNotIn("migrate", r.calls)
                self.assertTrue(st.daemon["active"])
                self.assertEqual(r.manifest, PART1_TEXT)
                self.assertIn("symlink", r.err)
                self.assertFalse(os.path.exists(
                    st.roots.manifest.with_name("pipelines.toml.new")))

    # I-3 ------------------------------------------------------------------------
    def test_a_non_root_write_writes_and_restarts_nothing(self):
        st = Part2Station(self)
        before = tree_sum(st.tmp)
        calls, counting = runner_spy()
        with mock.patch.object(um, "run_hs_uploader", side_effect=counting):
            r = st.step(write=True, euid=1000)
        self.assertEqual(r.rc, 1, r.both)
        self.assertIn("requires root", r.err)
        self.assertEqual(tree_sum(st.tmp), before)
        self.assertFalse(any(a[:2] == ["sink", "set"] for a in calls), calls)
        self.assertEqual(st.sc.restarts(), [])
        self.assertEqual(daemon_restarts(r), [])
        self.assertNotIn("lock", r.order)
        # positive control: the spy sees the hs-uploader calls it makes
        self.assertTrue(calls)

    # M-2 ------------------------------------------------------------------------
    def test_a_failed_mirror_write_still_runs_the_lever_step(self):
        st = self.standing()
        self.set_site(st, "fill")
        import errno
        from sigmond.commands import config as cfg
        with mock.patch.object(cfg, "_patch_uploads_block",
                               side_effect=PermissionError(errno.EACCES,
                                                           "denied")):
            r = st.step()
        self.assertEqual(r.rc, 1, r.both)
        self.assertIn("mirror repair: FAILED", r.err)
        self.assertEqual(sorted(st.sc.restarts()),
                         ["psk-recorder@W1AW.service",
                          "wspr-recorder@W1AW.service"])

    # M-3 ------------------------------------------------------------------------
    def test_the_preview_of_a_broken_site_never_says_repair(self):
        st = self.standing()
        (st.roots.sinks / "site.toml").write_text("state = [broken\n")
        before = tree_sum(st.tmp)
        r = st.step(write=False)
        self.assertEqual(tree_sum(st.tmp), before)
        self.assertNotIn("would repair", r.both)
        self.assertIn("would leave [uploads] and the site switch alone", r.err)
        self.assertEqual(r.rc, 1)


class MoveAsideTests(unittest.TestCase):
    """Ruling 10's helper, which Task 22's restore reuses: a rename inside
    the state directory's descriptor, never following a link, never
    replacing an earlier rolled-back marker."""

    def setUp(self):
        import tempfile
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name) / "state"
        self.dir.mkdir()
        self.marker = self.dir / ss.MARKER_NAME
        from datetime import datetime, timezone
        self.now = datetime(2026, 10, 23, 12, 34, 56, tzinfo=timezone.utc)

    def test_moves_the_marker_to_the_rolled_back_name(self):
        self.marker.write_text("carried\n")
        new = ss.move_marker_aside(self.marker, now=self.now)
        self.assertEqual(new, self.dir / (ss.MARKER_NAME
                                          + ".rolled-back-20261023T123456Z"))
        self.assertEqual(new.read_text(), "carried\n")
        self.assertFalse(ss.marker_stands(self.marker))

    def test_nothing_standing_moves_nothing(self):
        self.assertIsNone(ss.move_marker_aside(self.marker, now=self.now))
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_a_second_move_in_the_same_second_keeps_the_first(self):
        self.marker.write_text("one\n")
        first = ss.move_marker_aside(self.marker, now=self.now)
        self.marker.write_text("two\n")
        second = ss.move_marker_aside(self.marker, now=self.now)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(), "one\n")
        self.assertEqual(second.read_text(), "two\n")

    def test_a_symlink_marker_moves_as_a_link_its_target_untouched(self):
        target = self.dir.parent / "target"
        target.write_text("keep\n")
        self.marker.symlink_to(target)
        new = ss.move_marker_aside(self.marker, now=self.now)
        self.assertTrue(new.is_symlink())
        self.assertEqual(os.readlink(new), str(target))
        self.assertEqual(target.read_text(), "keep\n")
        self.assertFalse(os.path.lexists(self.marker))

    def test_a_directory_marker_moves_too(self):
        self.marker.mkdir()
        new = ss.move_marker_aside(self.marker, now=self.now)
        self.assertTrue(new.is_dir())
        self.assertFalse(os.path.lexists(self.marker))

    def test_a_symlinked_state_directory_is_refused(self):
        real = self.dir.parent / "real"
        real.mkdir()
        (real / ss.MARKER_NAME).write_text("x\n")
        link = self.dir.parent / "linked"
        link.symlink_to(real)
        with self.assertRaises(OSError):
            ss.move_marker_aside(link / ss.MARKER_NAME, now=self.now)
        self.assertTrue((real / ss.MARKER_NAME).exists())


class PlanMirrorTests(unittest.TestCase):
    """Ruling 9's pure planner."""

    @property
    def sm(self):
        from sigmond import sink_mirror
        return sink_mirror

    SITE = Path("/etc/hs-uploader/sinks/site.toml")
    COORD = Path("/etc/sigmond/coordination.toml")
    PROF = Path("/etc/sigmond/site-profile.toml")

    def plan(self, state, coord, prof=None, reason="why"):
        site = self.sm.SiteRead(state=state, reason=reason,
                                error="bad" if state == "error" else "")
        return self.sm.plan_mirror(self.SITE, site,
                                   [(self.COORD, coord), (self.PROF, prof)])

    def test_agreement_is_no_action(self):
        for state, mode in (("upload", "upload"), ("fill", "hold"),
                            ("off", "discard")):
            with self.subTest(state=state):
                self.assertEqual(self.plan(state, mode, mode).action, "none")

    def test_the_switch_wins_over_uploads(self):
        p = self.plan("fill", "upload", "upload")
        self.assertEqual(p.action, "rewrite")
        self.assertEqual(p.mode, "hold")
        self.assertEqual([f for f, _ in p.files], [self.COORD, self.PROF])

    def test_only_the_differing_file_is_rewritten(self):
        p = self.plan("off", "discard", "upload")
        self.assertEqual([f for f, _ in p.files], [self.PROF])

    def test_a_profile_without_the_block_is_left_alone(self):
        p = self.plan("fill", "hold", None)
        self.assertEqual(p.action, "none")

    def test_missing_recreates_from_coordination(self):
        p = self.plan("missing", "discard", "upload")
        self.assertEqual(p.action, "recreate")
        self.assertEqual(p.state, "off")

    def test_broken_touches_nothing(self):
        p = self.plan("error", "upload", "upload")
        self.assertEqual(p.action, "broken")
        self.assertEqual(p.files, ())
        self.assertTrue(any(str(self.SITE) in ln for ln in p.lines()))


# ---------------------------------------------------------------------------
# Task 27 M-3: a moved state directory refuses the gate path
# ---------------------------------------------------------------------------

class StateDirOverrideUnitTests(unittest.TestCase):

    def setUp(self):
        import tempfile
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.env_file = Path(td.name) / "uploader.env"

    def why(self, unit_env=None, *, unit_exc=None):
        kw = ({"side_effect": unit_exc} if unit_exc is not None
              else {"return_value": unit_env or {}})
        with mock.patch.object(up_cmd, "_unit_environment", **kw), \
                mock.patch.object(up_cmd, "_UPLOADER_ENV", self.env_file):
            return up_cmd._state_dir_override()

    def test_nothing_set_passes(self):
        self.assertIsNone(self.why())

    def test_the_default_spelled_out_passes(self):
        self.assertIsNone(self.why({"HS_UPLOADER_STATE_DIR":
                                    "/var/lib/hs-uploader/"}))
        self.env_file.write_text('HS_UPLOADER_STATE_DIR="/var/lib/hs-uploader"\n')
        self.assertIsNone(self.why())

    def test_the_unit_environment_moving_it_refuses(self):
        why = self.why({"HS_UPLOADER_STATE_DIR": "/srv/hsu"})
        self.assertIn("HS_UPLOADER_STATE_DIR=/srv/hsu", why)
        self.assertIn("hs-uploader.service", why)
        self.assertEqual(len(why.splitlines()), 1)

    def test_uploader_env_moving_it_refuses_even_when_systemctl_fails(self):
        self.env_file.write_text("# moved\nexport HS_UPLOADER_STATE_DIR='/srv/hsu'\n")
        why = self.why(unit_exc=OSError("no systemd"))
        self.assertIn("HS_UPLOADER_STATE_DIR=/srv/hsu", why)
        self.assertIn(str(self.env_file), why)


@needs_real
class StateDirOverrideStepTests(unittest.TestCase):
    MOVED = mock.patch.object(up_cmd, "_unit_environment",
                              return_value={"HS_UPLOADER_STATE_DIR": "/srv/hsu"})

    def step(self, st, *extra, features=REAL):
        return st.run(lambda: up_cmd.cmd_uploader_manifest(
            types.SimpleNamespace(write=True, enable=False)),
            features=features, extra=list(extra))

    def test_the_gate_step_refuses_before_any_write(self):
        st = Part2Station(self)
        before = tree_sum(st.tmp)
        r = self.step(st, self.MOVED)
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual(tree_sum(st.tmp), before)
        self.assertFalse(st.roots.marker.exists())
        refusals = [ln for ln in r.err.splitlines()
                    if "HS_UPLOADER_STATE_DIR=/srv/hsu" in ln]
        self.assertEqual(len(refusals), 1, r.both)
        self.assertEqual(daemon_restarts(r), [])
        # positive control: the default state directory carries over
        r = self.step(st)
        self.assertEqual(r.rc, 0, r.both)
        self.assertTrue(st.roots.marker.exists())

    def test_the_marker_standing_still_refuses(self):
        st = Part2Station(self)
        self.assertEqual(self.step(st).rc, 0)
        st.write("etc/hs-uploader/uploader.env",
                 "HS_UPLOADER_STATE_DIR=/srv/hsu\n")
        before = tree_sum(st.tmp)
        r = self.step(st)
        self.assertEqual(r.rc, 1, r.both)
        self.assertEqual(tree_sum(st.tmp), before)

    def test_without_gate_the_step_does_not_refuse(self):
        st = Part2Station(self)
        r = self.step(st, self.MOVED, features=NO_GATE)
        self.assertEqual(r.rc, 0, r.both)
        self.assertNotIn("HS_UPLOADER_STATE_DIR", r.both)


# ---------------------------------------------------------------------------
# Task 19 (final batch): the old form and a symlinked pipelines.d
# ---------------------------------------------------------------------------

@needs_real
class OldFormSymlinkedPipelinesDTests(unittest.TestCase):
    """Part 1's form only removes pipelines.d/sigmond.toml.  A symlinked
    pipelines.d refuses only when a sigmond.toml stands behind it;
    otherwise the legacy manifest lands."""

    def station(self, *, with_file):
        st = Part2Station(self)
        st.roots.manifest.write_text(GATE_TEXT)       # changed: steps 5-8
        target = st.tmp / "elsewhere"
        target.mkdir()
        if with_file:
            (target / um.SIGMOND_FILE).write_text("keep\n")
        if st.pipelines_d.exists():
            for p in st.pipelines_d.iterdir():
                p.unlink()
            st.pipelines_d.rmdir()
        st.pipelines_d.symlink_to(target)
        return st, target

    def test_no_sigmond_toml_behind_the_link_lands_the_legacy_manifest(self):
        st, target = self.station(with_file=False)
        r = st.step(features=NO_GATE)
        self.assertEqual(r.rc, 0, r.both)
        self.assertEqual(r.render, "part1")
        self.assertEqual(r.manifest, PART1_TEXT)
        self.assertTrue(st.pipelines_d.is_symlink())
        self.assertEqual(list(target.iterdir()), [])

    def test_a_sigmond_toml_behind_the_link_refuses_before_anything_stops(self):
        st, target = self.station(with_file=True)
        r = st.step(features=NO_GATE)
        self.assertEqual(r.rc, 1, r.both)
        self.assertIn("symlink", r.err)
        self.assertEqual(daemon_restarts(r), [])
        self.assertNotIn("migrate", r.calls)
        self.assertEqual(r.manifest, GATE_TEXT)
        self.assertEqual((target / um.SIGMOND_FILE).read_text(), "keep\n")


if __name__ == "__main__":
    unittest.main()
