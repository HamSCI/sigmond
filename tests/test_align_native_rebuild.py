"""v3.94 Task 2: `smd align` rebuilds ka9q-radio and ka9q-web whose binary
trails the checkout.

2026-11-19, AC0G-B4: radiod's binary dated 2026-10-25 while the ka9q-radio
checkout sat at a 2026-11-12 commit.  align called it "current": it compared
the checkout with the pin only, and `_build_current` returned early for
ka9q-radio.  align also never built ka9q-web after moving its checkout.

These tests drive the real `cmd_align` (dry run and --apply) over real git
checkouts in a temp dir.  Only the builders' compile step
(`_build_ka9q_radio`, `_build_ka9q_web_with_onion`), the live stages
(`_quiet_live_stages`) and the root-only unit install stay stubbed.
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from sigmond import align, align_apply
from sigmond.site_profile import LocallyManaged

from tests.test_align_cli import (_apply_args, _apply_patches, _empty_drift,
                                   _staleness, smd)

B4_BINARY_AT = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc).timestamp()
B4_HEAD_AT = "2026-11-12T12:00:00+00:00"


def _git(*args, cwd, date=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run(["git", *args], cwd=str(cwd), env=env, check=True,
                          capture_output=True, text=True)


class _Rig(unittest.TestCase):
    """base/<name>: a clone of a local origin with two commits; HEAD at the
    second (``self.new[name]``) unless a test moves it back to
    ``self.old[name]``.  The build-record dir and both installed binaries
    live in the temp dir."""

    NAMES = ("ka9q-radio", "ka9q-web")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.base = root / "base"
        self.base.mkdir()
        self.old, self.new = {}, {}
        for name in self.NAMES:
            origin = root / "origin" / name
            origin.mkdir(parents=True)
            _git("init", "-q", "-b", "main", cwd=origin)
            (origin / "a").write_text("a\n")
            _git("add", "-A", cwd=origin)
            _git("commit", "-qm", "one", cwd=origin, date="2026-10-01T12:00:00+00:00")
            self.old[name] = _git("rev-parse", "HEAD", cwd=origin).stdout.strip()
            (origin / "b").write_text("b\n")
            _git("add", "-A", cwd=origin)
            _git("commit", "-qm", "two", cwd=origin, date=B4_HEAD_AT)
            self.new[name] = _git("rev-parse", "HEAD", cwd=origin).stdout.strip()
            _git("clone", "-q", str(origin), str(self.base / name), cwd=root)
            _git("checkout", "-q", "--detach", self.new[name], cwd=self.base / name)
        self.manifests = root / "build-manifest"
        self.manifests.mkdir()
        self.radiod_bin = root / "radiod"
        self.web_bin = root / "ka9q-web"
        for p in (self.radiod_bin, self.web_bin):
            p.write_text("binary\n")
            os.utime(p, (B4_BINARY_AT, B4_BINARY_AT))
        self.marker = root / "align-radiod-rebuilt"
        for name, path in (("_BUILD_MANIFEST_DIR", self.manifests),
                           ("_ALIGN_RADIOD_REBUILT", self.marker),
                           ("_ALIGN_RADIOD_BINARY", self.radiod_bin),
                           ("_KA9Q_WEB_BIN", self.web_bin)):
            p = mock.patch.object(smd, name, path)
            p.start()
            self.addCleanup(p.stop)
        self.local = LocallyManaged()
        p = mock.patch.object(smd, "_locally_managed", side_effect=lambda: self.local)
        p.start()
        self.addCleanup(p.stop)
        self.calls = []
        self.build_ok = {"ka9q-radio": True, "ka9q-web": True}

    # --- fixtures ---------------------------------------------------------

    def _record(self, name, sha):
        (self.manifests / f"{name}.toml").write_text(
            f'component = "{name}"\n\n[[upstream]]\nname = "{name}"\n'
            f'url  = "x"\nsha  = "{sha}"\n')

    def _head(self, name):
        return smd._git_head(self.base / name)

    def _release(self, pins=None):
        pins = pins or self.new
        return align.Release(tag="v3.94", manifest_text="m", appliance_commit="a" * 40,
                             components=dict(pins))

    def _live(self):
        return {n: self._head(n) for n in self.NAMES}

    def _builders(self):
        """Recorders standing in for the compile step.  On success each
        installs a new binary (new bytes, mtime now), as `make install`
        does; on failure the installed binary stays as it was."""
        def install(name, binary, src):
            head = smd._git_head(Path(src))
            self.calls.append((name, Path(src), True, head))
            if self.build_ok[name]:
                binary.write_text(f"built from {head}\n")
            return self.build_ok[name]

        def radiod(src, force=False):
            ok = install("ka9q-radio", self.radiod_bin, src)
            self.calls[-1] = self.calls[-1][:2] + (force,) + self.calls[-1][3:]
            return ok

        def web(src, *, force=False):
            ok = install("ka9q-web", self.web_bin, src)
            self.calls[-1] = self.calls[-1][:2] + (force,) + self.calls[-1][3:]
            return ok
        return dict(_build_ka9q_radio=radiod, _build_ka9q_web_with_onion=web)

    # --- drivers ----------------------------------------------------------

    def _apply(self, rel=None, stale=None, **extra):
        """``stale``: what the staleness read reports (component -> why;
        each one running).  ``extra``: further smd attributes to patch.
        ``self.restarts`` collects the components each restart call got."""
        rel = rel or self._release()
        out = io.StringIO()
        staleness_calls = []
        self.restarts = []

        def staleness(*a, **k):
            staleness_calls.append(k)
            return _staleness(stale=stale, running=set(stale or ()))

        def restart(radiod_first, comps, services, **k):
            self.restarts.append((radiod_first, list(comps)))
            return []
        patches = dict(
            _runuser=lambda user, argv: argv,
            _align_own_units=lambda *a, **k: [],
            _align_start_timers=lambda **k: None,
            _refresh_cli_reference=lambda *a, **k: None,
            **self._builders())
        patches.update(extra)
        with contextlib.ExitStack() as st:
            m_record, _ = _apply_patches(st, rel=rel, live=self._live(), **patches)
            # After _quiet_live_stages: watch what the restart stage is told.
            st.enter_context(mock.patch.object(smd, "_align_staleness", staleness))
            st.enter_context(mock.patch.object(smd, "_align_restart", restart))
            st.enter_context(contextlib.redirect_stdout(out))
            rc = smd.cmd_align(_apply_args(self.base))
        self.staleness_calls = staleness_calls
        return rc, out.getvalue(), m_record

    def _dry_run(self, rel=None, extra=()):
        rel = rel or self._release()
        args = _apply_args(self.base, apply=False)
        out = io.StringIO()
        with contextlib.ExitStack() as st:
            for p in [
                mock.patch.object(smd, "_align_services", return_value={}),
                mock.patch.object(smd, "_align_units_started_at", return_value=None),
                mock.patch.object(smd, "_align_head_moved_at", return_value=None),
                mock.patch.object(smd, "_align_drift_report", side_effect=_empty_drift),
                mock.patch.object(smd, "_align_live_state",
                                  return_value=(self._live(), {}, {}, {})),
                mock.patch("sigmond.align.image_file_drift", return_value=[]),
                mock.patch("sigmond.align.recorded_release", return_value="v3.94"),
                mock.patch("sigmond.align.fetch_release", return_value=rel),
                mock.patch("sigmond.align_apply.alignment_summary", return_value=None),
                mock.patch("sigmond.catalog.load_catalog", return_value={}),
                *[mock.patch.object(smd, k, v) for k, v in self._builders().items()],
                *extra,
            ]:
                st.enter_context(p)
            st.enter_context(contextlib.redirect_stdout(out))
            rc = smd.cmd_align(args)
        return rc, out.getvalue()


class NativeStaleTests(_Rig):
    """`_align_native_stale`, the reader `Ctx.native_stale` is wired to."""

    def test_b4_no_record_binary_older_than_head_is_stale(self):
        self.assertEqual(smd._align_native_stale("ka9q-radio", self.base / "ka9q-radio"),
                         "no build record")

    def test_no_record_binary_newer_than_head_is_not_stale(self):
        now = datetime(2026, 11, 13, tzinfo=timezone.utc).timestamp()
        os.utime(self.radiod_bin, (now, now))
        self.assertIsNone(smd._align_native_stale("ka9q-radio", self.base / "ka9q-radio"))

    def test_record_behind_head_names_both_commits(self):
        self._record("ka9q-radio", self.old["ka9q-radio"])
        self.assertEqual(
            smd._align_native_stale("ka9q-radio", self.base / "ka9q-radio"),
            f"binary built from {self.old['ka9q-radio'][:7]}, "
            f"checkout at {self.new['ka9q-radio'][:7]}")

    def test_record_at_head_is_not_stale(self):
        self._record("ka9q-web", self.new["ka9q-web"])
        self.assertIsNone(smd._align_native_stale("ka9q-web", self.base / "ka9q-web"))

    def test_missing_web_record_is_stale(self):
        now = datetime(2026, 11, 13, tzinfo=timezone.utc).timestamp()
        os.utime(self.web_bin, (now, now))
        self.assertEqual(smd._align_native_stale("ka9q-web", self.base / "ka9q-web"),
                         "no build record")

    def test_locally_managed_radiod_is_never_stale(self):
        self.local = LocallyManaged(frozenset({"ka9q-radio"}), "Phil")
        self.assertIsNone(smd._align_native_stale("ka9q-radio", self.base / "ka9q-radio"))


class AlignApplyNativeRebuildTests(_Rig):

    def test_current_radiod_with_old_binary_rebuilds(self):
        """B4: no build record, binary 2026-10-25, HEAD 2026-11-12."""
        self._record("ka9q-web", self.new["ka9q-web"])
        rc, out, m_record = self._apply()
        radiod = [c for c in self.calls if c[0] == "ka9q-radio"]
        self.assertEqual(len(radiod), 1, out)
        self.assertEqual(radiod[0][1:3], (self.base / "ka9q-radio", True))
        self.assertFalse([c for c in self.calls if c[0] == "ka9q-web"], out)
        line = next(l for l in out.splitlines() if l.startswith("  ka9q-radio: current"))
        self.assertTrue(line.endswith("no build record; rebuilt"), out)
        self.assertIn("rebuilt 1", out)
        self.assertEqual(rc, 0, out)
        # The existing restart stage learns radiod was rebuilt in this run.
        self.assertEqual(self.staleness_calls[-1].get("rebuilt"), {"ka9q-radio"})
        steps = m_record.call_args[0][1]
        step = next(s for s in steps if s.component == "ka9q-radio")
        self.assertTrue(step.built)

    def test_moved_ka9q_web_builds_after_checkout(self):
        self._record("ka9q-radio", self.new["ka9q-radio"])
        self._record("ka9q-web", self.old["ka9q-web"])
        _git("checkout", "-q", "--detach", self.old["ka9q-web"], cwd=self.base / "ka9q-web")
        rc, out, _ = self._apply()
        web = [c for c in self.calls if c[0] == "ka9q-web"]
        self.assertEqual(len(web), 1, out)
        self.assertEqual(self.web_bin.read_text(), f"built from {self.new['ka9q-web']}\n")
        # Built at the release pin, not at the commit it moved from.
        self.assertEqual(web[0][3], self.new["ka9q-web"])
        self.assertFalse([c for c in self.calls if c[0] == "ka9q-radio"], out)
        self.assertIn("ka9q-web: moved", out)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.staleness_calls[-1].get("rebuilt"), {"ka9q-web"})

    def test_locally_managed_radiod_untouched(self):
        self.local = LocallyManaged(frozenset({"ka9q-radio"}), "Phil")
        self._record("ka9q-web", self.new["ka9q-web"])
        _git("checkout", "-q", "--detach", self.old["ka9q-radio"], cwd=self.base / "ka9q-radio")
        rc, out, _ = self._apply()
        self.assertEqual(self.calls, [], out)
        self.assertEqual(self._head("ka9q-radio"), self.old["ka9q-radio"])
        self.assertIn("ka9q-radio: left", out)
        self.assertNotIn("rebuilt", [w for line in out.splitlines()
                                     if line.strip().startswith("ka9q-radio")
                                     for w in line.split()])

    def test_ka9q_web_rebuild_failure_does_not_block_the_alignment(self):
        """Final review I3: a failed ka9q-web build exits 1 and leaves a
        native-build-failed marker, restarts no ka9q-web (its old binary
        runs on), and the rest of the live stages still run."""
        self._record("ka9q-radio", self.new["ka9q-radio"])
        self.build_ok["ka9q-web"] = False
        before = self.web_bin.read_text()
        marked = []
        rc, out, m_record = self._apply(
            stale={"ka9q-web": "its checkout moved after it started",
                   "psk-recorder": "its checkout moved after it started"},
            _mark_native_build_failed=lambda b, why: marked.append((b, why)))
        self.assertEqual(rc, 1, out)
        steps = m_record.call_args[0][1]
        step = next(s for s in steps if s.component == "ka9q-web")
        self.assertEqual(step.outcome, "current")
        self.assertTrue(step.build_failed)
        self.assertFalse(step.built)
        self.assertIn("no build record; rebuild failed", step.detail)
        self.assertEqual([m[0] for m in marked], ["ka9q-web"], out)
        self.assertEqual(self.web_bin.read_text(), before)
        # The rest of make_live ran: recorded, and the restart stage ran
        # for everything but ka9q-web.
        self.assertIn("  restarts:", out)
        self.assertEqual(self.restarts, [(False, ["psk-recorder"])], out)
        self.assertNotIn("ka9q-web", self.staleness_calls[-1].get("rebuilt"))
        self.assertIn("ka9q-web rebuild failed", out)

    def test_moved_ka9q_web_rebuild_failure_does_not_block(self):
        self._record("ka9q-radio", self.new["ka9q-radio"])
        self._record("ka9q-web", self.old["ka9q-web"])
        _git("checkout", "-q", "--detach", self.old["ka9q-web"], cwd=self.base / "ka9q-web")
        self.build_ok["ka9q-web"] = False
        rc, out, m_record = self._apply(
            stale={"ka9q-web": "its checkout moved after it started"},
            _mark_native_build_failed=lambda b, why: None)
        self.assertEqual(rc, 1, out)
        step = next(s for s in m_record.call_args[0][1] if s.component == "ka9q-web")
        self.assertEqual(step.outcome, "moved")
        self.assertTrue(step.build_failed)
        self.assertIn("  restarts:", out)
        self.assertFalse([c for rf, comps in self.restarts for c in comps
                          if c == "ka9q-web"], out)

    def test_radiod_rebuild_failure_fails_apply(self):
        """B4's case with a build that fails: the item fails, the apply
        exits nonzero, radiod's binary stays and nothing restarts."""
        self._record("ka9q-web", self.new["ka9q-web"])
        self.build_ok["ka9q-radio"] = False
        before = self.radiod_bin.read_text()
        rc, out, m_record = self._apply()
        self.assertNotEqual(rc, 0, out)
        steps = m_record.call_args[0][1]
        step = next(s for s in steps if s.component == "ka9q-radio")
        self.assertEqual(step.outcome, "failed")
        self.assertFalse(step.built)
        self.assertIn("no build record; rebuild failed", step.detail)
        self.assertEqual(len([c for c in self.calls if c[0] == "ka9q-radio"]), 1, out)
        self.assertEqual(self.radiod_bin.read_text(), before)
        self.assertNotIn("  restarts:", out)
        self.assertFalse([k for k in self.staleness_calls if "rebuilt" in k])
        self.assertFalse(self.marker.exists())

    def test_moved_radiod_builds_exactly_once(self):
        """_move builds a moved ka9q-radio, forced.  The stub writes no build
        record, so native_stale would call it stale if apply_plan asked
        again: one call proves it does not."""
        self._record("ka9q-web", self.new["ka9q-web"])
        _git("checkout", "-q", "--detach", self.old["ka9q-radio"], cwd=self.base / "ka9q-radio")
        rc, out, _ = self._apply()
        radiod = [c for c in self.calls if c[0] == "ka9q-radio"]
        self.assertEqual(len(radiod), 1, out)
        self.assertEqual(radiod[0][2:], (True, self.new["ka9q-radio"]))
        self.assertIn("ka9q-radio: moved", out)
        self.assertEqual(rc, 0, out)

    def test_rebuilt_radiod_leaves_a_restart_marker(self):
        """A rebuild notes the new binary's mtime, so a run that does not
        restart radiod (--no-restart, a failed restart) leaves the restart
        for the next run to find."""
        self._record("ka9q-web", self.new["ka9q-web"])
        rc, out, _ = self._apply()
        self.assertEqual(rc, 0, out)
        self.assertEqual(float(self.marker.read_text().strip()),
                         self.radiod_bin.stat().st_mtime)


