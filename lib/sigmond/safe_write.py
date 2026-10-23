"""Root writes into directories that service accounts own (spec section 8.2,
plan Ruling 17, Global Constraint 3).

sigmond runs as root and writes into directories another account can
write: each client's ``/etc/<client>/env/``, ``/etc/mag-recorder/`` and
``/var/lib/hs-uploader``.  (The install lock lives in a root-only
directory instead; see ``install_lock``.)  An account that owns such a
directory can plant a symlink at the name root is about to write, or at
the temporary name it writes first.  Every such write goes through
``write_owned``:

- It opens the parent directory once, with O_DIRECTORY|O_NOFOLLOW, and
  does every later step relative to that descriptor.  A parent that is
  itself a symlink is refused (nothing in sigmond needs to write through
  one), and a rename of the directory mid-write cannot redirect the file.
- It reads the directory's owner by ``fstat`` on that descriptor.
- It refuses a directory, FIFO, socket or device at the target.  A
  symlink at the target is replaced, never followed: ``rename(2)`` swaps
  the link itself (controller ruling C7, the same rule as hs-uploader's
  ``sinkswitch._atomic_write``).  Refusing would let the account that
  owns the directory block the write forever.
- It creates the temporary file under a random name with
  O_CREAT|O_EXCL|O_NOFOLLOW at mode 0600, retrying on a name that exists.
- As root, it gives the file to the directory's owner by ``fchown`` on
  the open descriptor, sets the mode by ``fchmod``, writes, ``fsync``s,
  renames over the target and ``fsync``s the directory.

It never calls ``Path.write_text``, never chowns or chmods by path, and
never uses a predictable name.  ``rewrite_owned`` reads the old contents
and writes the new through the same directory descriptor.  Its read
refuses a symlink, a hard link, and (as root) a file owned by anyone but
root or the directory's owner, so root never copies another file's
contents (``/etc/shadow``, say) into a file the service account can read.

hs-uploader carries its own copy of this rule; sigmond must stand alone,
so it does not import that one.
"""
from __future__ import annotations

import errno
import os
import re
import secrets
import stat
import tomllib
from pathlib import Path
from typing import Callable, Optional, Tuple, Union

_TMP_TRIES = 100
READ_CAP = 1 << 20      # 1 MiB: an env file or a client config, never more


def _open_parent(d: Path) -> int:
    """Open directory `d` without following a link at its last component."""
    try:
        return os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                       | os.O_CLOEXEC)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR) and os.path.islink(d):
            raise OSError(e.errno, f"{d}: the directory is a symlink; "
                          "refusing to work through it") from None
        raise


def _name_of(path: Path) -> str:
    name = path.name
    if not name or name in (".", "..") or "/" in name:
        raise ValueError(f"{path}: no file name")
    return name


def _read_at(dfd: int, dst: os.stat_result, name: str, path: Path):
    """(text, fstat) of `name` inside the open directory `dfd`, or None.

    Opened relative to the directory descriptor with O_NOFOLLOW|O_NONBLOCK,
    then checked on its own descriptor:
    - a symlink is refused.  A lever file belongs to the service, which can
      re-arm its own lever anyway; replacing the link would either read its
      target (disclosure) or drop the service's settings unseen.
    - a non-regular file is refused (O_NONBLOCK keeps a FIFO from hanging).
    - a hard link (st_nlink > 1) is refused: the directory's owner could
      link a secret it cannot read, and root would copy it into a file the
      owner can read.
    - as root, a file owned by anyone but root or the directory's owner is
      refused, for the same reason.
    - more than READ_CAP bytes are refused, counted as read.
    """
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                     | os.O_CLOEXEC, dir_fd=dfd)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise OSError(e.errno, f"{path}: a symlink; refusing to read "
                          "through it") from None
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EINVAL, f"{path}: not a regular file; "
                          "refusing to read it")
        if st.st_nlink > 1:
            raise OSError(errno.EMLINK, f"{path}: a hard link "
                          f"({st.st_nlink} names); refusing to read it")
        if os.geteuid() == 0 and st.st_uid not in (0, dst.st_uid):
            raise OSError(errno.EPERM, f"{path}: owned by uid {st.st_uid}, "
                          f"neither root nor the directory's owner "
                          f"(uid {dst.st_uid}); refusing to read it")
        chunks, got = [], 0
        while got <= READ_CAP:
            b = os.read(fd, READ_CAP + 1 - got)
            if not b:
                break
            chunks.append(b)
            got += len(b)
        if got > READ_CAP:
            raise OSError(errno.EFBIG, f"{path}: larger than {READ_CAP} "
                          "bytes; refusing to read it")
    finally:
        os.close(fd)
    return b"".join(chunks).decode("utf-8"), st


