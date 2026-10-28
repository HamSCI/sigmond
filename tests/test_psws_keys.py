"""`smd psws save-keys` / `restore-keys`: a station's registered keys survive a reflash.

B4's 10-26 reflash had no site-keys.tar.gz on the stick.  The keys went back
by hand, nothing ran `smd psws verify`, and PSWS refused uploads for a day
unnoticed.  save_keys writes the same archive the install wizard restores
(`tar czf site-keys.tar.gz -C / etc/hs-uploader/keys home/timestd/.ssh`);
restore_keys puts it back with the wizard's owners and modes, then returns
verify's exit code.

The archive carries private keys and restore runs as root, so restore checks
EVERY member before it writes ANY: a symlink, a hard link, a device, an
absolute path, a `..` component or a path outside the two trees refuses the
whole archive (exit 4) and leaves the root untouched.

Every test works under a tmp_path root with a fake chown and a fake verify;
nothing here touches the host's /etc or elevates.
"""
import io
import os
import stat
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

from sigmond import psws                                         # noqa: E402

KEYS = "etc/hs-uploader/keys"
SSH = "home/timestd/.ssh"

IDS = {("hsupload", "sigmond"): (1001, 1500),
       ("root", "sigmond"): (0, 1500),
       ("timestd", "timestd"): (1002, 1002)}


@pytest.fixture(autouse=True)
def _fake_ids(monkeypatch):
    """The test host has no hsupload/timestd users; resolve names from IDS."""
    monkeypatch.setattr(psws, "_lookup_owner", lambda u, g: IDS.get((u, g)))


class FakeChown:
    def __init__(self):
        self.calls = {}

    def __call__(self, path, uid, gid, **kw):
        self.calls[str(path)] = (uid, gid)


def _station(root: Path) -> dict:
    """A station's two key trees; returns {relpath: (bytes, mode)}."""
    files = {
        f"{KEYS}/id_ed25519_host": (b"PRIVATE-HOST\n", 0o600),
        f"{KEYS}/id_ed25519_host.pub": (b"ssh-ed25519 AAAA host\n", 0o644),
        f"{KEYS}/known_hosts": (b"psws ssh-ed25519 BBBB\n", 0o644),
        f"{SSH}/id_ed25519": (b"PRIVATE-TIMESTD\n", 0o600),
        f"{SSH}/id_ed25519.pub": (b"ssh-ed25519 CCCC timestd\n", 0o644),
        f"{SSH}/authorized_keys": (b"ssh-ed25519 DDDD op\n", 0o600),
    }
    for rel, (data, mode) in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        os.chmod(p, mode)
    os.chmod(root / SSH, 0o700)
    return files


def _tree_listing(root: Path) -> list:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _evil_archive(path: Path, members: list) -> Path:
    """members: (TarInfo, bytes|None); a good key file always rides first."""
    with tarfile.open(path, "w:gz") as tf:
        ok = tarfile.TarInfo(f"{KEYS}/id_ed25519_host")
        ok.size = 4
        tf.addfile(ok, io.BytesIO(b"good"))
        for ti, data in members:
            if data is not None:
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
            else:
                tf.addfile(ti)
    return path


def _never_verify():
    raise AssertionError("verify must not run after a refusal")


def _save(tmp_path) -> tuple:
    src = tmp_path / "old"
    files = _station(src)
    out = tmp_path / "out" / "site-keys.tar.gz"
    out.parent.mkdir()
    assert psws.save_keys(out, root=src) == 0
    return files, out


# ── save ────────────────────────────────────────────────────────────────────