class AlignDryRunNativeRebuildTests(_Rig):

    def test_dry_run_names_rebuild_and_changes_nothing(self):
        self._record("ka9q-web", self.old["ka9q-web"])
        heads = self._live()
        records = {p.name: p.read_text() for p in self.manifests.iterdir()}
        rc, out = self._dry_run()
        self.assertEqual(self.calls, [], out)
        self.assertEqual(self._live(), heads)
        self.assertEqual({p.name: p.read_text() for p in self.manifests.iterdir()}, records)
        radiod = next(l for l in out.splitlines() if l.strip().startswith("ka9q-radio "))
        web = next(l for l in out.splitlines() if l.strip().startswith("ka9q-web "))
        self.assertIn("current", radiod)
        self.assertIn("-- no build record; --apply REBUILDS and RESTARTS radiod -- "
                      "recording gap of about 60 s", radiod)
        self.assertIn(f"-- binary built from {self.old['ka9q-web'][:7]}, checkout at "
                      f"{self.new['ka9q-web'][:7]}; --apply REBUILDS", web)
        self.assertNotIn("RESTARTS radiod", web)
        self.assertTrue(radiod.isascii() and web.isascii(), out)
        self.assertIn("2 to rebuild", out)
        self.assertEqual(rc, 1, out)

    def test_all_current_prints_no_rebuild(self):
        self._record("ka9q-radio", self.new["ka9q-radio"])
        self._record("ka9q-web", self.new["ka9q-web"])
        rc, out = self._dry_run()
        self.assertEqual(self.calls, [], out)
        self.assertNotIn("REBUILDS", out)
        self.assertIn("0 to rebuild", out)
        self.assertEqual(rc, 0, out)


