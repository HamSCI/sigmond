"""A client with no install.sh must still get built, not just symlinked.

⛔ Why this check exists.  The v3.37 golden-VM build reported
`✓ hamsci-physics installed` and then failed its capture gate on
`imports: client binary not found`.  Both statements were true.

`install_client()` clones the repo, looks for an install.sh, and — finding none
— falls back to what it calls a "deploy.toml-only install".  That fallback
linked fourteen systemd units, set `ok = True`, and returned success.  It never
executed `[build].steps`, so the venv those units launch from was never
created.  Nothing said so.

The client contract (docs/CLIENT-CONTRACT.md §5) has always described `[build]`
as a phase sigmond drives, with `produces` naming the artefacts it yields.  The
readiness gate already reads `produces` to decide whether a component is
"built".  Only the code that would run the steps was missing, so a deploy.toml
whose author followed the contract got a half-install that announced itself as
a whole one.

AC0G-B4 has GRAPE only because someone built that venv by hand.  AC0G-ND had
the checkout, no venv, and no GRAPE.

## Why only the no-install.sh branch runs the steps

Four other clients — hf-timestd, wspr-recorder, psk-recorder, meteor-scatter —
also declare `[build].steps`, and all four ship an install.sh that already does
that work.  Running the steps for them too would rebuild every venv a second
time on every install, for no gain and some risk.  So the steps run exactly
where nothing else builds the client: the fallback branch.  A client that grows
an install.sh later stops running them, which is correct — its script owns the
build from then on.

`produces` doubles as the idempotence check, which is what it is for: when
every declared artefact already exists, the build is done and the steps are
skipped.
"""
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import installer  # noqa: E402


def _write(tmp: Path, body: str) -> Path:
    repo = tmp / "a-client"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "deploy.toml").write_text(textwrap.dedent(body))
    return repo


