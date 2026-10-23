"""Tests for sigmond.safe_write: root writes into directories that service
accounts own (Global Constraint 3, spec section 8.2, plan Ruling 17).

A link planted at the target is replaced, never followed (controller
ruling C7, the same rule as hs-uploader's sinkswitch._atomic_write).  A
link at the parent directory is refused.  Ownership goes to the
directory's owner through the open descriptor, never by path.
"""
import os
import stat
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from sigmond import safe_write  # noqa: E402
from sigmond.safe_write import (  # noqa: E402
    read_nofollow, set_toml_bool, write_owned, write_toml_bool,
)

MAG_TEMPLATE = Path(__file__).resolve().parent / "fixtures" / \
    "mag-recorder-config.toml.template"


@pytest.fixture
def victim(tmp_path):
    v = tmp_path / "outside" / "victim"
    v.parent.mkdir()
    v.write_text("VICTIM\n")
    return v


def _leftovers(d: Path, keep):
    return sorted(p.name for p in d.iterdir() if p.name not in keep)


# ---- write_owned: contents, mode, atomic replace --------------------------

def test_writes_contents_and_mode(tmp_path):
    p = tmp_path / "a.env"
    write_owned(p, "X=1\n", mode=0o640)
    assert p.read_text() == "X=1\n"
    st = os.lstat(p)
    assert stat.S_ISREG(st.st_mode)
    assert stat.S_IMODE(st.st_mode) == 0o640
    assert _leftovers(tmp_path, {"a.env"}) == []


def test_mode_ignores_the_umask(tmp_path):
    old = os.umask(0o077)
    try:
        write_owned(tmp_path / "a", "x", mode=0o644)
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.lstat(tmp_path / "a").st_mode) == 0o644


def test_replaces_an_existing_file(tmp_path):
    p = tmp_path / "a"
    p.write_text("old\n")
    write_owned(p, "new\n")
    assert p.read_text() == "new\n"


# ---- write_owned: planted links -------------------------------------------

def test_symlink_at_target_is_replaced_never_followed(tmp_path, victim):
    d = tmp_path / "svc"
    d.mkdir()
    target = d / "x.env"
    target.symlink_to(victim)
    write_owned(target, "MINE=1\n")
    assert victim.read_text() == "VICTIM\n"          # never written through
    assert not target.is_symlink()                   # the link is gone
    assert stat.S_ISREG(os.lstat(target).st_mode)
    assert target.read_text() == "MINE=1\n"


def test_dangling_symlink_at_target_creates_nothing_at_its_end(tmp_path):
    d = tmp_path / "svc"
    d.mkdir()
    far = tmp_path / "far" / "created-by-root"
    far.parent.mkdir()
    (d / "x.env").symlink_to(far)
    write_owned(d / "x.env", "MINE=1\n")
    assert not far.exists()
    assert (d / "x.env").read_text() == "MINE=1\n"


def test_symlinks_at_predictable_temp_names_are_not_followed(
        tmp_path, victim, monkeypatch):
    d = tmp_path / "svc"
    d.mkdir()
    pid = os.getpid()
    planted = ["x.env.tmp", ".x.env.tmp", "x.env.new", f".x.env.{pid}.tmp",
               f".x.env.{pid}.deadbeef.tmp"]
    for n in planted:
        (d / n).symlink_to(victim)
    # Force the first random name onto the planted link: O_EXCL|O_NOFOLLOW
    # must make that open fail and the helper retry under a fresh name.
    names = iter(["deadbeef", "cafef00d"])
    monkeypatch.setattr(safe_write.secrets, "token_hex", lambda n: next(names))
    write_owned(d / "x.env", "MINE=1\n")
    assert victim.read_text() == "VICTIM\n"
    assert (d / "x.env").read_text() == "MINE=1\n"
    for n in planted:                                # left as they were
        assert (d / n).is_symlink()
    assert _leftovers(d, set(planted) | {"x.env"}) == []


def test_directory_at_target_is_refused(tmp_path):
    (tmp_path / "x.env").mkdir()
    with pytest.raises(OSError, match="not a regular file"):
        write_owned(tmp_path / "x.env", "X=1\n")
    assert (tmp_path / "x.env").is_dir()
    assert _leftovers(tmp_path, {"x.env"}) == []


def test_fifo_at_target_is_refused(tmp_path):
    os.mkfifo(tmp_path / "x.env")
    with pytest.raises(OSError, match="not a regular file"):
        write_owned(tmp_path / "x.env", "X=1\n")
    assert stat.S_ISFIFO(os.lstat(tmp_path / "x.env").st_mode)