def _write_at(dfd: int, dst: os.stat_result, name: str, path: Path,
              data: bytes, mode: int, owner) -> None:
    """The write half of write_owned, inside the open directory `dfd`.

    `owner` is (uid, gid) to fchown to as root, or None for no chown.
    """
    try:
        st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
            raise OSError(errno.EISDIR if stat.S_ISDIR(st.st_mode)
                          else errno.EINVAL,
                          f"{path}: not a regular file; refusing to "
                          "replace it")
    if owner is not None and os.geteuid() == 0 \
            and owner[0] not in (0, dst.st_uid):
        raise OSError(errno.EPERM, f"{path}: owner uid {owner[0]} is "
                      "neither root nor the directory's owner; refusing")
    flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
             | os.O_CLOEXEC)
    for _ in range(_TMP_TRIES):
        tmp = f".{name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        try:
            fd = os.open(tmp, flags, 0o600, dir_fd=dfd)
            break
        except FileExistsError:
            continue
    else:
        raise OSError(errno.EEXIST,
                      f"{path}: could not create a temporary file")
    try:
        try:
            # The data lands while the file is still root's and 0600; only
            # then does it change hands.  fchown before fchmod, since a
            # chown may clear set-id bits.
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
            if owner is not None and os.geteuid() == 0:
                os.fchown(fd, owner[0], owner[1])
            os.fchmod(fd, mode)
        finally:
            os.close(fd)
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass        # never let the cleanup hide the real error
        raise
    os.fsync(dfd)


def write_owned(path: Union[str, Path], data: Union[str, bytes], *,
                mode: int = 0o644, owner_from_dir: bool = True,
                owner: Optional[Tuple[int, int]] = None) -> None:
    """Write `data` to `path` atomically, safe in a directory others can write.

    `mode` is set exactly, whatever the umask.  With `owner_from_dir`, a
    root caller gives the file to the directory's owner and group (the
    service account, so it can read its own file); a caller that is not
    root cannot chown and leaves its own ownership.  `owner`, a (uid, gid)
    pair, overrides that; as root its uid must be root or the directory's
    owner, or the write is refused.  Raises OSError for a non-regular file
    at `path` or a symlink at its directory, and FileNotFoundError when the
    directory is missing.
    """
    path = Path(path)
    name = _name_of(path)
    if isinstance(data, str):
        data = data.encode("utf-8")
    dfd = _open_parent(path.parent)
    try:
        dst = os.fstat(dfd)
        if owner is None and owner_from_dir:
            owner = (dst.st_uid, dst.st_gid)
        _write_at(dfd, dst, name, path, data, mode, owner)
    finally:
        os.close(dfd)


def rewrite_owned(path: Union[str, Path],
                  transform: Callable[[Optional[str]], Union[str, bytes]], *,
                  mode: Optional[int] = 0o644, keep_owner: bool = False) -> None:
    """Read `path`, pass its text to `transform`, write what it returns.

    The read and the write share one directory descriptor, opened once with
    O_DIRECTORY|O_NOFOLLOW, so a directory swapped under the path between
    them cannot redirect either.  The read follows ``_read_at``'s rules
    (no link, no hard link, no stranger's file, at most READ_CAP bytes);
    `transform` gets None when nothing stands at `path`.

    `mode` None keeps the existing file's mode (0644 for a new file).
    `keep_owner` keeps the existing file's uid and gid, which ``_read_at``
    has already checked are root or the directory's owner; otherwise, and
    for a new file, the file goes to the directory's owner as write_owned
    does.
    """
    path = Path(path)
    name = _name_of(path)
    dfd = _open_parent(path.parent)
    try:
        dst = os.fstat(dfd)
        got = _read_at(dfd, dst, name, path)
        old, st = (None, None) if got is None else got
        data = transform(old)
        if isinstance(data, str):
            data = data.encode("utf-8")
        if mode is None:
            mode = stat.S_IMODE(st.st_mode) & 0o777 if st else 0o644
        owner = (st.st_uid, st.st_gid) if (keep_owner and st) \
            else (dst.st_uid, dst.st_gid)
        _write_at(dfd, dst, name, path, data, mode, owner)
    finally:
        os.close(dfd)


def read_nofollow(path: Union[str, Path]) -> Optional[str]:
    """The text of the regular file at `path`, or None when nothing stands there.

    Opens the directory with O_DIRECTORY|O_NOFOLLOW and reads relative to
    it, under ``_read_at``'s rules: a symlink, a non-regular file, a hard
    link, (as root) a stranger's file, or more than READ_CAP bytes raise
    OSError.  A read that feeds a write uses ``rewrite_owned`` instead, so
    both halves share one directory descriptor.
    """
    path = Path(path)
    name = _name_of(path)
    dfd = _open_parent(path.parent)
    try:
        got = _read_at(dfd, os.fstat(dfd), name, path)
    finally:
        os.close(dfd)
    return None if got is None else got[0]


