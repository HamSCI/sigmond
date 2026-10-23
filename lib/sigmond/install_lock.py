"""hs-uploader's install lock, taken by sigmond's manifest step (plan Ruling 7).

Design §3.2 has hs-uploader's installer hold an install lock.  The
installer does not take it yet (that waits for v3.79), so the manifest step
takes it itself, around the features probe, the carry-over, the render and
the landing (spec §6.1).

The lock lives at ``/run/hs-uploader-install/lock``, in a directory only
root can write (ruling C14-3).  ``/run/lock`` is 1777, so any account could
plant a file, a link or a FIFO at a lock name there before root opens it;
inside a root-only directory nobody else can.  ``held``:

- creates the directory with mkdir 0700, or accepts an existing one only
  when ``lstat`` shows a real directory (not a symlink) owned by the
  expected owner with no group or other write bit; anything else refuses
  with one line.
- opens the directory with O_DIRECTORY|O_NOFOLLOW and re-checks it by
  ``fstat`` on the descriptor, so a swap between the check and the open
  cannot redirect it.
- opens the lock file relative to that descriptor, with O_NOFOLLOW, and
  adds O_CREAT|O_EXCL only to create it (mode 0600).  A symlink refuses.
- checks the open file by ``fstat``: a non-regular file refuses, a file
  with more than one name refuses (a hard link to another file, whose
  mode root must not change), and a file owned by anyone but the
  expected owner refuses.  The expected owner is root in production;
  tests inject their own uid (preflight ruling X2).
- only then narrows the mode to 0600 by ``fchmod`` on the descriptor.
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

LOCK_PATH = Path("/run/hs-uploader-install/lock")
WAIT_S = 30
_POLL_S = 0.1


class InstallLockError(Exception):
    """The install lock could not be taken; the message is one line."""


def _check_dir(d: Path, st: os.stat_result, expected_uid: int) -> None:
    if stat.S_ISLNK(st.st_mode):
        raise InstallLockError(f"{d} is a symlink; refusing to follow it "
                               "(remove it as root)")
    if not stat.S_ISDIR(st.st_mode):
        raise InstallLockError(f"{d} is not a directory; refusing "
                               "(remove it as root)")
    if st.st_uid != expected_uid:
        raise InstallLockError(f"{d} is owned by uid {st.st_uid}, not uid "
                               f"{expected_uid}; refusing (remove it as root)")
    if st.st_mode & 0o022:
        raise InstallLockError(
            f"{d} has mode {stat.S_IMODE(st.st_mode):04o}, writable by "
            "group or others; refusing (remove it as root)")


def _open_dir(d: Path, expected_uid: int) -> int:
    """The lock's directory, created 0700 or checked, opened without
    following a link."""
    try:
        os.mkdir(d, 0o700)
    except FileExistsError:
        pass
    except OSError as e:
        raise InstallLockError(f"cannot create {d}: {e.strerror or e}") from None
    try:
        _check_dir(d, os.lstat(d), expected_uid)
        dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                      | os.O_CLOEXEC)
    except OSError as e:
        raise InstallLockError(f"cannot open {d}: {e.strerror or e}") from None
    try:
        _check_dir(d, os.fstat(dfd), expected_uid)
    except BaseException:
        os.close(dfd)
        raise
    return dfd


def _open_at(dfd: int, name: str, path: Path) -> int:
    """Open `name` in `dfd` without following a link, creating it if absent."""
    base = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    for _ in range(3):
        try:
            return os.open(name, base, dir_fd=dfd)
        except FileNotFoundError:
            pass
        try:
            return os.open(name, base | os.O_CREAT | os.O_EXCL, 0o600,
                           dir_fd=dfd)
        except FileExistsError:
            continue        # someone created it between the two opens
    raise InstallLockError(f"{path}: appeared and vanished while opening "
                           "it; refusing")


def _open_checked(path: Path, expected_uid: int) -> int:
    dfd = _open_dir(path.parent, expected_uid)
    try:
        try:
            fd = _open_at(dfd, path.name, path)
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise InstallLockError(f"{path} is a symlink; refusing to "
                                       "follow it (remove it as root)") from None
            if e.errno == errno.EISDIR:
                raise InstallLockError(f"{path} is not a regular file; "
                                       "refusing (remove it as root)") from None
            raise InstallLockError(f"cannot open {path}: "
                                   f"{e.strerror or e}") from None
    finally:
        os.close(dfd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise InstallLockError(f"{path} is not a regular file; "
                                   "refusing (remove it as root)")
        if st.st_nlink != 1:
            raise InstallLockError(f"{path} has {st.st_nlink} names (a hard "
                                   "link); refusing (remove it as root)")
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
    ``InstallLockError``.  `expected_uid` names the owner the directory and
    the file must have; None means the effective uid (root, when the step
    runs).
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