def test_symlinked_parent_directory_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(OSError, match="symlink"):
        write_owned(link / "x.env", "X=1\n")
    assert list(real.iterdir()) == []


def test_missing_parent_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_owned(tmp_path / "nope" / "x.env", "X=1\n")


def test_a_failed_write_leaves_the_target_and_no_temp(tmp_path, monkeypatch):
    p = tmp_path / "x.env"
    p.write_text("old\n")

    def boom(fd):
        raise OSError("disk full")
    monkeypatch.setattr(safe_write.os, "fsync", boom)
    with pytest.raises(OSError, match="disk full"):
        write_owned(p, "new\n")
    assert p.read_text() == "old\n"
    assert _leftovers(tmp_path, {"x.env"}) == []


# ---- write_owned: ownership through the descriptor ------------------------

class _Calls:
    def __init__(self):
        self.fchown = []


@pytest.fixture
def as_root(monkeypatch):
    """Pretend to be root; record fchown; forbid chown by path."""
    calls = _Calls()
    monkeypatch.setattr(safe_write.os, "geteuid", lambda: 0)
    monkeypatch.setattr(safe_write.os, "fchown",
                        lambda fd, uid, gid: calls.fchown.append((fd, uid, gid)))

    def no_path_chown(*a, **k):
        raise AssertionError(f"os.chown by path: {a!r}")
    monkeypatch.setattr(safe_write.os, "chown", no_path_chown)
    monkeypatch.setattr(safe_write.os, "lchown", no_path_chown)
    return calls


def test_root_gives_the_file_to_the_directory_owner_by_descriptor(
        tmp_path, as_root):
    d = tmp_path / "svc"
    d.mkdir()
    write_owned(d / "x.env", "X=1\n")
    dst = os.stat(d)
    assert len(as_root.fchown) == 1
    fd, uid, gid = as_root.fchown[0]
    assert isinstance(fd, int) and not isinstance(fd, bool)
    assert (uid, gid) == (dst.st_uid, dst.st_gid)


def test_root_without_owner_from_dir_does_not_chown(tmp_path, as_root):
    write_owned(tmp_path / "x", "X=1\n", owner_from_dir=False)
    assert as_root.fchown == []
    # positive control: the same harness sees the call when it happens
    write_owned(tmp_path / "y", "X=1\n")
    assert len(as_root.fchown) == 1


