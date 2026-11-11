"""The station pin set (/etc/sigmond/component-pins.toml), track-main mode
(SIGMOND_TRACK_MAIN=1) and `smd update --latest` (rob, 2026-11-11: build
each image from the newest commit of every repo's main, test that set, and
pin stations to it)."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent


def _load_smd():
    os.environ.setdefault("SIGMOND_NO_VENV_REEXEC", "1")
    loader = importlib.machinery.SourceFileLoader(
        "smd_under_test_component_pins", str(REPO / "bin" / "smd"))
    spec = importlib.util.spec_from_loader(
        "smd_under_test_component_pins", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


smd = _load_smd()
A, B, C, D = "a" * 40, "b" * 40, "c" * 40, "d" * 40


class _PinFile(unittest.TestCase):
    """Each test gets its own pin file path, no track-main, no cache."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pins = Path(self.tmp.name) / "component-pins.toml"
        for p in (mock.patch.object(smd, "_COMPONENT_PINS_FILE", self.pins),
                  mock.patch.dict(os.environ, {}, clear=False),
                  mock.patch.object(smd, "_REMOTE_HEADS", {})):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("SIGMOND_TRACK_MAIN", None)

    def write(self, **pins):
        body = "\n".join(f'"{k}" = "{v}"' for k, v in pins.items())
        self.pins.write_text(f"[pins]\n{body}\n")


class PinFilePrecedence(_PinFile):
    def test_no_file_means_the_constants(self):
        self.assertEqual(smd._native_build_pins(),
                         {"onion": smd._ONION_COMMIT,
                          "wsjtx": smd._WSJTX_COMMIT,
                          "ubersdr_wsprd": smd._UBERSDR_COMMIT})

    def test_an_entry_wins_over_the_constant_everywhere(self):
        self.write(onion=A, wsjtx=B, ubersdr_wsprd=C)
        self.assertEqual(smd._native_build_pins(),
                         {"onion": A, "wsjtx": B, "ubersdr_wsprd": C})
        self.assertEqual(smd._expected_build_pins(),
                         {"ka9q-web": {"onion": A},
                          "wsjtx-decoders": {"wsjtx": B},
                          "ubersdr-decoders": {"ubersdr_wsprd": C}})

    def test_a_partial_file_keeps_the_other_constants(self):
        self.write(onion=A)
        pins = smd._native_build_pins()
        self.assertEqual(pins["onion"], A)
        self.assertEqual(pins["wsjtx"], smd._WSJTX_COMMIT)

    def test_a_malformed_sha_is_ignored(self):
        self.write(onion="deadbeef", wsjtx="not a sha")
        self.assertEqual(smd._native_pin("onion"), smd._ONION_COMMIT)
        self.assertEqual(smd._native_pin("wsjtx"), smd._WSJTX_COMMIT)

    def test_an_unparsable_file_is_ignored(self):
        self.pins.write_text("[pins\n")
        self.assertEqual(smd._read_component_pins(), {})

    def test_ka9q_radio_pin_belongs_to_ka9q_python_alone(self):
        # A ka9q-radio entry in the pin file, and track-main, are both
        # ignored: ka9q-python's compat commit is the one owner (mjh, 11-11).
        self.pins.write_text(f'[pins]\n"ka9q-radio" = "{D}"\n')
        os.environ["SIGMOND_TRACK_MAIN"] = "1"
        fake = mock.MagicMock(KA9Q_RADIO_COMMIT=C)
        with mock.patch.dict("sys.modules", {"ka9q.compat": fake}), \
             mock.patch.object(smd, "_run") as run:
            self.assertEqual(smd._ka9q_radio_pin(), C)
        run.assert_not_called()

    def test_wsjtx_label_names_the_tag_only_at_the_tagged_commit(self):
        self.assertEqual(smd._wsjtx_ref_label(smd._WSJTX_COMMIT),
                         smd._WSJTX_TAG)
        self.assertEqual(smd._wsjtx_ref_label(B), "main@bbbbbbbbb")


