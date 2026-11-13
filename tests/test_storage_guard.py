"""smd storage guard: a data mount's writers wait for it, and the bare
directory beneath refuses writes (data-volume design 2026-11-08, the two
guards for the stations moved by hand; v3.91)."""
import types
from pathlib import Path

import pytest

from sigmond.storage import guard as G

MOUNTINFO = (
    "22 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n"
    "90 22 8:17 / /var/lib/timestd rw,relatime - ext4 /dev/sdb rw\n"
    "91 22 8:33 / /var/lib/sigmond/archive rw - ext4 /dev/sdc1 rw\n"
    "92 22 0:40 / /run rw - tmpfs tmpfs rw\n"
)


@pytest.fixture
def mi(tmp_path):
    p = tmp_path / "mountinfo"
    p.write_text(MOUNTINFO)
    return p


def test_data_mounts_finds_timestd(mi):
    paths = {"hf-timestd": ["/var/lib/timestd/raw_buffer", "/var/lib/timestd/state"],
             "psk-recorder": ["/var/lib/psk-recorder"]}
    assert G.data_mounts(mi, paths, "/var/lib/sigmond/archive") == {"/var/lib/timestd": ["hf-timestd"]}


def test_root_and_archive_excluded(mi):
    paths = {"x": ["/var/lib/sigmond/archive/timestd"], "y": ["/srv/data"]}
    assert G.data_mounts(mi, paths, "/var/lib/sigmond/archive") == {}


def test_units_for_collapses_templates_and_adds_timestd_extras():
    refs = [types.SimpleNamespace(unit="psk-recorder@ft8.service", kind="service", orphaned=False),
            types.SimpleNamespace(unit="psk-recorder@ft4.service", kind="service", orphaned=False),
            types.SimpleNamespace(unit="psk-recorder.timer", kind="timer", orphaned=False)]
    assert G.units_for("psk-recorder", resolve=lambda c: refs) == ["psk-recorder@.service"]
    got = G.units_for("hf-timestd", resolve=lambda c: [])
    assert "timestd-events.service" in got and "timestd-archive.service" in got


def test_dropin_text():
    assert G.dropin_text(["/var/lib/timestd"]) == (
        "# sigmond: data volume guard (smd storage guard)\n"
        "[Unit]\nRequiresMountsFor=/var/lib/timestd\n")


def test_dropins_written_once(tmp_path):
    etc = tmp_path / "etc"
    plan = {"timestd-core-recorder.service": ["/var/lib/timestd"],
            "psk-recorder@.service": ["/var/lib/timestd"]}
    changed = G.write_dropins(plan, etc_dir=etc)
    assert sorted(changed) == ["psk-recorder@.service", "timestd-core-recorder.service"]
    f = etc / "psk-recorder@.service.d" / "sigmond-data-volume.conf"
    assert f.read_text().endswith("RequiresMountsFor=/var/lib/timestd\n")
    assert G.write_dropins(plan, etc_dir=etc) == []


def test_hand_dropin_left_alone(tmp_path):
    etc = tmp_path / "etc"
    d = etc / "timestd-fusion.service.d"
    d.mkdir(parents=True)
    (d / "10-data-disk.conf").write_text("[Unit]\nRequiresMountsFor=/var/lib/timestd\n")
    assert G.write_dropins({"timestd-fusion.service": ["/var/lib/timestd"]}, etc_dir=etc) == []
    assert sorted(p.name for p in d.iterdir()) == ["10-data-disk.conf"]


def test_dry_run_writes_nothing(tmp_path):
    etc = tmp_path / "etc"
    assert G.write_dropins({"a.service": ["/m"]}, etc_dir=etc, dry_run=True) == ["a.service"]
    assert not etc.exists()


class Seal:
    def __init__(self, tmp_path, under_files=()):
        self.tmp, self.calls = tmp_path / "bind", []
        self.under_files = under_files

    def bind(self, src, dst):
        self.calls.append(["bind", src, dst])
        u = Path(dst) / "var/lib/timestd"
        u.mkdir(parents=True)
        for f in self.under_files:
            (u / f).write_text("x")

    def unbind(self, dst):
        self.calls.append(["unbind", dst])

    def run(self, argv, **kw):
        self.calls.append(argv)
        import subprocess
        out = "--------------e------- x\n" if argv[0] == "lsattr" else ""
        return subprocess.CompletedProcess(argv, 0, out, "")

    def mkdtemp(self):
        self.tmp.mkdir()
        return str(self.tmp)


def test_seal_bare_dir_sealed_and_unbound(tmp_path, mi):
    s = Seal(tmp_path)
    r = G.seal_bare_dir("/var/lib/timestd", mountinfo=mi, bind=s.bind, unbind=s.unbind,
                        run=s.run, mkdtemp=s.mkdtemp)
    assert r == "sealed"
    kinds = [c[0] for c in s.calls]
    assert kinds[0] == "bind" and s.calls[0][1] == "/"
    under = str(s.tmp / "var/lib/timestd")
    assert ["chmod", "000", under] in s.calls
    assert ["chattr", "+i", under] in s.calls
    assert kinds[-1] == "unbind"
    assert kinds.index("chmod") < kinds.index("chattr") < kinds.index("unbind")


def test_seal_not_empty_left_alone(tmp_path, mi):
    s = Seal(tmp_path, under_files=("stray.bin",))
    r = G.seal_bare_dir("/var/lib/timestd", mountinfo=mi, bind=s.bind, unbind=s.unbind,
                        run=s.run, mkdtemp=s.mkdtemp)
    assert r.startswith("not-empty")
    assert not any(c[0] in ("chmod", "chattr") for c in s.calls)
    assert s.calls[-1][0] == "unbind"


def test_seal_already(tmp_path, mi):
    s = Seal(tmp_path)

    def run(argv, **kw):
        import subprocess
        s.calls.append(argv)
        out = "----i---------e------- x\n" if argv[0] == "lsattr" else ""
        return subprocess.CompletedProcess(argv, 0, out, "")
    r = G.seal_bare_dir("/var/lib/timestd", mountinfo=mi, bind=s.bind, unbind=s.unbind,
                        run=run, mkdtemp=s.mkdtemp)
    assert r == "already"
    assert not any(c[0] == "chattr" for c in s.calls)


def test_unguarded(tmp_path, mi):
    etc = tmp_path / "etc"
    plan = {"timestd-core-recorder.service": ["/var/lib/timestd"]}
    assert G.unguarded(plan, etc_dir=etc) == ["timestd-core-recorder.service"]
    G.write_dropins(plan, etc_dir=etc)
    assert G.unguarded(plan, etc_dir=etc) == []


def test_check_warns_unguarded(tmp_path, mi, monkeypatch):
    from sigmond.commands import check as C
    etc = tmp_path / "etc"
    etc.mkdir()
    monkeypatch.setattr(C, "GUARD_MOUNTINFO", mi)
    monkeypatch.setattr(C, "GUARD_ETC", etc)
    env = types.SimpleNamespace(disk=lambda p: (100, 50))
    ln = C.storage_line(env)
    assert ln.mark == C.WAIT and "/var/lib/timestd" in ln.text and "smd storage guard" in ln.text
    G.write_dropins({"timestd-core-recorder.service": ["/var/lib/timestd"]}, etc_dir=etc)
    assert C.storage_line(env).mark == C.OK
