"""Choosing between the two decoder builds — and refusing to choose wrongly.

Sigmond builds WSJT-X's `wsprd`/`jt9` and UberSDR's drop-in rewrites of the
same two, keeps BOTH under /usr/local/libexec/sigmond/decoders/, and points
/usr/local/bin/{wsprd,jt9} at one of them.  Clients resolve the decoders by
name off PATH, so the symlink is the whole switch: no client change, and
falling back costs a symlink swap rather than a five-minute Qt rebuild on a
station that is already failing.

⚠ What makes this worth testing is the shape of the failure it guards.  A
decoder that is wrong does not crash.  It decodes *slightly differently*, and
the station keeps reporting, and the spots are wrong for as long as nobody
looks.  So selection is gated on the parity check having passed, and these
tests are mostly about REFUSALS: the paths where selection must not happen.

The second guard is `-march`.  Upstream builds for x86-64-v3 (AVX2, FMA,
BMI2) by default.  On an older CPU that binary does not run slowly — it dies
with SIGILL on its first AVX2 instruction, which from wspr-recorder's side is
indistinguishable from a decoder that decodes nothing.  _decoder_x86_arch
must drop to the compiler baseline rather than ship a binary that cannot run.
"""
import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock


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

_FLAGS_V3 = "flags\t\t: fpu vme de pse avx2 bmi2 fma sse4_2\n"
_FLAGS_OLD = "flags\t\t: fpu vme de pse sse4_2\n"


class Arch(unittest.TestCase):
    """_decoder_x86_arch probes rather than assumes."""

    def _arch(self, machine, cpuinfo):
        with mock.patch("platform.machine", return_value=machine), \
             mock.patch.object(_SMD.Path, "read_text", return_value=cpuinfo):
            return _SMD._decoder_x86_arch()

    def test_modern_x86_gets_upstreams_tuned_default(self):
        self.assertEqual(self._arch("x86_64", _FLAGS_V3), "x86-64-v3")

    def test_cpu_without_avx2_falls_back_to_baseline(self):
        # The whole point: a v3 binary on this CPU SIGILLs on first use.
        self.assertEqual(self._arch("x86_64", _FLAGS_OLD), "")

    def test_cpu_missing_only_bmi2_still_falls_back(self):
        # x86-64-v3 is all three or nothing; two of three is not enough.
        flags = "flags\t\t: avx2 fma sse4_2\n"
        self.assertEqual(self._arch("x86_64", flags), "")

    def test_arm_gets_no_march_at_all(self):
        # CMakeLists applies -march only on x86_64; NEON is always there.
        self.assertEqual(self._arch("aarch64", _FLAGS_V3), "")

    def test_unreadable_cpuinfo_does_not_assume_avx2(self):
        with mock.patch("platform.machine", return_value="x86_64"), \
             mock.patch.object(_SMD.Path, "read_text",
                               side_effect=OSError("nope")):
            self.assertEqual(_SMD._decoder_x86_arch(), "")


class Preference(unittest.TestCase):
    """[decoders] implementation in coordination.toml, and its defaults."""

    def _pref(self, text):
        if text is None:
            ctx = mock.patch.object(_SMD.Path, "read_text",
                                    side_effect=OSError("absent"))
        else:
            ctx = mock.patch.object(_SMD.Path, "read_text", return_value=text)
        with ctx:
            return _SMD._decoder_preference()

    def test_default_is_ubersdr(self):
        # rob, 2026-10-24: "we're not gonna go backwards."
        self.assertEqual(self._pref(""), "ubersdr")

    def test_absent_config_is_the_default_not_an_error(self):
        self.assertEqual(self._pref(None), "ubersdr")

    def test_operator_can_pin_the_wsjtx_build(self):
        self.assertEqual(
            self._pref('[decoders]\nimplementation = "wsjtx"\n'), "wsjtx")

    def test_case_and_space_are_forgiven(self):
        self.assertEqual(
            self._pref('[decoders]\nimplementation = " WSJTX "\n'), "wsjtx")

    def test_an_unknown_name_warns_and_uses_the_default(self):
        # Never silently decode with something nobody named.
        with mock.patch.object(_SMD, "_warn") as warn:
            got = self._pref('[decoders]\nimplementation = "ft8onacid"\n')
        self.assertEqual(got, "ubersdr")
        self.assertTrue(warn.called, "an unknown implementation must warn")

    def test_malformed_toml_is_the_default_not_a_crash(self):
        self.assertEqual(self._pref("[decoders\nimplementation ="), "ubersdr")