# ---- the boolean TOML setter (mag-recorder's lever) -----------------------

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")
_KEY_VALUE_SIMPLE = re.compile(
    r"""^(true|false|"[^"\\]*"|'[^']*')(\s*(?:\#.*)?)$""")


def _header(line: str) -> Optional[str]:
    """The table name a `[name]` or `[[name]]` line opens, else None."""
    m = re.match(r"^\s*\[\[?\s*([^\]]*?)\s*\]\]?\s*(?:#.*)?$", line)
    return m.group(1) if m else None


def set_toml_bool(text: str, section: str, key: str, value: bool) -> str:
    """Return `text` with ``[section] key`` set to the bare boolean `value`.

    Writes ``false``, never ``"false"`` (``psws._set_toml_field`` writes a
    string, which mag-recorder reads as true).  Replaces the key's value in
    place, keeping its indent and any trailing comment; adds the key right
    under the section header when the section lacks it; appends the
    section when the text has none.  It then parses the result and
    requires it to equal the original with only that key changed, so a
    shape it cannot edit by line (a dotted key, an inline table, a
    multi-line string) raises ValueError instead of yielding a wrong file.
    """
    if not (_BARE.match(section) and _BARE.match(key)):
        raise ValueError(f"[{section}] {key}: only bare names are supported")
    if not isinstance(value, bool):
        raise TypeError("value must be a bool")
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"cannot parse the TOML: {e}") from None
    word = "true" if value else "false"
    lines = text.splitlines(keepends=True)
    key_re = re.compile(
        r"^(\s*)(" + re.escape(key) + r'|"' + re.escape(key) + r'"'
        r"|'" + re.escape(key) + r"')(\s*=\s*)(.*?)(\r?\n)?$")
    current = None          # table the line belongs to; "" = top level
    header_at = None
    done = False
    for i, ln in enumerate(lines):
        h = _header(ln)
        if h is not None:
            current = h
            if h == section and header_at is None and not ln.lstrip().startswith("[["):
                header_at = i
            continue
        if current != section or done:
            continue
        m = key_re.match(ln)
        if not m:
            continue
        indent, k, eq, rest, nl = m.groups()
        sv = _KEY_VALUE_SIMPLE.match(rest)
        tail = sv.group(2) if sv else ""
        lines[i] = f"{indent}{k}{eq}{word}{tail}{nl or ''}"
        done = True
    if not done:
        if header_at is not None:
            nl = "\r\n" if lines[header_at].endswith("\r\n") else "\n"
            if not lines[header_at].endswith("\n"):
                lines[header_at] += nl
            lines.insert(header_at + 1, f"{key} = {word}{nl}")
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            sep = "\n" if lines and lines[-1].strip() else ""
            lines.append(f"{sep}[{section}]\n{key} = {word}\n")
    out = "".join(lines)
    try:
        after = tomllib.loads(out)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"[{section}] {key}: cannot set it in this file's "
                         f"shape ({e}); edit it by hand") from None
    expect = dict(before)
    expect[section] = dict(before.get(section, {}))
    expect[section][key] = value
    if after != expect or after[section][key] is not value:
        raise ValueError(f"[{section}] {key}: cannot set it in this file's "
                         "shape; edit it by hand")
    return out


def write_toml_bool(path: Union[str, Path], section: str, key: str,
                    value: bool) -> None:
    """Set ``[section] key = value`` in the TOML file at `path`, as a boolean.

    Reads and writes through ``rewrite_owned``, keeping the file's mode and
    its owner (root or the directory's owner, as the read has checked; a
    root:magrec 0640 config stays root:magrec 0640), then re-reads the file
    with ``tomllib`` and raises ValueError unless it holds the boolean.
    The file must exist (FileNotFoundError otherwise): this sets a key in
    a client's config, it never invents one.
    """
    path = Path(path)

    def edit(text):
        if text is None:
            raise FileNotFoundError(errno.ENOENT, f"{path}: no such file")
        return set_toml_bool(text, section, key, value)
    rewrite_owned(path, edit, mode=None, keep_owner=True)
    got = tomllib.loads(read_nofollow(path) or "")
    v = got.get(section, {}).get(key) if isinstance(got.get(section), dict) else None
    if not isinstance(v, bool) or v is not value:
        raise ValueError(f"{path}: [{section}] {key} reads back {v!r}, "
                         f"not the boolean {value!r}")
