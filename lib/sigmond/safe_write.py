"""Root writes into directories that service accounts own (spec section 8.2,
plan Ruling 17, Global Constraint 3).

sigmond runs as root and writes into directories another account can
write: each client's ``/etc/<client>/env/``, ``/etc/mag-recorder/``,
``/var/lib/hs-uploader`` and ``/run/lock``.  An account that owns such a
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
never uses a predictable name.  ``read_nofollow`` gives a read-modify-write
caller the old contents without following a link, so root never copies
another file's contents (``/etc/shadow``, say) into a file the service
account can read.

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
from typing import Optional, Union

_TMP_TRIES = 100


def _open_parent(d: Path) -> int:
    """Open directory `d` without following a link at its last component."""
    try:
        return os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                       | os.O_CLOEXEC)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR) and os.path.islink(d):
            raise OSError(e.errno, f"{d}: the directory is a symlink; "
                          "refusing to write through it") from None
        raise


def write_owned(path: Union[str, Path], data: Union[str, bytes], *,
                mode: int = 0o644, owner_from_dir: bool = True) -> None:
    """Write `data` to `path` atomically, safe in a directory others can write.

    `mode` is set exactly, whatever the umask.  With `owner_from_dir`, a
    root caller gives the file to the directory's owner and group (the
    service account, so it can read its own file); a caller that is not
    root cannot chown and leaves its own ownership.  Raises OSError for a
    non-regular file at `path` or a symlink at its directory, and
    FileNotFoundError when the directory is missing.
    """
    path = Path(path)
    name = path.name
    if not name or name in (".", "..") or "/" in name:
        raise ValueError(f"{path}: no file name to write")
    if isinstance(data, str):
        data = data.encode("utf-8")
    dfd = _open_parent(path.parent)
    try:
        dst = os.fstat(dfd)
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
                if owner_from_dir and os.geteuid() == 0:
                    os.fchown(fd, dst.st_uid, dst.st_gid)
                os.fchmod(fd, mode)  # after fchown, which may clear set-id bits
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except FileNotFoundError:
                pass
            raise
        os.fsync(dfd)
    finally:
        os.close(dfd)


def read_nofollow(path: Union[str, Path]) -> Optional[str]:
    """The text of the regular file at `path`, or None when nothing stands there.

    Never follows a symlink at `path`: a link raises OSError, as does any
    other non-regular file.  O_NONBLOCK keeps a planted FIFO from hanging
    the open.
    """
    got = _read_nofollow(Path(path))
    return None if got is None else got[0]


def _read_nofollow(path: Path):
    """(text, fstat of the descriptor read), or None when absent."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                     | os.O_CLOEXEC)
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
        chunks = []
        while True:
            b = os.read(fd, 1 << 16)
            if not b:
                break
            chunks.append(b)
    finally:
        os.close(fd)
    return b"".join(chunks).decode("utf-8"), st


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

    Reads without following a link, writes through ``write_owned`` (the
    file's mode kept, owner from the directory), then re-reads the file
    with ``tomllib`` and raises ValueError unless it holds the boolean.
    The file must exist (FileNotFoundError otherwise): this sets a key in
    a client's config, it never invents one.
    """
    path = Path(path)
    got = _read_nofollow(path)
    if got is None:
        raise FileNotFoundError(errno.ENOENT, f"{path}: no such file")
    text, st = got
    mode = stat.S_IMODE(st.st_mode) & 0o777
    write_owned(path, set_toml_bool(text, section, key, value), mode=mode)
    got = tomllib.loads(read_nofollow(path) or "")
    v = got.get(section, {}).get(key) if isinstance(got.get(section), dict) else None
    if not isinstance(v, bool) or v is not value:
        raise ValueError(f"{path}: [{section}] {key} reads back {v!r}, "
                         f"not the boolean {value!r}")