class TrackMain(_PinFile):
    def _ls_remote(self, heads):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            url = argv[2]
            sha = heads.get(url, "")
            return subprocess.CompletedProcess(
                argv, 0 if sha else 128,
                f"{sha}\tHEAD\n" if sha else "", "" if sha else "fatal")
        return calls, mock.patch.object(smd, "_run", side_effect=fake_run)

    def test_track_main_resolves_each_source_to_its_remote_head(self):
        os.environ["SIGMOND_TRACK_MAIN"] = "1"
        self.write(onion=A)    # the pin file loses to track-main
        calls, p = self._ls_remote({smd._ONION_REPO: B,
                                    smd._WSJTX_REPO: C,
                                    smd._UBERSDR_REPO: D})
        with p:
            # wsjtx is pinned on purpose: track-main leaves it at its pin.
            self.assertEqual(smd._native_build_pins(),
                             {"onion": B, "wsjtx": smd._WSJTX_COMMIT,
                              "ubersdr_wsprd": D})
            smd._native_pin("onion")
        self.assertTrue(all(c[:3] == ["git", "ls-remote", c[2]]
                            and c[3] == "HEAD" for c in calls))
        # one ls-remote per url per run, none for wsjtx or ka9q-radio
        self.assertEqual(len(calls), 2)
        self.assertNotIn(smd._WSJTX_REPO, [c[2] for c in calls])

    def test_unreachable_remote_falls_back_to_the_pin(self):
        os.environ["SIGMOND_TRACK_MAIN"] = "1"
        self.write(wsjtx=B)
        _, p = self._ls_remote({})
        with p, mock.patch.object(smd, "_warn") as warn:
            self.assertEqual(smd._native_pin("wsjtx"), B)
            self.assertEqual(smd._native_pin("onion"), smd._ONION_COMMIT)
            self.assertEqual(smd._native_pin("ubersdr_wsprd"), smd._UBERSDR_COMMIT)
        self.assertTrue(warn.called)

    def test_without_the_env_ls_remote_is_never_called(self):
        _, p = self._ls_remote({smd._ONION_REPO: B})
        with p as run:
            smd._native_build_pins()
        run.assert_not_called()

    def test_a_tracked_build_records_its_commit_as_root(self):
        os.environ["SIGMOND_TRACK_MAIN"] = "1"
        self.write(onion=A)
        with mock.patch.object(smd.os, "geteuid", return_value=0):
            smd._pin_if_tracking("wsjtx", C)
        pins = smd._read_component_pins()
        self.assertEqual(pins, {"onion": A, "wsjtx": C})
        self.assertEqual(oct(self.pins.stat().st_mode & 0o777), "0o644")

    def test_an_untracked_build_leaves_the_file_alone(self):
        with mock.patch.object(smd, "_record_component_pins") as rec:
            smd._pin_if_tracking("wsjtx", C)
        rec.assert_not_called()


def _row(name, head, tip, kind="component", **kw):
    r = {"name": name, "head": head, "tip": tip, "behind": 3, "kind": kind,
         "builder": smd._NATIVE_BUILD_OF.get(name, name),
         "installer": False, "build": False, "dirty": False}
    r.update(kw)
    return r


class PlanLatest(unittest.TestCase):
    def test_everything_at_its_tip_plans_nothing(self):
        self.assertEqual(smd._plan_latest([_row("hf-timestd", A, A),
                                           _row("onion", B, B, "native")]),
                         [])

    def test_order_and_kinds(self):
        rows = [
            _row("ubersdr_wsprd", A, B, "native"),
            _row("ka9q-radio", A, B, "radiod"),
            _row("hf-timestd", A, B, installer=True),
            _row("hamsci-physics", A, B, build=True),
            _row("callhash", A, A),
            _row("onion", C, D, "native"),
            _row("wsjtx", C, D, "native"),
        ]
        plan = smd._plan_latest(rows)
        self.assertEqual([(k, t) for k, t, _ in plan], [
            ("held", "wsjtx"),
            ("move", "hamsci-physics"), ("build", "hamsci-physics"),
            ("move", "hf-timestd"), ("install", "hf-timestd"),
            ("radiod", "ka9q-radio"),
            ("native-build", "ka9q-web"),
            ("native-build", "ubersdr-decoders"),
            ("restart", "services"),
            ("pins", str(smd._COMPONENT_PINS_FILE)),
        ])

    def test_a_held_pin_is_moved_but_local_edits_are_not(self):
        plan = smd._plan_latest([_row("psk-recorder", A, B, dirty=True)])
        self.assertEqual(plan[0][:2], ("held", "psk-recorder"))
        self.assertFalse([s for s in plan if s[0] == "move"])

    def test_an_unreadable_default_branch_is_reported_not_moved(self):
        plan = smd._plan_latest([_row("igmp-querier", A, "")])
        self.assertEqual(plan[0][:2], ("unknown", "igmp-querier"))
        self.assertNotIn("restart", [s[0] for s in plan])

    def test_one_native_build_per_builder(self):
        plan = smd._plan_latest([_row("onion", A, B, "native",
                                      builder="ka9q-web"),
                                 _row("onion2", A, B, "native",
                                      builder="ka9q-web")])
        self.assertEqual([s for s in plan if s[0] == "native-build"],
                         [("native-build", "ka9q-web", mock.ANY)])

    def test_pinned_on_purpose_is_held_not_moved(self):
        plan = smd._plan_latest([_row("wsjtx", A, B, "native",
                                      builder="wsjtx-decoders"),
                                 _row("ft8_lib", A, B)])
        self.assertEqual(sorted(s[:2] for s in plan),
                         [("held", "ft8_lib"), ("held", "wsjtx")])


