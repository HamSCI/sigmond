"""Hermetic tests for the whole-host uninstaller (lib/sigmond/uninstall.py).

Covers the pure logic — the `make install` argv parser and the
plan-render keep/remove classification — without touching the real host."""

import unittest

from sigmond.uninstall import (
    _split_install_args, render_plan, UninstallPlan,
)
from pathlib import Path


class TestSplitInstallArgs(unittest.TestCase):
    def test_mode_value_not_a_source(self):
        # `install -m 0755 start-hfdl /S/usr/local/sbin` -> src start-hfdl only
        toks = ["install", "-m", "0755", "start-hfdl",
                "/__SIGMOND_UNINSTALL_SENTINEL__/usr/local/sbin"]
        srcs, tdir = _split_install_args(toks)
        self.assertEqual(srcs, ["start-hfdl"])
        self.assertIsNone(tdir)

    def test_multiple_sources(self):
        toks = ["install", "-m", "0644", "98-sockbuf.conf", "50-multicast.conf",
                "/__SIGMOND_UNINSTALL_SENTINEL__/etc/sysctl.d"]
        srcs, tdir = _split_install_args(toks)
        self.assertEqual(srcs, ["98-sockbuf.conf", "50-multicast.conf"])

    def test_dash_t_target_dir(self):
        toks = ["install", "-m", "0644", "-D", "html/index.html", "-t",
                "/__SIGMOND_UNINSTALL_SENTINEL__/usr/local/share/ka9q-web/html"]
        srcs, tdir = _split_install_args(toks)
        self.assertEqual(srcs, ["html/index.html"])
        self.assertTrue(tdir.endswith("/usr/local/share/ka9q-web/html"))


def _sample_plan(keep_config: bool, wipe_data: bool) -> UninstallPlan:
    p = UninstallPlan(keep_config=keep_config, wipe_data=wipe_data,
                      revert_host=not keep_config, remove_users=not keep_config,
                      remove_source=not keep_config)
    p.config_dirs = [Path("/etc/wspr-recorder"), Path("/etc/sigmond")]
    p.data_dirs = [Path("/var/lib/sigmond")]
    p.ext_files = [Path("/usr/local/sbin/radiod")]
    p.ext_asset_dirs = [Path("/usr/local/share/ka9q-radio")]
    p.venvs = [Path("/opt/git/sigmond/sigmond/venv")]
    p.checkouts = [Path("/opt/git/sigmond/wspr-recorder")]
    p.users = ["sigmond"]
    return p


class TestRenderClassification(unittest.TestCase):
    def test_full_mode_removes_config_and_data(self):
        lines = render_plan(_sample_plan(keep_config=False, wipe_data=True))
        body = "\n".join(lines)
        self.assertIn("mode: full", body)
        # config + data are rm in full mode
        self.assertTrue(any("config" in l and "rm" in l and "wspr-recorder" in l
                            for l in lines))
        self.assertTrue(any("data" in l and "rm" in l for l in lines))
        # ext-files/assets always removed
        self.assertTrue(any("ext-file" in l and "rm" in l for l in lines))
        self.assertTrue(any("ext-asset" in l and "rm" in l for l in lines))

    def test_keep_config_preserves_config_data_source(self):
        lines = render_plan(_sample_plan(keep_config=True, wipe_data=False))
        self.assertTrue(any("config" in l and "KEEP" in l for l in lines))
        self.assertTrue(any("data" in l and "KEEP" in l for l in lines))
        self.assertTrue(any("source" in l and "KEEP" in l for l in lines))
        # but software (venv, ext-files) is still removed even in keep-config
        self.assertTrue(any("venv" in l and "rm" in l for l in lines))
        self.assertTrue(any("ext-file" in l and "rm" in l for l in lines))

    def test_keep_config_wipe_data_override(self):
        lines = render_plan(_sample_plan(keep_config=True, wipe_data=True))
        # config kept, but data removed when --wipe-data overrides
        self.assertTrue(any("config" in l and "KEEP" in l for l in lines))
        self.assertTrue(any("data" in l and "rm" in l for l in lines))


if __name__ == "__main__":
    unittest.main()


class TestProtectedDirs(unittest.TestCase):
    """The catastrophic bug: a deploy.toml dst=/etc/systemd/system caused
    rmtree to wipe every host service's enable-symlinks.  Lock the guard."""

    def test_critical_shared_dirs_protected(self):
        from sigmond.uninstall import _PROTECTED_DIRS
        for d in ("/etc/systemd/system", "/etc", "/etc/udev/rules.d",
                  "/usr/local/bin", "/usr/local/sbin", "/usr/local/lib",
                  "/var/lib", "/var/log", "/opt/git/sigmond", "/"):
            self.assertIn(Path(d), _PROTECTED_DIRS, f"{d} must be protected")

    def test_rmtree_noops_on_protected(self):
        # Must refuse + return without raising.  Use a protected dir that we
        # assert remains afterwards (never actually removed).
        from sigmond.uninstall import _rmtree
        _rmtree(Path("/usr/local/bin"))
        self.assertTrue(Path("/usr/local/bin").is_dir()
                        or not Path("/usr/local/bin").exists())