class BuildStepsRun(unittest.TestCase):

    def setUp(self):
        self._tmp = Path(__import__("tempfile").mkdtemp())

    def tearDown(self):
        __import__("shutil").rmtree(self._tmp, ignore_errors=True)

    def test_declared_steps_execute_in_order(self):
        repo = _write(self._tmp, """
            [build]
            steps = ["step-one", "step-two"]
        """)
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        ok, msgs = installer.run_deploy_build_steps(repo, runner=runner)
        self.assertTrue(ok, msgs)
        self.assertEqual([c for c in calls], ["step-one", "step-two"])

    def test_steps_run_from_the_repo_root(self):
        """The contract says src paths are relative to the repo root."""
        repo = _write(self._tmp, """
            [build]
            steps = ["pip install -e ."]
        """)
        seen = {}

        def runner(cmd, **kw):
            seen.update(kw)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        installer.run_deploy_build_steps(repo, runner=runner)
        self.assertEqual(Path(seen["cwd"]), repo)

    def test_a_failing_step_fails_the_install_and_names_itself(self):
        """⛔ The whole point: a build that did not happen must not read as one."""
        repo = _write(self._tmp, """
            [build]
            steps = ["works", "explodes", "never-reached"]
        """)
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd)
            rc = 1 if cmd == "explodes" else 0
            return subprocess.CompletedProcess(cmd, rc, "", "boom")

        ok, msgs = installer.run_deploy_build_steps(repo, runner=runner)
        self.assertFalse(ok)
        self.assertEqual(calls, ["works", "explodes"],
                         "a failed step must stop the sequence")
        self.assertTrue(any("explodes" in m for m in msgs),
                        f"the failing step must be named: {msgs}")

    def test_no_build_section_is_success_not_failure(self):
        repo = _write(self._tmp, """
            [package]
            name = "a-client"
        """)
        ok, msgs = installer.run_deploy_build_steps(
            repo, runner=lambda *a, **k: self.fail("nothing should run"))
        self.assertTrue(ok)

    def test_an_already_built_client_skips_its_steps(self):
        """`produces` is the idempotence check — that is what it is for."""
        artefact = self._tmp / "already-there"
        artefact.write_text("x")
        repo = _write(self._tmp, f"""
            [build]
            steps = ["expensive"]
            produces = ["{artefact}"]
        """)
        ok, msgs = installer.run_deploy_build_steps(
            repo, runner=lambda *a, **k: self.fail("must not rebuild"))
        self.assertTrue(ok)
        self.assertTrue(any("already built" in m for m in msgs), msgs)

    def test_a_missing_artefact_rebuilds(self):
        artefact = self._tmp / "not-yet"
        repo = _write(self._tmp, f"""
            [build]
            steps = ["build-it"]
            produces = ["{artefact}"]
        """)
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd)
            artefact.write_text("built")      # the step does its job
            return subprocess.CompletedProcess(cmd, 0, "", "")

        ok, msgs = installer.run_deploy_build_steps(repo, runner=runner)
        self.assertTrue(ok, msgs)
        self.assertEqual(calls, ["build-it"],
                         "an absent artefact must trigger the build")

    def test_steps_that_ran_but_produced_nothing_fail(self):
        """Exit 0 is not evidence.  The artefact is."""
        repo = _write(self._tmp, """
            [build]
            steps = ["pretends-to-work"]
            produces = ["/nonexistent/artefact"]
        """)
        ok, msgs = installer.run_deploy_build_steps(
            repo,
            runner=lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", ""))
        self.assertFalse(ok, "every step exited 0 and the artefact is absent — "
                             "that is a failed build, not a successful one")
        self.assertTrue(any("artefact" in m for m in msgs), msgs)

    def test_dry_run_executes_nothing(self):
        repo = _write(self._tmp, """
            [build]
            steps = ["would-run"]
        """)
        ok, msgs = installer.run_deploy_build_steps(
            repo, dry_run=True,
            runner=lambda *a, **k: self.fail("dry run must not execute"))
        self.assertTrue(ok)
        self.assertTrue(any("would-run" in m for m in msgs), msgs)


class InstallClientDrivesTheBuild(unittest.TestCase):
    """The fallback branch is where this matters."""

    def setUp(self):
        self._tmp = Path(__import__("tempfile").mkdtemp())

    def tearDown(self):
        __import__("shutil").rmtree(self._tmp, ignore_errors=True)

    def _entry(self):
        from sigmond.catalog import CatalogEntry
        return CatalogEntry(name="a-client", kind="client", description="x",
                            repo="https://example.invalid/a", install_script="")

    def test_a_failed_build_makes_install_client_return_false(self):
        repo = _write(self._tmp, """
            [build]
            steps = ["explodes"]
        """)
        with mock.patch.object(installer, "clone_repo", return_value=repo), \
             mock.patch.object(installer, "_clone_source_only_deps"), \
             mock.patch.object(installer, "find_install_script", return_value=None), \
             mock.patch.object(installer, "apply_deploy_toml_links", return_value=[]), \
             mock.patch.object(installer, "run_deploy_build_steps",
                               return_value=(False, ["step failed: explodes"])):
            ok = installer.install_client(self._entry())
        self.assertFalse(ok, "install_client reported success while the client "
                             "was never built — the v3.37 regression")

    def test_a_successful_build_still_installs(self):
        repo = _write(self._tmp, """
            [build]
            steps = ["fine"]
        """)
        with mock.patch.object(installer, "clone_repo", return_value=repo), \
             mock.patch.object(installer, "_clone_source_only_deps"), \
             mock.patch.object(installer, "find_install_script", return_value=None), \
             mock.patch.object(installer, "apply_deploy_toml_links", return_value=[]), \
             mock.patch.object(installer, "run_deploy_build_steps",
                               return_value=(True, [])):
            ok = installer.install_client(self._entry())
        self.assertTrue(ok)


class FindClientBinaryKnowsTheRealLayout(unittest.TestCase):
    """⛔ Even AC0G-B4, with GRAPE running, failed `client binary not found`.

    `find_client_binary` searched PATH and `/opt/<name>/venv/bin/<name>`.
    Sigmond installs every client under `/opt/git/sigmond/<name>/`, so that
    fallback matched nothing on any station this fleet has ever built.
    """

    def setUp(self):
        self._tmp = Path(__import__("tempfile").mkdtemp())

    def tearDown(self):
        __import__("shutil").rmtree(self._tmp, ignore_errors=True)

    def test_the_suite_venv_is_searched(self):
        from sigmond import catalog
        binary = self._tmp / "a-client" / "venv" / "bin" / "a-client"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n")
        with mock.patch.object(catalog.shutil, "which", return_value=None), \
             mock.patch.object(catalog, "SUITE_ROOT", self._tmp):
            self.assertEqual(catalog.find_client_binary("a-client"), str(binary))

    def test_path_still_wins(self):
        from sigmond import catalog
        with mock.patch.object(catalog.shutil, "which",
                               return_value="/usr/local/bin/a-client"):
            self.assertEqual(catalog.find_client_binary("a-client"),
                             "/usr/local/bin/a-client")

    def test_absent_everywhere_is_still_none(self):
        from sigmond import catalog
        with mock.patch.object(catalog.shutil, "which", return_value=None), \
             mock.patch.object(catalog, "SUITE_ROOT", self._tmp):
            self.assertIsNone(catalog.find_client_binary("nothing-here"))


if __name__ == "__main__":
    unittest.main()