def test_non_root_does_not_chown(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(safe_write.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(safe_write.os, "fchown", lambda *a: seen.append(a))
    write_owned(tmp_path / "x", "X=1\n")
    assert seen == []
    # positive control
    monkeypatch.setattr(safe_write.os, "geteuid", lambda: 0)
    write_owned(tmp_path / "y", "X=1\n")
    assert len(seen) == 1


# ---- read_nofollow --------------------------------------------------------

def test_read_nofollow_reads_a_regular_file(tmp_path):
    (tmp_path / "a").write_text("A=1\n")
    assert read_nofollow(tmp_path / "a") == "A=1\n"


def test_read_nofollow_absent_reads_none(tmp_path):
    assert read_nofollow(tmp_path / "a") is None


def test_read_nofollow_refuses_a_symlink(tmp_path, victim):
    (tmp_path / "a").symlink_to(victim)
    with pytest.raises(OSError, match="symlink"):
        read_nofollow(tmp_path / "a")


def test_read_nofollow_refuses_a_fifo_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "a")
    with pytest.raises(OSError, match="not a regular file"):
        read_nofollow(tmp_path / "a")


# ---- set_toml_bool --------------------------------------------------------

def _b(text, section="uploader", key="enabled"):
    return tomllib.loads(text)[section][key]


def test_set_toml_bool_replaces_true_with_bare_false():
    out = set_toml_bool("[uploader]\nenabled = true\n", "uploader", "enabled", False)
    assert _b(out) is False
    assert '"false"' not in out
    assert out == "[uploader]\nenabled = false\n"


def test_set_toml_bool_replaces_a_quoted_string_with_a_boolean():
    out = set_toml_bool('[uploader]\nenabled = "true"\n', "uploader", "enabled", False)
    assert _b(out) is False


def test_set_toml_bool_adds_a_missing_section():
    src = '[station]\nname = "x"\n'
    out = set_toml_bool(src, "uploader", "enabled", False)
    assert _b(out) is False
    assert out.startswith(src)
    assert tomllib.loads(out)["station"] == {"name": "x"}


def test_set_toml_bool_adds_a_missing_key_to_its_section():
    src = '[uploader]\nhost = "h"\n\n[other]\nenabled = true\n'
    out = set_toml_bool(src, "uploader", "enabled", False)
    d = tomllib.loads(out)
    assert d["uploader"] == {"host": "h", "enabled": False}
    assert d["other"] == {"enabled": True}


def test_set_toml_bool_on_empty_text():
    assert _b(set_toml_bool("", "uploader", "enabled", False)) is False


def test_set_toml_bool_keeps_a_trailing_comment_and_indent():
    out = set_toml_bool("[uploader]\n  enabled   = true  # on\n",
                        "uploader", "enabled", False)
    assert out == "[uploader]\n  enabled   = false  # on\n"


def test_set_toml_bool_sets_true():
    out = set_toml_bool("[uploader]\nenabled = false\n", "uploader", "enabled", True)
    assert _b(out) is True


def test_set_toml_bool_leaves_the_same_key_in_another_section():
    src = "[a]\nenabled = true\n[uploader]\nenabled = true\n[b]\nenabled = true\n"
    out = set_toml_bool(src, "uploader", "enabled", False)
    d = tomllib.loads(out)
    assert (d["a"]["enabled"], d["uploader"]["enabled"], d["b"]["enabled"]) == \
        (True, False, True)


def test_set_toml_bool_refuses_text_it_cannot_parse():
    with pytest.raises(ValueError):
        set_toml_bool("[uploader\nenabled = true\n", "uploader", "enabled", False)


def test_set_toml_bool_refuses_a_shape_it_cannot_edit_safely():
    # a dotted key at top level: appending [uploader] would redefine it
    with pytest.raises(ValueError):
        set_toml_bool("uploader.enabled = true\n", "uploader", "enabled", False)


def test_set_toml_bool_on_the_shipped_mag_template_changes_one_line():
    src = MAG_TEMPLATE.read_text()
    before = tomllib.loads(src)
    assert before["uploader"]["enabled"] is True     # the template ships it on
    out = set_toml_bool(src, "uploader", "enabled", False)
    after = tomllib.loads(out)
    assert after["uploader"]["enabled"] is False
    before["uploader"]["enabled"] = False
    assert after == before
    diff = [(a, b) for a, b in zip(src.splitlines(), out.splitlines()) if a != b]
    assert len(src.splitlines()) == len(out.splitlines())
    assert len(diff) == 1 and diff[0][1].split("#")[0].split("=")[1].strip() == "false"


# ---- write_toml_bool ------------------------------------------------------

def test_write_toml_bool_on_the_mag_template(tmp_path):
    p = tmp_path / "mag-recorder-config.toml"
    p.write_text(MAG_TEMPLATE.read_text())
    os.chmod(p, 0o640)
    write_toml_bool(p, "uploader", "enabled", False)
    with open(p, "rb") as f:
        assert tomllib.load(f)["uploader"]["enabled"] is False
    assert stat.S_IMODE(os.lstat(p).st_mode) == 0o640   # mode kept


def test_write_toml_bool_replaces_a_link_and_spares_its_victim(tmp_path, victim):
    victim.write_text("[uploader]\nenabled = true\n")
    p = tmp_path / "cfg.toml"
    p.symlink_to(victim)
    with pytest.raises(OSError, match="symlink"):
        write_toml_bool(p, "uploader", "enabled", False)
    assert victim.read_text() == "[uploader]\nenabled = true\n"


def test_write_toml_bool_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_toml_bool(tmp_path / "cfg.toml", "uploader", "enabled", False)


def test_write_toml_bool_rereads_and_raises_on_a_string(tmp_path, monkeypatch):
    p = tmp_path / "cfg.toml"
    p.write_text("[uploader]\nenabled = true\n")
    monkeypatch.setattr(safe_write, "set_toml_bool",
                        lambda text, s, k, v: '[uploader]\nenabled = "false"\n')
    with pytest.raises(ValueError, match="boolean"):
        write_toml_bool(p, "uploader", "enabled", False)


def test_set_toml_bool_refuses_to_edit_inside_a_multiline_string():
    # The line editor would touch a line inside the string and add no key;
    # the result parses, so only the equality check can catch it.
    src = '[uploader]\nnote = """\nenabled = true\n"""\n'
    with pytest.raises(ValueError, match="edit it by hand"):
        set_toml_bool(src, "uploader", "enabled", False)