class Selection(unittest.TestCase):
    """_select_decoders links, or refuses and says why."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp,
                        ignore_errors=True)
        self.calls = []

        def fake_run(cmd, **kw):
            self.calls.append([str(c) for c in cmd])
            return __import__("types").SimpleNamespace(
                returncode=0, stdout="", stderr="")

        self.p = [
            mock.patch.object(_SMD, "_DECODER_DIR", self.tmp / "decoders"),
            mock.patch.object(_SMD, "_run", fake_run),
            mock.patch.object(_SMD, "_adopt_legacy_decoders", lambda: None),
            mock.patch.object(_SMD, "_ok"), mock.patch.object(_SMD, "_err"),
            mock.patch.object(_SMD, "_warn"), mock.patch.object(_SMD, "_info"),
        ]
        for q in self.p:
            q.start()
            self.addCleanup(q.stop)

    def _install(self, impl, provenance="parity=verified"):
        d = self.tmp / "decoders" / impl
        d.mkdir(parents=True, exist_ok=True)
        for b in ("wsprd", "jt9"):
            (d / b).write_text("#!/bin/true\n")
        if provenance is not None:
            (d / ".provenance").write_text(
                f"ubersdr-decoders abc123 {provenance}\n")
        return d

    def _links(self):
        return [c for c in self.calls if c and c[0] == "ln"]

    def test_verified_ubersdr_is_linked(self):
        self._install("ubersdr")
        self.assertTrue(_SMD._select_decoders("ubersdr"))
        links = self._links()
        self.assertEqual(len(links), 2, "both wsprd and jt9 must be pointed")
        for c in links:
            self.assertIn("decoders/ubersdr/", c[2])

    def test_links_are_relative_so_the_pair_survives_a_move(self):
        self._install("ubersdr")
        _SMD._select_decoders("ubersdr")
        for c in self._links():
            self.assertFalse(c[2].startswith("/"),
                             f"link target must be relative: {c[2]}")

    def test_link_replaces_the_link_itself_not_a_dir_entry(self):
        # ln -sfn without -T into an existing symlink-to-dir makes a link
        # INSIDE it.  -T is what keeps /usr/local/bin/wsprd being the link.
        self._install("ubersdr")
        _SMD._select_decoders("ubersdr")
        for c in self._links():
            self.assertIn("T", c[1], f"ln needs -T: {c}")

    def test_unbuilt_implementation_is_refused(self):
        self.assertFalse(_SMD._select_decoders("wsjtx"))
        self.assertEqual(self._links(), [])

    def test_half_built_implementation_is_refused(self):
        d = self.tmp / "decoders" / "ubersdr"
        d.mkdir(parents=True)
        (d / "wsprd").write_text("x")          # jt9 missing
        (d / ".provenance").write_text("parity=verified")
        self.assertFalse(_SMD._select_decoders("ubersdr"))
        self.assertEqual(self._links(), [])

    def test_unverified_ubersdr_is_refused(self):
        # ⛔ The core guard.  A build that did not match WSJT-X must never
        # become the thing the station decodes with.
        self._install("ubersdr", provenance="parity=SKIPPED")
        self.assertFalse(_SMD._select_decoders("ubersdr"))
        self.assertEqual(self._links(), [])

    def test_ubersdr_without_a_provenance_marker_is_refused(self):
        self._install("ubersdr", provenance=None)
        self.assertFalse(_SMD._select_decoders("ubersdr"))
        self.assertEqual(self._links(), [])

    def test_wsjtx_needs_no_parity_marker(self):
        # It IS the reference; there is nothing for it to differ from.
        self._install("wsjtx", provenance=None)
        self.assertTrue(_SMD._select_decoders("wsjtx"))
        self.assertEqual(len(self._links()), 2)

    def test_unknown_implementation_is_refused(self):
        # ⚠ Install it fully — binaries AND a verified marker — so the
        # refusal can only come from the name check.  Without this the test
        # passed on an smd whose name check had been deleted: the unknown
        # name simply had no directory, and the missing-binaries path caught
        # it.  A test that passes for the wrong reason is not a test.
        self._install("nonesuch")
        self.assertFalse(_SMD._select_decoders("nonesuch"))
        self.assertEqual(self._links(), [])

    def test_fallback_needs_no_rebuild(self):
        # Both present; switching is two symlinks and nothing else.  This is
        # the property the whole libexec split exists to provide.
        self._install("ubersdr")
        self._install("wsjtx", provenance=None)
        self.assertTrue(_SMD._select_decoders("ubersdr"))
        self.calls.clear()
        self.assertTrue(_SMD._select_decoders("wsjtx"))
        self.assertEqual(len(self._links()), 2)
        self.assertTrue(all(c[0] == "ln" for c in self.calls),
                        f"a fallback must only re-link, ran: {self.calls}")


class Adoption(unittest.TestCase):
    """An upgrade must not throw away the decoder the station already has."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp,
                        ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = []

        def fake_run(cmd, **kw):
            cmd = [str(c) for c in cmd]
            self.calls.append(cmd)
            if cmd[0] == "install" and cmd[1] == "-d":
                Path(cmd[2]).mkdir(parents=True, exist_ok=True)
            elif cmd[0] in ("mv", "cp"):
                dst = Path(cmd[2])
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(Path(cmd[1]).read_text())
                if cmd[0] == "mv":
                    Path(cmd[1]).unlink()
            return __import__("types").SimpleNamespace(
                returncode=0, stdout="", stderr="")

        self.p = [
            mock.patch.object(_SMD, "_DECODER_DIR", self.tmp / "decoders"),
            mock.patch.object(_SMD, "_run", fake_run),
            mock.patch.object(_SMD, "_info"),
        ]
        for q in self.p:
            q.start()
            self.addCleanup(q.stop)

    def _adopt(self):
        # _adopt_legacy_decoders reads /usr/local/bin; point it at the fake.
        real = _SMD.Path

        class P(type(Path())):
            pass

        with mock.patch.object(_SMD, "Path",
                               lambda s="": real(str(s).replace(
                                   "/usr/local/bin", str(self.bin)))):
            _SMD._adopt_legacy_decoders()

    def test_a_pre_split_binary_is_moved_not_clobbered(self):
        (self.bin / "wsprd").write_text("the old real binary")
        (self.bin / "jt9").write_text("the old real jt9")
        self._adopt()
        moved = self.tmp / "decoders" / "wsjtx"
        self.assertEqual((moved / "wsprd").read_text(), "the old real binary")
        self.assertFalse((self.bin / "wsprd").exists(),
                         "the old file must move out of the way of the link")

    def test_its_provenance_moves_with_it(self):
        (self.bin / "wsprd").write_text("x")
        (self.bin / ".wsjtx-decoders.provenance").write_text("wsjtx v3.0.2 cc\n")
        self._adopt()
        self.assertIn("v3.0.2",
                      (self.tmp / "decoders" / "wsjtx" / ".provenance").read_text())

    def test_adoption_is_idempotent(self):
        (self.bin / "wsprd").write_text("x")
        self._adopt()
        n = len(self.calls)
        self._adopt()
        self.assertEqual(len(self.calls), n,
                         "a second adoption must do nothing")

    def test_an_already_split_install_is_left_alone(self):
        (self.tmp / "decoders" / "wsjtx").mkdir(parents=True)
        (self.tmp / "decoders" / "wsjtx" / "wsprd").write_text("already here")
        (self.bin / "wsprd").write_text("newer, do not overwrite")
        self._adopt()
        self.assertEqual(
            (self.tmp / "decoders" / "wsjtx" / "wsprd").read_text(),
            "already here")


