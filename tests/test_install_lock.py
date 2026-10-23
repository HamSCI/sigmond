"""``sigmond.install_lock.held`` (plan Ruling 7, spec §6.1).

The manifest step holds ``/run/lock/hs-uploader-install.lock`` around the
probe, the render and the landing.  ``/run/lock`` is 1777, so any account
can plant something at that name before root opens it.  The lock:

- opens with O_NOFOLLOW (O_CREAT only to create it), so a planted symlink
  refuses instead of leading root to another file;
- refuses a non-regular file and a file owned by anyone but the expected
  owner (root in production; the tests inject their own uid);
- creates the file at 0600 whatever the umask;
- waits with LOCK_NB and a bounded retry, then raises a clear error.

Every test that could block runs the lock in a thread and joins it with a
timeout, so a lock taken with no bound fails here instead of hanging.
"""
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond import install_lock

ME = os.geteuid()
JOIN_S = 15.0       # far past every bound below; a hang fails, never blocks

# A second process holds the lock: prints "held" once it has it, then
# keeps it for argv[2] seconds (or until killed).
_HOLDER = r"""
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(float(sys.argv[2]))
"""


def _in_thread(fn):
    """Run fn() in a daemon thread; return (alive_after_join, result, exc)."""
    box = {}

    def body():
        try:
            box["result"] = fn()
        except BaseException as e:      # noqa: BLE001 - reported to the test
            box["exc"] = e
    t = threading.Thread(target=body, daemon=True)
    t.start()
    t.join(JOIN_S)
    return t.is_alive(), box.get("result"), box.get("exc")


class _Dir(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        self.path = self.dir / "hs-uploader-install.lock"

    def holder(self, seconds):
        p = subprocess.Popen([sys.executable, "-c", _HOLDER, str(self.path),
                              str(seconds)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        self.assertEqual(p.stdout.readline().strip(), "held")
        return p

    def take(self, wait):
        def fn():
            with install_lock.held(self.path, wait=wait, expected_uid=ME):
                return "got it"
        return _in_thread(fn)


class HeldTests(_Dir):

    def test_the_default_path_and_bound(self):
        import inspect
        sig = inspect.signature(install_lock.held)
        self.assertEqual(sig.parameters["path"].default,
                         Path("/run/lock/hs-uploader-install.lock"))
        self.assertEqual(sig.parameters["wait"].default, 30)

    def test_creates_the_file_0600_whatever_the_umask(self):
        old = os.umask(0)
        try:
            with install_lock.held(self.path, wait=1, expected_uid=ME):
                st = os.lstat(self.path)
        finally:
            os.umask(old)
        self.assertTrue(stat.S_ISREG(st.st_mode))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)

    def test_an_existing_wide_file_narrows_to_0600(self):
        self.path.write_text("")
        os.chmod(self.path, 0o666)
        with install_lock.held(self.path, wait=1, expected_uid=ME):
            pass
        self.assertEqual(stat.S_IMODE(os.lstat(self.path).st_mode), 0o600)

    def test_a_second_holder_waits_then_gets_it(self):
        self.holder(0.8)
        t0 = time.monotonic()
        alive, result, exc = self.take(wait=10)
        self.assertFalse(alive, "the lock never returned")
        self.assertIsNone(exc)
        self.assertEqual(result, "got it")
        self.assertGreater(time.monotonic() - t0, 0.4,
                           "it did not wait for the holder")

    def test_the_lock_is_released_on_exit(self):
        with install_lock.held(self.path, wait=1, expected_uid=ME):
            pass
        alive, result, exc = self.take(wait=1)
        self.assertFalse(alive)
        self.assertEqual(result, "got it", exc)

    def test_a_holder_that_never_releases_ends_in_a_clear_error(self):
        self.holder(600)
        t0 = time.monotonic()
        alive, result, exc = self.take(wait=0.6)
        self.assertFalse(alive, "blocked past the bound")
        self.assertIsInstance(exc, install_lock.InstallLockError)
        self.assertGreaterEqual(time.monotonic() - t0, 0.5)
        msg = str(exc)
        self.assertIn(str(self.path), msg)
        self.assertIn("0.6 s", msg)
        self.assertNotIn("\n", msg)


class RefusalTests(_Dir):

    def test_a_symlink_at_the_path_refuses_and_its_target_is_untouched(self):
        target = self.dir / "target"
        target.write_text("keep")
        os.chmod(target, 0o644)
        self.path.symlink_to(target)
        alive, result, exc = self.take(wait=0.5)
        self.assertFalse(alive)
        self.assertIsInstance(exc, install_lock.InstallLockError)
        self.assertIn("symlink", str(exc))
        self.assertIn(str(self.path), str(exc))
        self.assertEqual(target.read_text(), "keep")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        self.assertTrue(self.path.is_symlink())

    def test_a_dangling_symlink_refuses_and_creates_nothing(self):
        target = self.dir / "nowhere"
        self.path.symlink_to(target)
        alive, result, exc = self.take(wait=0.5)
        self.assertFalse(alive)
        self.assertIsInstance(exc, install_lock.InstallLockError)
        self.assertFalse(target.exists())

    def test_a_file_owned_by_another_uid_refuses(self):
        self.path.write_text("")
        os.chmod(self.path, 0o600)
        def fn():
            with install_lock.held(self.path, wait=0.5, expected_uid=ME + 1):
                return "got it"
        alive, result, exc = _in_thread(fn)
        self.assertFalse(alive)
        self.assertIsInstance(exc, install_lock.InstallLockError)
        self.assertIn(f"uid {ME}", str(exc))
        self.assertIn(str(self.path), str(exc))
        # Positive control: the same file, the right owner, takes.
        self.assertEqual(self.take(wait=0.5)[1], "got it")

    def test_a_directory_at_the_path_refuses(self):
        self.path.mkdir()
        alive, result, exc = self.take(wait=0.5)
        self.assertFalse(alive)
        self.assertIsInstance(exc, install_lock.InstallLockError)
        self.assertIn(str(self.path), str(exc))

    def test_a_fifo_at_the_path_refuses_without_hanging(self):
        os.mkfifo(self.path)
        alive, result, exc = self.take(wait=0.5)
        self.assertFalse(alive, "opening the FIFO hung")
        self.assertIsInstance(exc, install_lock.InstallLockError)
        self.assertIn("not a regular file", str(exc))

    def test_the_expected_owner_defaults_to_the_effective_uid(self):
        # Root in production; here, the test's own uid takes.
        with install_lock.held(self.path, wait=0.5):
            pass


if __name__ == "__main__":
    unittest.main()