class AlignStalenessRebuiltTests(unittest.TestCase):
    """The existing restart stage, told what this run rebuilt, restarts
    radiod even though its checkout did not move after radiod started."""

    def test_rebuilt_radiod_is_stale_without_a_checkout_move(self):
        services = {"ka9q-radio": ["radiod@rx888.service"]}
        start = 1000.0
        with mock.patch.object(smd, "_align_units_started_at", return_value=start), \
             mock.patch.object(smd, "_align_head_moved_at", return_value=500.0), \
             mock.patch.object(smd, "_align_consumes", return_value={}), \
             mock.patch.object(smd, "_align_radiod_built_at", return_value=2000.0), \
             mock.patch.object(smd, "_locally_managed", return_value=LocallyManaged()):
            quiet = smd._align_staleness("/x", services, {})
            rebuilt = smd._align_staleness("/x", services, {}, rebuilt={"ka9q-radio"})
        self.assertNotIn("ka9q-radio", quiet["stale"])
        self.assertIn("ka9q-radio", rebuilt["stale"])


class AlignRadiodRebuiltNoteSafetyTests(_Rig):
    """Fix round 2: /var/lib/sigmond is writable by service accounts, so
    the note is written and read without following a planted symlink."""

    def setUp(self):
        super().setUp()
        self._record("ka9q-web", self.new["ka9q-web"])
        self.victim = Path(self._tmp.name) / "victim"
        self.victim.write_text("precious\n")

    def test_symlinks_at_note_and_tmp_names_leave_the_target_untouched(self):
        self.marker.symlink_to(self.victim)
        tmp_link = self.marker.with_name(self.marker.name + ".tmp")
        tmp_link.symlink_to(self.victim)
        rc, out, _ = self._apply()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.victim.read_text(), "precious\n")
        # The link itself was replaced by a regular file holding the note.
        self.assertFalse(self.marker.is_symlink())
        self.assertEqual(float(self.marker.read_text().strip()),
                         self.radiod_bin.stat().st_mtime)
        self.assertTrue(tmp_link.is_symlink())       # never written through

    def test_symlinked_parent_skips_the_note_with_a_warning(self):
        real = Path(self._tmp.name) / "real-state"
        real.mkdir()
        linked = Path(self._tmp.name) / "linked-state"
        linked.symlink_to(real)
        err = io.StringIO()
        with mock.patch.object(smd, "_ALIGN_RADIOD_REBUILT", linked / "align-radiod-rebuilt"), \
             contextlib.redirect_stderr(err):
            rc, out, _ = self._apply()
        self.assertEqual(rc, 0, out)
        self.assertEqual(list(real.iterdir()), [])
        self.assertIn("could not note the radiod rebuild", out + err.getvalue())

    def test_a_symlinked_note_reads_as_no_note(self):
        noted = Path(self._tmp.name) / "planted"
        noted.write_text(f"{self.radiod_bin.stat().st_mtime!r}\n")
        self.marker.symlink_to(noted)
        self.assertIsNone(smd._align_radiod_rebuilt_at())