def test_save_with_no_keys_returns_1(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    out = tmp_path / "site-keys.tar.gz"
    assert psws.save_keys(out, root=root) == 1
    assert not out.exists()


def test_saved_archive_matches_the_wizard_member_layout(tmp_path):
    _, out = _save(tmp_path)
    with tarfile.open(out, "r:gz") as tf:
        names = tf.getnames()
    assert names
    for n in names:
        assert n == KEYS or n.startswith(KEYS + "/") \
            or n == SSH or n.startswith(SSH + "/"), n
    # The wizard's `tar xzf - -C / etc/hs-uploader/keys home/timestd/.ssh`
    # names both trees as members; each must appear exactly as written.
    assert KEYS in names and SSH in names


def test_saved_archive_is_mode_600(tmp_path):
    _, out = _save(tmp_path)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_save_with_only_one_tree_saves_it(tmp_path):
    src = tmp_path / "old"
    (src / KEYS).mkdir(parents=True)
    (src / KEYS / "id_ed25519_host").write_bytes(b"k")
    out = tmp_path / "site-keys.tar.gz"
    assert psws.save_keys(out, root=src) == 0
    with tarfile.open(out, "r:gz") as tf:
        assert f"{KEYS}/id_ed25519_host" in tf.getnames()
        assert not any(n.startswith("home/") for n in tf.getnames())


def test_save_skips_a_symlink_restore_would_refuse(tmp_path):
    src = tmp_path / "old"
    _station(src)
    (src / SSH / "config").symlink_to("/etc/passwd")
    out = tmp_path / "site-keys.tar.gz"
    assert psws.save_keys(out, root=src) == 0
    with tarfile.open(out, "r:gz") as tf:
        assert f"{SSH}/config" not in tf.getnames()
    # ... so its own archive restores cleanly.
    dst = tmp_path / "new"
    dst.mkdir()
    assert psws.restore_keys(out, root=dst, chown=FakeChown(),
                             verify=lambda: 0) == 0


# ── restore: round trip and the wizard's owners ─────────────────────────────

def test_save_then_restore_round_trips_bytes_and_modes(tmp_path):
    files, out = _save(tmp_path)
    dst = tmp_path / "new"
    dst.mkdir()
    assert psws.restore_keys(out, root=dst, chown=FakeChown(),
                             verify=lambda: 0) == 0
    for rel, (data, mode) in files.items():
        p = dst / rel
        assert p.read_bytes() == data, rel
        assert stat.S_IMODE(p.stat().st_mode) == mode, rel
    assert stat.S_IMODE((dst / SSH).stat().st_mode) == 0o700


def test_restore_overwrites_a_freshly_generated_key(tmp_path):
    """Config render makes a fresh key when none exists; the registered one wins."""
    _, out = _save(tmp_path)
    dst = tmp_path / "new"
    (dst / KEYS).mkdir(parents=True)
    (dst / KEYS / "id_ed25519_host").write_bytes(b"FRESH-UNREGISTERED\n")
    assert psws.restore_keys(out, root=dst, chown=FakeChown(),
                             verify=lambda: 0) == 0
    assert (dst / KEYS / "id_ed25519_host").read_bytes() == b"PRIVATE-HOST\n"


def test_restore_sets_owners_like_the_wizard(tmp_path):
    _, out = _save(tmp_path)
    dst = tmp_path / "new"
    dst.mkdir()
    ch = FakeChown()
    assert psws.restore_keys(out, root=dst, chown=ch, verify=lambda: 0) == 0
    hs = IDS[("hsupload", "sigmond")]
    ts = IDS[("timestd", "timestd")]
    for rel in (KEYS, f"{KEYS}/id_ed25519_host", f"{KEYS}/id_ed25519_host.pub",
                f"{KEYS}/known_hosts"):
        assert ch.calls[str(dst / rel)] == hs, rel
    for rel in (SSH, f"{SSH}/id_ed25519", f"{SSH}/id_ed25519.pub",
                f"{SSH}/authorized_keys"):
        assert ch.calls[str(dst / rel)] == ts, rel
    assert stat.S_IMODE((dst / KEYS / "id_ed25519_host").stat().st_mode) == 0o600
    assert stat.S_IMODE((dst / KEYS / "id_ed25519_host.pub").stat().st_mode) == 0o644
    assert stat.S_IMODE((dst / SSH).stat().st_mode) == 0o700


def test_restore_fixes_modes_the_archive_got_wrong(tmp_path):
    path = tmp_path / "loose.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        for name, mode in ((f"{KEYS}/id_ed25519_host", 0o644),
                           (f"{KEYS}/id_ed25519_host.pub", 0o600)):
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode = 1, mode
            tf.addfile(ti, io.BytesIO(b"x"))
    dst = tmp_path / "new"
    dst.mkdir()
    assert psws.restore_keys(path, root=dst, chown=FakeChown(),
                             verify=lambda: 0) == 0
    assert stat.S_IMODE((dst / KEYS / "id_ed25519_host").stat().st_mode) == 0o600
    assert stat.S_IMODE((dst / KEYS / "id_ed25519_host.pub").stat().st_mode) == 0o644