class ChildCarriesTrackMain(unittest.TestCase):
    def _argv(self, euid, track_main):
        seen = {}

        def fake(argv, **kw):
            seen["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, "", "")
        with mock.patch.object(smd.subprocess, "run", side_effect=fake), \
             mock.patch.object(smd.os, "geteuid", return_value=euid), \
             mock.patch.object(smd, "_write_update_log", return_value=None), \
             mock.patch("builtins.print"):
            smd._run_smd_child(["restart"], "restart", "restart",
                               track_main=track_main)
        return seen["argv"]

    def test_sudo_env_line_names_track_main(self):
        argv = self._argv(1000, True)
        self.assertEqual(argv[:5], ["sudo", "-n", "env",
                                    "SIGMOND_ALLOW_SUDO=1",
                                    "SIGMOND_TRACK_MAIN=1"])

    def test_root_still_passes_it(self):
        self.assertEqual(self._argv(0, True)[:2],
                         ["env", "SIGMOND_TRACK_MAIN=1"])

    def test_without_track_main_the_argv_is_unchanged(self):
        argv = self._argv(1000, False)
        self.assertNotIn("SIGMOND_TRACK_MAIN=1", argv)
        self.assertEqual(argv[:4], ["sudo", "-n", "env",
                                    "SIGMOND_ALLOW_SUDO=1"])


class Parser(unittest.TestCase):
    def test_update_takes_latest(self):
        args = smd._build_parser().parse_args(["update", "--latest"])
        self.assertTrue(args.latest)
        self.assertFalse(args.apply)
        args = smd._build_parser().parse_args(["update"])
        self.assertFalse(args.latest)


class TrackMainSurvivesSelfElevation(unittest.TestCase):
    """smd re-execs itself under sudo, whose env_reset drops variables: the
    image build's SIGMOND_TRACK_MAIN=1 must reach the root smd."""

    def _argv(self, env):
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(smd.os, "geteuid", return_value=1000), \
             mock.patch.object(smd.shutil, "which", return_value="/usr/bin/sudo"), \
             mock.patch.object(smd.os, "execvp", side_effect=SystemExit) as ex:
            os.environ.pop("SIGMOND_TEST_NO_ELEVATE", None)
            with self.assertRaises(SystemExit):
                smd._need_root("install")
        return ex.call_args[0][1]

    def test_track_main_is_carried(self):
        self.assertIn("SIGMOND_TRACK_MAIN=1", self._argv({"SIGMOND_TRACK_MAIN": "1"}))

    def test_not_added_when_unset(self):
        self.assertNotIn("SIGMOND_TRACK_MAIN=1", self._argv({"SIGMOND_TRACK_MAIN": ""}))


class VersionShowsPinnedOnPurpose(unittest.TestCase):
    def test_upstream_column_and_footer(self):
        from sigmond import provenance as pv
        st = {"pinned": True,
              "upstream": {"behind": 3, "checked": True}}
        self.assertEqual(pv._upstream_col(st, "wsjtx"), "pinned")
        self.assertEqual(pv._upstream_col(st, "ft8_lib"), "pinned")
        self.assertEqual(pv._upstream_col(st, "onion"), "3 newer*")
        comps = {"wsjtx": "ccdfaf3", "onion": "de8ea93"}
        commits = {n: {"date": "2026-01-01", "summary": "x"} for n in comps}
        out = pv._detail_table(comps, commits, {"wsjtx": st, "onion": st}, None)
        self.assertIn("newer upstream: onion*", out)
        self.assertNotIn("wsjtx*", out)
        self.assertIn("pinned on purpose: wsjtx", out)

    def test_versions_is_an_alias(self):
        src = (REPO / "bin" / "smd").read_text()
        self.assertIn("aliases=['versions']", src)
        self.assertIn("'versions': cmd_version", src)


class Ka9qRadioTargetsKa9qPythonMain(unittest.TestCase):
    """--latest moves ka9q-radio to the compat commit on ka9q-python's main,
    never to ka9q-radio's own main."""

    def test_reads_compat_from_ka9q_python_default_branch(self):
        with tempfile.TemporaryDirectory() as t:
            kp = Path(t) / "ka9q-python"
            kp.mkdir()
            g = ["git", "-C", str(kp), "-c", "user.email=t@t", "-c", "user.name=t"]
            subprocess.run(g[:3] + ["init", "-q"], check=True)
            (kp / "ka9q_radio_compat").write_text(B + "\n")
            subprocess.run(g + ["add", "."], check=True)
            subprocess.run(g + ["commit", "-qm", "x"], check=True)
            (kp / "ka9q_radio_compat").write_text(C + "\n")   # uncommitted: ignored
            with mock.patch.object(smd, "_git_default_ref", return_value="HEAD"):
                self.assertEqual(smd._ka9q_python_main_compat(kp), B)

    def test_unreadable_gives_empty(self):
        with mock.patch.object(smd, "_git_default_ref", return_value=""):
            self.assertEqual(smd._ka9q_python_main_compat(Path("/nonexistent")), "")


if __name__ == "__main__":
    unittest.main()