class CombinedBuild(unittest.TestCase):
    """_build_decoders: what happens when one of the two builds fails.

    The asymmetry is the point.  The WSJT-X build is a hard requirement —
    psk-recorder's jt9_decode drives THAT jt9 over Qt5 shared memory, and a
    fallback that must be built before it can be used is not a fallback.
    UberSDR failing is not fatal: a station that cannot build or verify the
    new decoders should keep decoding with the old ones and say so, rather
    than fail an install over a performance improvement.
    """

    def setUp(self):
        self.p = [mock.patch.object(_SMD, "_adopt_legacy_decoders",
                                    lambda: None),
                  mock.patch.object(_SMD, "_warn"),
                  mock.patch.object(_SMD, "_ok"), mock.patch.object(_SMD, "_err")]
        for q in self.p:
            q.start()
            self.addCleanup(q.stop)

    def _run(self, wsjtx, ubersdr, want="ubersdr"):
        picked = []
        with mock.patch.object(_SMD, "_build_wsjtx_decoders",
                               lambda: wsjtx), \
             mock.patch.object(_SMD, "_build_ubersdr_decoders",
                               lambda **k: ubersdr), \
             mock.patch.object(_SMD, "_decoder_preference", lambda: want), \
             mock.patch.object(_SMD, "_select_decoders",
                               lambda i: picked.append(i) or True):
            ok = _SMD._build_decoders()
        return ok, picked

    def test_both_build_and_the_wanted_one_is_selected(self):
        self.assertEqual(self._run(True, True), (True, ["ubersdr"]))

    def test_a_failed_wsjtx_build_fails_the_install(self):
        ok, picked = self._run(False, True)
        self.assertFalse(ok)
        self.assertEqual(picked, [], "nothing may be selected without the "
                                     "fallback in place")

    def test_a_failed_ubersdr_build_falls_back_rather_than_failing(self):
        # ⚠ A performance improvement must not be able to fail an install.
        ok, picked = self._run(True, False)
        self.assertTrue(ok)
        self.assertEqual(picked, ["wsjtx"])

    def test_the_fallback_is_announced_not_silent(self):
        with mock.patch.object(_SMD, "_warn") as warn:
            self._run(True, False)
        self.assertTrue(warn.called,
                        "falling back must say so — a station quietly on the "
                        "old decoder looks identical to one on the new")

    def test_wsjtx_preference_does_not_build_ubersdr(self):
        # An operator who pinned wsjtx should not pay for a build they
        # asked not to use.
        built = []
        with mock.patch.object(_SMD, "_build_wsjtx_decoders", lambda: True), \
             mock.patch.object(_SMD, "_build_ubersdr_decoders",
                               lambda **k: built.append(1) or True), \
             mock.patch.object(_SMD, "_decoder_preference", lambda: "wsjtx"), \
             mock.patch.object(_SMD, "_select_decoders", lambda i: True):
            _SMD._build_decoders()
        self.assertEqual(built, [])