def test_restore_falls_back_to_root_when_hsupload_is_absent(tmp_path, monkeypatch):
    ids = {k: v for k, v in IDS.items() if k[0] != "hsupload"}
    monkeypatch.setattr(psws, "_lookup_owner", lambda u, g: ids.get((u, g)))
    _, out = _save(tmp_path)
    dst = tmp_path / "new"
    dst.mkdir()
    ch = FakeChown()
    assert psws.restore_keys(out, root=dst, chown=ch, verify=lambda: 0) == 0
    assert ch.calls[str(dst / KEYS / "id_ed25519_host")] == IDS[("root", "sigmond")]


def test_restore_returns_verifys_code(tmp_path):
    _, out = _save(tmp_path)
    dst = tmp_path / "new"
    dst.mkdir()
    assert psws.restore_keys(out, root=dst, chown=FakeChown(),
                             verify=lambda: 3) == 3


# ── restore: refusals write nothing ─────────────────────────────────────────

def _assert_refused(tmp_path, members):
    arc = _evil_archive(tmp_path / "evil.tar.gz", members)
    dst = tmp_path / "new"
    dst.mkdir()
    ch = FakeChown()
    assert psws.restore_keys(arc, root=dst, chown=ch, verify=_never_verify) == 4
    assert _tree_listing(dst) == []
    assert ch.calls == {}
    # nothing escaped the root either
    assert not (tmp_path / "escaped").exists()


def test_restore_refuses_traversal(tmp_path):
    _assert_refused(tmp_path, [(tarfile.TarInfo(f"{KEYS}/../../../../escaped"), b"x")])


def test_restore_refuses_a_dotdot_that_lands_back_inside(tmp_path):
    _assert_refused(tmp_path, [(tarfile.TarInfo(f"{SSH}/../.ssh/authorized_keys"), b"x")])


def test_restore_refuses_an_absolute_path(tmp_path):
    _assert_refused(tmp_path, [(tarfile.TarInfo(f"/{KEYS}/id_ed25519_host"), b"x")])


def test_restore_refuses_a_symlink(tmp_path):
    ti = tarfile.TarInfo(f"{SSH}/authorized_keys")
    ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/shadow"
    _assert_refused(tmp_path, [(ti, None)])


def test_restore_refuses_a_hard_link(tmp_path):
    ti = tarfile.TarInfo(f"{SSH}/authorized_keys")
    ti.type, ti.linkname = tarfile.LNKTYPE, f"{KEYS}/id_ed25519_host"
    _assert_refused(tmp_path, [(ti, None)])


@pytest.mark.parametrize("kind", [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE])
def test_restore_refuses_a_device_or_fifo(tmp_path, kind):
    ti = tarfile.TarInfo(f"{KEYS}/dev")
    ti.type = kind
    _assert_refused(tmp_path, [(ti, None)])


def test_restore_refuses_a_path_outside_the_key_trees(tmp_path):
    _assert_refused(tmp_path, [(tarfile.TarInfo("etc/sudoers.d/evil"), b"x")])


def test_restore_refuses_a_sibling_that_shares_a_prefix(tmp_path):
    _assert_refused(tmp_path, [(tarfile.TarInfo("etc/hs-uploader/keys2/x"), b"x")])


def test_restore_refuses_a_tree_parent_entry(tmp_path):
    """The wizard's tar names the trees themselves, never etc/ or home/."""
    ti = tarfile.TarInfo("home/timestd")
    ti.type = tarfile.DIRTYPE
    _assert_refused(tmp_path, [(ti, None)])


def test_restore_refuses_when_a_key_tree_on_the_target_is_a_symlink(tmp_path):
    """A planted link at the tree root would carry the write elsewhere."""
    _, out = _save(tmp_path)
    dst = tmp_path / "new"
    (dst / "home" / "timestd").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (dst / SSH).symlink_to(elsewhere)
    assert psws.restore_keys(out, root=dst, chown=FakeChown(),
                             verify=_never_verify) == 4
    assert list(elsewhere.iterdir()) == []
    assert not (dst / KEYS).exists()


