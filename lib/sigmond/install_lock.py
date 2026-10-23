"""hs-uploader's install lock, taken by sigmond's manifest step (plan Ruling 7).

Design §3.2 has hs-uploader's installer hold
``/run/lock/hs-uploader-install.lock``.  The installer does not take it yet
(that waits for v3.79), so the manifest step takes it itself, around the
features probe, the carry-over, the render and the landing (spec §6.1).

``/run/lock`` is mode 1777: any account can plant a file, a FIFO or a
symlink at the lock's name before root opens it.  So ``held``:

- opens the path with O_NOFOLLOW, and adds O_CREAT|O_EXCL only to create
  it.  A symlink at the path refuses; root never follows one to another
  file.
- checks the open descriptor by ``fstat``: a non-regular file refuses
  (an O_RDWR open of a FIFO returns at once on Linux), and so does a file
  owned by anyone but the expected owner, root in production (preflight
  ruling X2: tests pass their own uid).  A file another account planted
  makes the step fail, naming the file, until root removes it: a denial
  of service, never a leak (preflight D5).
- creates the file at 0600 and narrows an existing one to 0600 by
  ``fchmod`` on the descriptor, so no other account can open it to
  starve the lock (part 1 ruling C13).
- takes the flock with LOCK_NB and retries until ``wait`` seconds pass,
  then raises ``InstallLockError`` with one clear line.  A stuck holder
  makes the step fail loudly instead of hanging it.

Closing the descriptor drops the lock, on any exit from the ``with``.
"""
from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import stat
import time
from pathlib import Path
from typing import Iterator, Optional, Union

LOCK_PATH = Path("/run/lock/hs-uploader-install.lock")
WAIT_S = 30
_POLL_S = 0.1


class InstallLockError(Exception):
    """The install lock could not be taken; the message is one line."""


def _open(path: Path) -> int:
    """Open the lock file without following a link, creating it if absent."""
    base = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    for _ in range(3):
        try:
            return os.open(path, base)
        except FileNotFoundError:
            pass
        try:
            return os.open(path, base | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue        # someone created it between the two opens
    raise InstallLockError(f"{path}: appeared and vanished while opening "
                           "it; refusing")


def _open_checked(path: Path, expected_uid: int) -> int:
    try:
        fd = _open(path)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise InstallLockError(f"{path} is a symlink; refusing to "
                                   "follow it (remove it as root)") from None
        if e.errno == errno.EISDIR:
            raise InstallLockError(f"{path} is not a regular file; "
                                   "refusing (remove it as root)") from None
        raise InstallLockError(f"cannot open {path}: "
                               f"{e.strerror or e}") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise InstallLockError(f"{path} is not a regular file; "
                                   "refusing (remove it as root)")
        if st.st_uid != expected_uid:
            raise InstallLockError(
                f"{path} is owned by uid {st.st_uid}, not uid "
                f"{expected_uid}; refusing (remove it as root)")
        if stat.S_IMODE(st.st_mode) != 0o600:
            os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextlib.contextmanager
def held(path: Union[str, Path] = LOCK_PATH, wait: float = WAIT_S, *,
         expected_uid: Optional[int] = None) -> Iterator[None]:
    """Hold the install lock for the body of the ``with``.

    Waits up to `wait` seconds for another holder, then raises
    ``InstallLockError``.  `expected_uid` names the owner the file must
    have; None means the effective uid (root, when the step runs).
    """
    path = Path(path)
    if expected_uid is None:
        expected_uid = os.geteuid()
    fd = _open_checked(path, expected_uid)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise InstallLockError(
                        f"could not take {path} within {wait:g} s: another "
                        "manifest step or hs-uploader install holds it"
                    ) from None
                time.sleep(_POLL_S)
        yield
    finally:
        os.close(fd)        # closing drops the flock