class DoctorFindings(unittest.TestCase):
    """What doctor says about a station decoding with the wrong build.

    Every case here is one the station survives.  It keeps decoding, keeps
    uploading, and reports healthy — which is precisely why a check has to
    look, and why none of these can be left to a service failing.
    """

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp,
                        ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.dec = self.tmp / "decoders"
        self.bin.mkdir()

    def _build(self, impl):
        d = self.dec / impl
        d.mkdir(parents=True, exist_ok=True)
        for b in ("wsprd", "jt9"):
            (d / b).write_text("x")
        return d

    def _link(self, name, impl, target=None):
        (self.bin / name).symlink_to(target or (self.dec / impl / name))

    def _find(self, want="ubersdr"):
        return _SMD._decoder_findings(bindir=self.bin, decdir=self.dec,
                                      want=want)

    def _kinds(self, want="ubersdr"):
        return sorted(f.kind for f in self._find(want))

    def test_a_host_with_no_decoders_says_nothing(self):
        self.assertEqual(self._find(), [])

    def test_a_correctly_selected_station_is_clean(self):
        self._build("ubersdr")
        self._link("wsprd", "ubersdr")
        self._link("jt9", "ubersdr")
        self.assertEqual(self._find(), [])

    def test_wsprd_and_jt9_from_different_builds_is_reported(self):
        # ⛔ Nobody writes this on purpose and nothing else notices: both
        # decode, so every service stays green.
        self._build("ubersdr")
        self._build("wsjtx")
        self._link("wsprd", "ubersdr")
        self._link("jt9", "wsjtx")
        self.assertIn("decoder-split", self._kinds())

    def test_a_split_is_reported_even_when_one_half_is_the_wanted_one(self):
        self._build("ubersdr")
        self._build("wsjtx")
        self._link("wsprd", "wsjtx")
        self._link("jt9", "ubersdr")
        self.assertIn("decoder-split", self._kinds(want="ubersdr"))

    def test_an_unapplied_config_change_is_reported(self):
        self._build("wsjtx")
        self._link("wsprd", "wsjtx")
        self._link("jt9", "wsjtx")
        self.assertEqual(self._kinds(want="ubersdr"),
                         ["decoder-not-selected"])

    def test_the_advice_names_the_command_that_fixes_it(self):
        self._build("wsjtx")
        self._link("wsprd", "wsjtx")
        self._link("jt9", "wsjtx")
        detail = self._find(want="ubersdr")[0].detail
        self.assertIn("--select ubersdr", detail)

    def test_a_dangling_link_is_reported(self):
        # Does not read as "no decoder" at runtime — it reads as whatever
        # wspr-recorder makes of an exec failure, every cycle.
        self._build("ubersdr")
        self._link("wsprd", "ubersdr", target=self.dec / "gone" / "wsprd")
        self._link("jt9", "ubersdr")
        self.assertIn("decoder-dangling", self._kinds())

    def test_a_hand_placed_real_binary_is_reported(self):
        self._build("ubersdr")
        (self.bin / "wsprd").write_text("someone built this by hand")
        self._link("jt9", "ubersdr")
        self.assertIn("decoder-unmanaged", self._kinds())

    def test_findings_name_a_component_so_doctor_can_group_them(self):
        self._build("wsjtx")
        self._link("wsprd", "wsjtx")
        self._link("jt9", "wsjtx")
        for f in self._find(want="ubersdr"):
            self.assertTrue(f.component, "a finding needs a component")


if __name__ == "__main__":
    unittest.main()