def test_restore_refuses_an_archive_that_is_not_one(tmp_path):
    bad = tmp_path / "site-keys.tar.gz"
    bad.write_bytes(b"not a tarball")
    dst = tmp_path / "new"
    dst.mkdir()
    assert psws.restore_keys(bad, root=dst, chown=FakeChown(),
                             verify=_never_verify) == 4
    assert _tree_listing(dst) == []


def test_restore_refuses_an_empty_archive(tmp_path):
    arc = tmp_path / "site-keys.tar.gz"
    with tarfile.open(arc, "w:gz"):
        pass
    dst = tmp_path / "new"
    dst.mkdir()
    assert psws.restore_keys(arc, root=dst, chown=FakeChown(),
                             verify=_never_verify) == 4


def test_restore_never_prints_key_contents(tmp_path, capsys):
    _, out = _save(tmp_path)
    dst = tmp_path / "new"
    dst.mkdir()
    psws.restore_keys(out, root=dst, chown=FakeChown(), verify=lambda: 0)
    printed = capsys.readouterr()
    assert b"PRIVATE" not in (printed.out + printed.err).encode()


# ── the CLI wrapper: default output and owner ───────────────────────────────

def test_save_defaults_to_the_invoking_users_home_and_chowns_it(tmp_path, monkeypatch):
    src = tmp_path / "old"
    _station(src)
    home = tmp_path / "home-op"
    home.mkdir()
    monkeypatch.setattr(psws, "_invoking_user", lambda: ("op", 1234, 1234, home))
    ch = FakeChown()
    assert psws.cmd_site_save_keys(None, root=src, chown=ch) == 0
    out = home / "site-keys.tar.gz"
    assert out.is_file()
    assert ch.calls[str(out)] == (1234, 1234)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_invoking_user_is_sudo_user_when_set(monkeypatch):
    import pwd
    me = pwd.getpwuid(os.getuid())
    monkeypatch.setenv("SUDO_USER", me.pw_name)
    name, uid, gid, home = psws._invoking_user()
    assert (name, uid, gid, home) == (me.pw_name, me.pw_uid, me.pw_gid,
                                      Path(me.pw_dir))


def test_smd_psws_offers_both_verbs():
    import subprocess
    env = dict(os.environ, PYTHONPATH=str(REPO / "lib"),
               SIGMOND_NO_VENV_REEXEC="1")
    out = subprocess.run([sys.executable, str(REPO / "bin" / "smd"), "psws",
                          "--help"], capture_output=True, text=True, env=env,
                         timeout=60).stdout
    assert "save-keys" in out and "restore-keys" in out


def test_restore_refuses_a_path_that_is_both_file_and_directory(tmp_path):
    f = tarfile.TarInfo(f"{SSH}/x")
    d = tarfile.TarInfo(f"{SSH}/x")
    d.type = tarfile.DIRTYPE
    _assert_refused(tmp_path, [(f, b"x"), (d, None)])


def test_smd_psws_restore_keys_elevates_like_verify():
    """Under the suite smd refuses to elevate; restore-keys must reach that
    refusal rather than run unprivileged."""
    import subprocess
    env = dict(os.environ, PYTHONPATH=str(REPO / "lib"),
               SIGMOND_NO_VENV_REEXEC="1", SIGMOND_TEST_NO_ELEVATE="1")
    for argv in (["save-keys"], ["restore-keys", "/nonexistent.tar.gz"]):
        r = subprocess.run([sys.executable, str(REPO / "bin" / "smd"), "psws",
                            *argv], capture_output=True, text=True, env=env,
                           timeout=60)
        if os.geteuid() != 0:
            assert r.returncode == 1 and "refusing to elevate" in r.stderr, argv


def test_restore_makes_the_ssh_dir_700_whatever_the_archive_says(tmp_path):
    path = tmp_path / "loose.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        d = tarfile.TarInfo(SSH)
        d.type, d.mode = tarfile.DIRTYPE, 0o755
        tf.addfile(d)
        f = tarfile.TarInfo(f"{SSH}/authorized_keys")
        f.size, f.mode = 1, 0o600
        tf.addfile(f, io.BytesIO(b"x"))
    dst = tmp_path / "new"
    dst.mkdir()
    assert psws.restore_keys(path, root=dst, chown=FakeChown(),
                             verify=lambda: 0) == 0
    assert stat.S_IMODE((dst / SSH).stat().st_mode) == 0o700