class AlignRadiodRestartDueTests(_Rig):
    """Reviewer Minor 1: a radiod align rebuilt but did not restart stays a
    restart due on later runs, through the marker, while an identical-bytes
    reinstall by `smd update` / `smd install --force` (no marker) still
    restarts nothing."""

    START = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc).timestamp()

    def _staleness(self):
        services = {"ka9q-radio": ["radiod@rx888.service"], "ka9q-web": ["ka9q-web.service"]}
        cat = {"ka9q-web": type("E", (), {"requires": ("ka9q-radio",)})()}
        with mock.patch.object(smd, "_align_units_started_at", return_value=self.START), \
             mock.patch.object(smd, "_align_head_moved_at", return_value=self.START - 3600), \
             mock.patch.object(smd, "_align_consumes", return_value={}):
            return smd._align_staleness(str(self.base), services, cat)

    def _rebuild_now(self):
        built = self.START + 600
        os.utime(self.radiod_bin, (built, built))
        smd._align_note_radiod_rebuilt()
        return built

    def test_rebuilt_not_restarted_is_due_next_run(self):
        self._rebuild_now()
        st = self._staleness()
        self.assertEqual(st["stale"].get("ka9q-radio"), smd._ALIGN_RADIOD_REBUILT_REASON)
        # ...and its consumers follow, as for any radiod restart.
        first, comps, _ = smd._align_plan_restarts([], [], st, say=lambda m: None)
        self.assertTrue(first)
        self.assertIn("ka9q-web", comps)

    def test_identical_bytes_reinstall_without_marker_restarts_nothing(self):
        later = self.START + 600
        os.utime(self.radiod_bin, (later, later))
        self.assertNotIn("ka9q-radio", self._staleness()["stale"])

    def test_reinstall_after_the_marker_falls_back_to_the_guard(self):
        self._rebuild_now()
        later = self.START + 1200
        os.utime(self.radiod_bin, (later, later))     # another install since
        self.assertNotIn("ka9q-radio", self._staleness()["stale"])

    def test_radiod_restarted_since_is_not_due(self):
        self._rebuild_now()
        self.START += 3600
        self.assertNotIn("ka9q-radio", self._staleness()["stale"])

    def test_locally_managed_radiod_is_never_due(self):
        self._rebuild_now()
        self.local = LocallyManaged(frozenset({"ka9q-radio"}), "Phil")
        self.assertNotIn("ka9q-radio", self._staleness()["stale"])

    def test_dry_run_names_the_restart(self):
        self._rebuild_now()
        lines = []
        with mock.patch.object(smd, "_align_services",
                               return_value={"ka9q-radio": ["radiod@rx888.service"]}), \
             mock.patch.object(smd, "_align_units_started_at", return_value=self.START), \
             mock.patch.object(smd, "_align_head_moved_at", return_value=self.START - 3600), \
             mock.patch.object(smd, "_align_consumes", return_value={}):
            n = smd._align_print_restarts(str(self.base), [], catalog={}, say=lines.append)
        radiod = next(l for l in lines if l.strip().startswith("radiod"))
        self.assertIn("-- binary rebuilt after radiod started; --apply RESTARTS radiod", radiod)
        self.assertTrue(radiod.isascii(), radiod)
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
