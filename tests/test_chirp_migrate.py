"""`smd admin chirp convert`: a receiver's codar-sounder instance gains a
chirp-recorder beside it, carrying the same channels.

Plan Task 21 (tasks/chirp-registry/2026-11-19-chirp-recorder-plan.md).
Nothing here touches a real system: `run` is a fake, and `tmp_path` stands
for `/`.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

import pytest

from sigmond.chirp_migrate import (ConvertTarget, apply, apply_convert,
                                   convert_steps, convert_targets,
                                   state_path)


def codar_instance(root: Path, key: str, rid: str, radiod: str, *,
                   centres) -> Path:
    """codar-sounder's per-instance file and the sources file sigmond
    writes beside it, as `instance split` and `receiver apply` leave them."""
    d = root / "etc" / "codar-sounder"
    d.mkdir(parents=True, exist_ok=True)
    body = f'[instance]\nreporter_id = "{rid}"\n\n' if rid else ""
    for i, c in enumerate(centres):
        body += (f'[[radiod]]\nstatus = "{radiod}"\n'
                 f'channel_name = "c{c // 1000}"\n\n'
                 f'[[radiod.transmitter]]\nid = "TX{i}"\n'
                 f'center_freq_hz = {c}\n\n')
    p = d / f"{key}.toml"
    p.write_text(body)
    src = root / "etc" / "sigmond" / "clients"
    src.mkdir(parents=True, exist_ok=True)
    (src / f"codar-sounder@{key}.sources.toml").write_text(
        f'selected = ["radiod:{radiod}"]\n')
    return p


GRID_WARNING = ("WARNING chirp_recorder.config: instance AC0G-B4: only "
                "grid_square given; timing uses its centre")


class FakeRun:
    """Every command convert runs, recorded.  `config init` writes the file
    the real one would; `validate --json` answers `validate_json` on stdout
    and, as on a station located by grid, a WARNING on stderr.  Returns
    (rc, stdout, stderr)."""

    def __init__(self, root: Path, capsys):
        self.root = root
        self.capsys = capsys
        self.calls: list = []
        self.validate_json = None
        self.fail = set()          # argv[0:2] or argv[0:3] tuples that exit 1
        self.after = {}            # argv prefix tuple -> callable, run after
        self._out = ""

    @property
    def output(self) -> str:
        self._out += "".join(self.capsys.readouterr())
        return self._out

    def __call__(self, argv, env=None):
        argv = list(argv)
        self.calls.append(argv)
        try:
            return self._answer(argv)
        finally:
            for prefix, fn in self.after.items():
                if tuple(argv[:len(prefix)]) == prefix:
                    fn()

    def _answer(self, argv):
        if tuple(argv[:2]) in self.fail or tuple(argv[:3]) in self.fail:
            return 1, "", f"{argv[0]}: refused"
        if argv[:3] == ["chirp-recorder", "config", "init"]:
            key = argv[argv.index("--instance") + 1]
            d = self.root / "etc" / "chirp-recorder"
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o755)             # as install.sh's mkdir -p as root
            (d / f"{key}.toml").write_text("# chirp\n")
            return 0, f"chirp-recorder: wrote {d / key}.toml\n", ""
        if argv[:2] == ["chirp-recorder", "validate"]:
            doc = self.validate_json or {
                "ok": True, "issues": [],
                "capacity": {"admitted": ["4.4630MHz"], "refused": [],
                             "estimated": False}}
            doc = {"ok": True, "issues": [], **doc}
            return (0 if doc["ok"] else 1), json.dumps(doc), GRID_WARNING + "\n"
        return 0, "", ""


@pytest.fixture(autouse=True)
def trusted_is_me(monkeypatch):
    """Root owns the state directory on a station; here the test user does."""
    from sigmond import instance_move as IM
    monkeypatch.setattr(IM, "_trusted_uid", lambda: os.getuid())


@pytest.fixture
def tmp_root(tmp_path):
    return tmp_path


@pytest.fixture
def fake_run(tmp_root, capsys):
    return FakeRun(tmp_root, capsys)


# -- the brief's five ------------------------------------------------------

def test_convert_carries_the_codar_channels(tmp_root, fake_run):
    codar_instance(tmp_root, "AC0G-B4", "AC0G/B4", "b4-status.local", centres=[4_463_000, 13_475_000])
    (t,), _ = convert_targets(tmp_root / "etc", run=fake_run)
    argv = [list(s.argv) for s in convert_steps(t)]                      # pre-flight C9
    assert ["--from-codar", str(tmp_root / "etc/codar-sounder/AC0G-B4.toml")] == argv[0][-2:]
    assert argv[1][:3] == ["chirp-recorder", "validate", "--json"]
    assert ["hs-uploader", "sink", "set", "chirp-recorder", "fill"] == argv[2][:5]


def test_convert_refuses_when_capacity_short(tmp_root, fake_run):          # Review Focus 3
    codar_instance(tmp_root, "AC0G-B4", "AC0G/B4", "b4-status.local", centres=[4_463_000, 13_475_000])
    fake_run.validate_json = {"capacity": {"admitted": ["c4463"], "refused": [["c13475", "cpu pressure 22.0 %"]],
                                           "estimated": False}}
    rc = apply_convert(tmp_root, run=fake_run)
    assert rc != 0 and not (tmp_root / "etc/chirp-recorder/AC0G-B4.toml").exists()
    assert not any("systemctl" in c[0] for c in fake_run.calls) and "22.0" in fake_run.output


def test_legacy_radiod_keyed_codar_refuses(tmp_root, fake_run):
    codar_instance(tmp_root, "b4-status.local", "", "b4-status.local", centres=[4_463_000])
    targets, refused = convert_targets(tmp_root / "etc", run=fake_run)
    assert targets == [] and "instance split codar-sounder" in refused[0]


def test_convert_follows_moved_instance(tmp_root, fake_run):               # Review Focus 5
    codar_instance(tmp_root, "N8UR-SIGMOND", "N8UR-SIGMOND", "n8ur-status.local", centres=[4_463_000])
    (t,), _ = convert_targets(tmp_root / "etc", run=fake_run)
    assert (t.key, t.radiod) == ("N8UR-SIGMOND", "n8ur-status.local")


def test_second_run_does_nothing(tmp_root, fake_run):
    codar_instance(tmp_root, "AC0G-B4", "AC0G/B4", "b4-status.local", centres=[4_463_000])
    apply_convert(tmp_root, run=fake_run); n = len(fake_run.calls)
    apply_convert(tmp_root, run=fake_run)
    assert len(fake_run.calls) == n


# -- the rest of the contract ----------------------------------------------

def _b4(root):
    codar_instance(root, "AC0G-B4", "AC0G/B4", "b4-status.local",
                   centres=[4_463_000])


def test_steps_run_in_the_briefs_order(tmp_root, fake_run):
    _b4(tmp_root)
    (t,), _ = convert_targets(tmp_root / "etc", run=fake_run)
    argv = [list(s.argv) for s in convert_steps(t)]
    assert argv[0] == ["chirp-recorder", "config", "init", "--non-interactive",
                       "--instance", "AC0G-B4", "--reporter-id", "AC0G/B4",
                       "--radiod-status", "b4-status.local", "--from-codar",
                       str(tmp_root / "etc/codar-sounder/AC0G-B4.toml")]
    assert argv[1] == ["chirp-recorder", "validate", "--json",
                       "--instance", "AC0G-B4"]
    assert argv[2] == ["hs-uploader", "sink", "set", "chirp-recorder", "fill",
                       "--reason", "side by side with codar-sounder",
                       "--by", "smd admin chirp convert"]
    assert argv[3] == []                           # topology, in process
    assert argv[4] == ["smd", "admin", "uploader", "manifest", "--write"]
    assert argv[5] == ["systemctl", "enable", "--now",
                       "chirp-recorder@AC0G-B4.service"]
    assert len(argv) == 7 and argv[6] == []        # the state file
    assert [s.phase for s in convert_steps(t)] == [
        "config", "validate", "sink", "topology", "manifest", "start", "state"]


def test_convert_writes_the_state_file_and_leaves_codar_alone(tmp_root, fake_run):
    _b4(tmp_root)
    assert apply_convert(tmp_root, run=fake_run) == 0
    doc = json.loads(state_path(tmp_root / "etc", "AC0G-B4").read_text())
    assert doc["key"] == "AC0G-B4" and doc["reporter_id"] == "AC0G/B4"
    assert doc["radiod"] == "b4-status.local"
    assert doc["codar_unit"] == "codar-sounder@AC0G-B4.service"
    assert doc["channels"] == ["4.4630MHz"]
    assert doc["verdict"] is None and doc["retired_at"] is None
    assert doc["started_at"].endswith("Z")
    assert state_path(tmp_root / "etc", "AC0G-B4") == \
        tmp_root / "var/lib/sigmond/chirp-migrate/AC0G-B4.json"
    # codar-sounder: no argv names it except the --from-codar path and the
    # brief's sink reason.
    for c in fake_run.calls:
        rest = [a for a in c if a not in (
            str(tmp_root / "etc/codar-sounder/AC0G-B4.toml"),
            "side by side with codar-sounder")]
        assert not any("codar-sounder" in a for a in rest), c
    assert (tmp_root / "etc/codar-sounder/AC0G-B4.toml").exists()


def test_dry_run_runs_nothing_and_writes_nothing(tmp_root, fake_run):
    _b4(tmp_root)
    rc = apply_convert(tmp_root, run=fake_run, dry_run=True)
    assert rc == 0 and fake_run.calls == []
    assert not (tmp_root / "var").exists()
    out = fake_run.output
    assert "--from-codar" in out and "chirp-recorder@AC0G-B4.service" in out


def test_a_refused_config_init_stops_before_anything_else(tmp_root, fake_run):
    _b4(tmp_root)
    fake_run.fail.add(("chirp-recorder", "config", "init"))
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert len(fake_run.calls) == 1
    assert "nothing changed" in fake_run.output
    assert not state_path(tmp_root / "etc", "AC0G-B4").exists()


def test_validate_that_fails_removes_the_file(tmp_root, fake_run):
    _b4(tmp_root)
    fake_run.validate_json = {"ok": False, "capacity": None, "issues": [
        {"severity": "fail", "instance": "AC0G-B4", "message": "bad channel"}]}
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert not (tmp_root / "etc/chirp-recorder/AC0G-B4.toml").exists()
    assert "bad channel" in fake_run.output
    assert not any(c[0] in ("hs-uploader", "systemctl") for c in fake_run.calls)


def test_a_failure_before_the_unit_starts_removes_the_file(tmp_root, fake_run):
    _b4(tmp_root)
    fake_run.fail.add(("hs-uploader", "sink"))
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert not (tmp_root / "etc/chirp-recorder/AC0G-B4.toml").exists()
    assert not any(c[0] == "systemctl" for c in fake_run.calls)
    assert not state_path(tmp_root / "etc", "AC0G-B4").exists()


def test_a_failed_start_writes_no_state(tmp_root, fake_run):
    _b4(tmp_root)
    fake_run.fail.add(("systemctl", "enable"))
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert not state_path(tmp_root / "etc", "AC0G-B4").exists()


def test_an_existing_chirp_config_refuses(tmp_root, fake_run):
    _b4(tmp_root)
    d = tmp_root / "etc/chirp-recorder"
    d.mkdir(parents=True)
    (d / "AC0G-B4.toml").write_text("# measuring\n")
    targets, refused = convert_targets(tmp_root / "etc", run=fake_run)
    assert targets == [] and "AC0G-B4.toml" in refused[0]
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert (d / "AC0G-B4.toml").read_text() == "# measuring\n"
    assert fake_run.calls == []


def test_an_unsplit_shared_config_refuses(tmp_root, fake_run):
    d = tmp_root / "etc/codar-sounder"
    d.mkdir(parents=True)
    (d / "codar-sounder-config.toml").write_text(
        '[[radiod]]\nstatus = "b4-status.local"\n')
    targets, refused = convert_targets(tmp_root / "etc", run=fake_run)
    assert targets == []
    assert any("instance split codar-sounder" in r for r in refused)


def test_a_key_converts_one_receiver(tmp_root, fake_run):
    _b4(tmp_root)
    codar_instance(tmp_root, "AC0G-B1A", "AC0G/B1A", "b1a-status.local",
                   centres=[4_463_000])
    assert apply_convert(tmp_root, key="AC0G/B1A", run=fake_run) == 0
    assert state_path(tmp_root / "etc", "AC0G-B1A").exists()
    assert not state_path(tmp_root / "etc", "AC0G-B4").exists()


def test_a_key_with_no_instance_refuses(tmp_root, fake_run):
    _b4(tmp_root)
    assert apply_convert(tmp_root, key="AC0G-B9", run=fake_run) == 1
    assert fake_run.calls == []


def test_a_mismatched_reporter_waits_for_the_move(tmp_root, fake_run):
    codar_instance(tmp_root, "N8UR", "N8UR-SIGMOND", "n8ur-status.local",
                   centres=[4_463_000])
    targets, refused = convert_targets(tmp_root / "etc", run=fake_run)
    assert targets == [] and "receiver apply" in refused[0]


def test_apply_takes_the_steps_alone(tmp_root, fake_run):
    _b4(tmp_root)
    (t,), _ = convert_targets(tmp_root / "etc", run=fake_run)
    assert isinstance(t, ConvertTarget)
    assert apply(convert_steps(t), run=fake_run) == 0
    assert state_path(tmp_root / "etc", "AC0G-B4").exists()


# -- the verb ----------------------------------------------------------------

def _load_smd():
    import importlib.util
    import os
    from unittest import mock
    spec = importlib.util.spec_from_loader('smd_mod_chirp_migrate', loader=None)
    smd = importlib.util.module_from_spec(spec)
    smd.__dict__['__file__'] = 'bin/smd'
    src = Path(__file__).resolve().parent.parent / 'bin' / 'smd'
    with mock.patch.dict(os.environ, {'SIGMOND_NO_VENV_REEXEC': '1'}):
        exec(compile(src.read_text(), str(src), 'exec'), smd.__dict__)
    return smd


def _args(smd, *argv):
    return smd._build_parser().parse_args(['admin', 'chirp', *argv])


def test_verb_parses_convert_key_and_dry_run():
    smd = _load_smd()
    a = _args(smd, 'convert', 'AC0G-B4', '--dry-run')
    assert (a.admin_command, a.chirp_command, a.key, a.dry_run) == \
        ('chirp', 'convert', 'AC0G-B4', True)
    assert _args(smd, 'convert').key is None


def test_verb_dry_run_changes_nothing(tmp_root, fake_run):
    _b4(tmp_root)
    smd = _load_smd()
    a = _args(smd, 'convert', '--dry-run')
    assert smd.cmd_chirp_convert(a, run=fake_run, root=tmp_root) == 0
    assert fake_run.calls == []
    assert 'chirp-recorder@AC0G-B4.service' in fake_run.output


def test_verb_converts(tmp_root, fake_run):
    _b4(tmp_root)
    smd = _load_smd()
    a = _args(smd, 'convert', 'AC0G/B4')
    assert smd.cmd_chirp_convert(a, run=fake_run, root=tmp_root) == 0
    assert state_path(tmp_root / 'etc', 'AC0G-B4').exists()


def test_verb_reports_a_capacity_refusal(tmp_root, fake_run):
    _b4(tmp_root)
    fake_run.validate_json = {"capacity": {"admitted": [], "refused": [
        ["4.4630MHz", "memory pressure 31.0 %"]], "estimated": False}}
    smd = _load_smd()
    assert smd.cmd_chirp_convert(_args(smd, 'convert'), run=fake_run,
                                 root=tmp_root) == 1
    assert "31.0" in fake_run.output



# -- round 1: root-safe state, stderr apart, full rollback --------------------

STATE_REL = "var/lib/sigmond/chirp-migrate"


def _topology(root):
    return root / "etc/sigmond/topology.toml"


def _chirp_enabled(root) -> bool:
    from sigmond.topology import load_topology
    return load_topology(_topology(root)).is_enabled("chirp-recorder")


def test_validate_stderr_stays_out_of_the_json(tmp_root, fake_run):          # I1
    _b4(tmp_root)
    assert apply_convert(tmp_root, run=fake_run) == 0
    out = fake_run.output
    assert "only grid_square" in out              # logged, not parsed


def test_a_symlink_at_the_part_name_is_never_followed(tmp_root, fake_run):   # C1
    _b4(tmp_root)
    d = tmp_root / STATE_REL
    d.mkdir(parents=True, mode=0o700)
    victim = tmp_root / "precious"
    victim.write_text("precious\n")
    (d / "AC0G-B4.json.part").symlink_to(victim)
    assert apply_convert(tmp_root, run=fake_run) == 0
    assert victim.read_text() == "precious\n"
    p = d / "AC0G-B4.json"
    assert p.is_file() and not p.is_symlink()
    assert json.loads(p.read_text())["key"] == "AC0G-B4"


def test_a_symlink_at_the_state_file_refuses(tmp_root, fake_run):           # C1
    _b4(tmp_root)
    d = tmp_root / STATE_REL
    d.mkdir(parents=True, mode=0o700)
    victim = tmp_root / "precious"
    victim.write_text(json.dumps({"key": "AC0G-B4"}))
    (d / "AC0G-B4.json").symlink_to(victim)
    targets, refused = convert_targets(tmp_root / "etc", run=fake_run)
    assert targets == [] and "AC0G-B4.json" in refused[0]
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert fake_run.calls == [] and "converted already" not in fake_run.output


def test_a_group_writable_state_file_refuses(tmp_root, fake_run):           # C1
    _b4(tmp_root)
    d = tmp_root / STATE_REL
    d.mkdir(parents=True, mode=0o700)
    (d / "AC0G-B4.json").write_text(json.dumps({"key": "AC0G-B4"}))
    os.chmod(d / "AC0G-B4.json", 0o664)
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert fake_run.calls == []


def test_a_group_writable_state_directory_refuses(tmp_root, fake_run):      # C1
    _b4(tmp_root)
    d = tmp_root / STATE_REL
    d.mkdir(parents=True)
    os.chmod(d, 0o770)
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert fake_run.calls == []
    assert not (d / "AC0G-B4.json").exists()


def test_a_symlinked_state_directory_refuses(tmp_root, fake_run):           # C1
    _b4(tmp_root)
    elsewhere = tmp_root / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (tmp_root / "var/lib/sigmond").mkdir(parents=True)
    (tmp_root / STATE_REL).symlink_to(elsewhere)
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert fake_run.calls == [] and list(elsewhere.iterdir()) == []


def test_a_failed_manifest_reverts_topology_and_enables_no_unit(tmp_root, fake_run):  # I2
    _b4(tmp_root)
    fake_run.fail.add(("smd", "admin"))
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert not (tmp_root / "etc/chirp-recorder/AC0G-B4.toml").exists()
    assert not _chirp_enabled(tmp_root)
    assert not any(c[0] == "systemctl" for c in fake_run.calls)
    out = fake_run.output
    assert "chirp-recorder disabled in topology again" in out
    assert "sink switch stays at fill" in out


def test_a_topology_flag_set_before_stays_set(tmp_root, fake_run):          # I2
    _b4(tmp_root)
    from sigmond.topology import set_component_enabled
    _topology(tmp_root).parent.mkdir(parents=True, exist_ok=True)
    set_component_enabled("chirp-recorder", True, _topology(tmp_root))
    fake_run.fail.add(("smd", "admin"))
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert _chirp_enabled(tmp_root)
    assert "disabled in topology again" not in fake_run.output


def test_a_converted_station_enables_chirp_in_topology(tmp_root, fake_run):
    _b4(tmp_root)
    assert apply_convert(tmp_root, run=fake_run) == 0
    assert _chirp_enabled(tmp_root)


def test_a_failed_start_rolls_back_and_a_second_run_finishes(tmp_root, fake_run):  # I3
    _b4(tmp_root)
    fake_run.fail.add(("systemctl", "enable"))
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert ["systemctl", "disable", "--now",
            "chirp-recorder@AC0G-B4.service"] in fake_run.calls
    assert not (tmp_root / "etc/chirp-recorder/AC0G-B4.toml").exists()
    assert not state_path(tmp_root / "etc", "AC0G-B4").exists()
    assert not _chirp_enabled(tmp_root)
    assert "disabled and stopped chirp-recorder@AC0G-B4.service" in fake_run.output
    fake_run.fail.clear()
    assert apply_convert(tmp_root, run=fake_run) == 0
    assert state_path(tmp_root / "etc", "AC0G-B4").exists()


def test_a_failed_state_write_rolls_back_and_a_second_run_finishes(tmp_root, fake_run):  # I3
    _b4(tmp_root)
    d = tmp_root / STATE_REL
    fake_run.after[("systemctl", "enable")] = lambda: os.chmod(d, 0o770)
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert ["systemctl", "disable", "--now",
            "chirp-recorder@AC0G-B4.service"] in fake_run.calls
    assert not (tmp_root / "etc/chirp-recorder/AC0G-B4.toml").exists()
    assert not (d / "AC0G-B4.json").exists()
    fake_run.after.clear()
    os.chmod(d, 0o700)
    assert apply_convert(tmp_root, run=fake_run) == 0
    assert state_path(tmp_root / "etc", "AC0G-B4").exists()


def test_a_failed_disable_keeps_the_config_and_says_so(tmp_root, fake_run):  # I3
    _b4(tmp_root)
    fake_run.fail.add(("systemctl", "enable"))
    fake_run.fail.add(("systemctl", "disable"))
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert (tmp_root / "etc/chirp-recorder/AC0G-B4.toml").exists()
    assert "kept" in fake_run.output


def test_config_removal_refuses_a_writable_directory(tmp_root, fake_run):   # M5
    _b4(tmp_root)
    d = tmp_root / "etc/chirp-recorder"
    fake_run.after[("chirp-recorder", "config", "init")] = lambda: os.chmod(d, 0o777)
    fake_run.validate_json = {"capacity": {"admitted": [], "refused": [
        ["4.4630MHz", "cpu pressure 22.0 %"]], "estimated": False}}
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert (d / "AC0G-B4.toml").exists()
    assert "remove it by hand" in fake_run.output


def test_a_broken_neighbour_does_not_block_the_named_receiver(tmp_root, fake_run):  # M1
    _b4(tmp_root)
    codar_instance(tmp_root, "AC0G-B41", "", "b41-status.local", centres=[4_463_000])
    assert apply_convert(tmp_root, key="AC0G-B4", run=fake_run) == 0
    assert apply_convert(tmp_root, key="AC0G-B41", run=fake_run) == 1


def test_a_station_wide_refusal_holds_for_a_named_receiver(tmp_root, fake_run):  # M1
    _b4(tmp_root)
    (tmp_root / "etc/codar-sounder/codar-sounder-config.toml").write_text(
        '[[radiod]]\nstatus = "b4-status.local"\n')
    assert apply_convert(tmp_root, key="AC0G-B4", run=fake_run) == 1
    assert fake_run.calls == []


def test_dry_run_numbers_steps_as_the_brief_does(tmp_root, fake_run):       # M2
    _b4(tmp_root)
    apply_convert(tmp_root, run=fake_run, dry_run=True)
    out = fake_run.output
    for n in ("1.", "2.", "3.", "4.", "5.", "6."):
        assert f"  {n} " in out
    assert "  7. " not in out


def test_a_held_lock_refuses(tmp_root, fake_run):                           # M3
    _b4(tmp_root)
    d = tmp_root / STATE_REL
    d.mkdir(parents=True, mode=0o700)
    fd = os.open(d / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert apply_convert(tmp_root, run=fake_run) == 1
        assert fake_run.calls == [] and "another" in fake_run.output
    finally:
        os.close(fd)


def test_a_radiod_only_host_refuses(tmp_root, fake_run):
    _b4(tmp_root)
    role = tmp_root / "etc/sigmond/station-role"
    role.parent.mkdir(parents=True, exist_ok=True)
    role.write_text("radiod-only\n")
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert fake_run.calls == []


def test_main_dispatches_admin_chirp_convert(monkeypatch):                  # M6
    import sys
    from sigmond import chirp_migrate as CM
    smd = _load_smd()
    seen = {}

    def fake(root, **kw):
        seen.update(kw, root=root)
        return 0
    monkeypatch.setattr(CM, "apply_convert", fake)
    monkeypatch.setattr(sys, "argv", ["smd", "admin", "chirp", "convert",
                                      "AC0G-B4", "--dry-run"])
    assert smd.main() == 0
    assert seen["key"] == "AC0G-B4" and seen["dry_run"] is True


# == Task 22: check and retire ==============================================

from datetime import datetime, timedelta, timezone

from sigmond import chirp_migrate as CM
from sigmond.chirp_migrate import (MigrateRefused, apply_retire, check,
                                   night_covered, retire_steps)

# 18:00Z; at longitude -90 local solar midnight falls at 06:00Z, so the
# first night (dated 2026-11-21) closes at 12:00Z.
STARTED = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
LON = -90.0
_ROOT: dict = {}


def STATE(key: str) -> Path:
    return _ROOT["root"] / STATE_REL / f"{key}.json"


def _converted(root: Path, key: str, rid: str, *, verdicts=None,
               started=STARTED) -> None:
    codar_instance(root, key, rid, f"{key.lower()}-status.local",
                   centres=[4_463_000])
    env = root / "etc/codar-sounder/env"
    env.mkdir(parents=True, exist_ok=True)
    (env / f"{key}.env").write_text("CODAR_X=1\n")
    out = root / "var/lib/codar-sounder"
    (out / "arrivals").mkdir(parents=True, exist_ok=True)
    cfg = root / "etc/codar-sounder" / f"{key}.toml"
    cfg.write_text(cfg.read_text() + f'[paths]\noutput_dir = "{out}"\n')
    clients = root / "etc/sigmond/clients"
    for d in (env.parent, env, clients):   # root's 0755, as installs leave them
        os.chmod(d, 0o755)
    ch = root / "etc/chirp-recorder"
    ch.mkdir(parents=True, exist_ok=True)
    (ch / f"{key}.toml").write_text(
        f'[station]\nreceiver_lat = 40.0\nreceiver_lon = {LON}\n')
    d = root / STATE_REL
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    doc = {"key": key, "reporter_id": rid, "radiod": "x",
           "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "codar_unit": f"codar-sounder@{key}.service",
           "channels": ["4.4630MHz"], "verdict": None, "verdicts": [],
           "retired_at": None}
    if verdicts:
        doc["verdicts"] = [
            {"night": (STARTED.date() + timedelta(days=1 + i)).isoformat(),
             "status": s, "rows": [], "covered": True}
            for i, s in enumerate(verdicts)]
    p = d / f"{key}.json"
    p.write_text(json.dumps(doc))
    os.chmod(p, 0o644)


@pytest.fixture
def chirp_root(tmp_root, monkeypatch):
    monkeypatch.setattr(CM, "ROOT", tmp_root)
    _ROOT["root"] = tmp_root
    return tmp_root


@pytest.fixture
def state_after_convert(chirp_root):
    _converted(chirp_root, "AC0G-B4", "AC0G/B4")
    return chirp_root


@pytest.fixture
def state_with_pass(chirp_root):
    _converted(chirp_root, "AC0G-B4", "AC0G/B4", verdicts=["pass"])
    return chirp_root


@pytest.fixture
def state_with_passes(chirp_root):
    def make(key, statuses):
        _converted(chirp_root, key, key.replace("-", "/", 1),
                   verdicts=statuses)
    return make


@pytest.fixture
def b2_states(chirp_root):
    _converted(chirp_root, "AC0G-B1A", "AC0G/B1A", verdicts=["pass"])
    _converted(chirp_root, "AC0G-B1B", "AC0G/B1B", verdicts=["insufficient"])
    return chirp_root


VERDICT = {
    "status": "pass", "match": "bin", "codar_bin_ns": {},
    "rows": [
        {"name": "matched", "value": 4, "limit": 3, "gating": True, "passed": True},
        {"name": "coverage", "value": 1.0, "limit": 0.9, "gating": True, "passed": True},
        {"name": "doppler", "value": 1.0, "limit": 0.9, "gating": True, "passed": True},
        {"name": "s4", "value": 0.05, "limit": None, "gating": False, "passed": True},
        {"name": "sigma_phi", "value": 0.01, "limit": None, "gating": False, "passed": True}],
    "matched": [[4_120_000, 4_121_500, -1500, "G"]],
    "explained": [[20_000_000, "rung 1"]],
    "codar_unmatched": [61_000_000], "chirp_only": [70_000_000]}


class CheckRun(FakeRun):
    """FakeRun that answers `chirp-recorder compare` with ``verdict`` and
    `hs-uploader sink status --json` with ``sink``."""

    def __init__(self, root, capsys):
        super().__init__(root, capsys)
        self.verdict = dict(VERDICT)
        self.compare_rc = 0
        self.sink = {"schema": 1, "clients": [
            {"client": "chirp-recorder", "file_state": "fill",
             "effective": "fill"}], "errors": []}

    def _answer(self, argv):
        if argv[:2] == ["chirp-recorder", "compare"]:
            if tuple(argv[:2]) in self.fail:
                return 2, "", "chirp-recorder: no sink to read"
            return (self.compare_rc if self.verdict.get("status") == "pass"
                    else 1), json.dumps(self.verdict), ""
        if argv[:3] == ["hs-uploader", "sink", "status"]:
            if tuple(argv[:3]) in self.fail:
                return 2, "", "hs-uploader: cannot read the manifest"
            return 0, json.dumps(self.sink), ""
        return super()._answer(argv)


@pytest.fixture
def check_run(tmp_root, capsys):
    return CheckRun(tmp_root, capsys)


# -- the brief's six -----------------------------------------------------------

def test_retire_refuses_without_a_passing_night(state_after_convert):
    with pytest.raises(MigrateRefused, match="no passing check"):
        retire_steps("AC0G-B4")


def test_check_waits_for_a_whole_night(state_after_convert, fake_run):
    out = check("AC0G-B4", run=fake_run, now=STARTED + timedelta(hours=8))
    assert out["status"] == "window" and "00:00-06:00" in out["message"]


def test_retire_order_after_pass(state_with_pass):
    argv = [list(s.argv) for s in retire_steps("AC0G-B4")]                # pre-flight C9
    assert argv[0] == ["systemctl", "disable", "--now", "codar-sounder@AC0G-B4.service"]
    assert argv[-2][:5] == ["hs-uploader", "sink", "set", "chirp-recorder", "upload"]


def test_seven_nights_needs_seven_passes(state_with_passes):
    state_with_passes("AC0G-B4", ["pass"] * 6)
    with pytest.raises(MigrateRefused, match="7"):
        retire_steps("AC0G-B4", nights=7)
    state_with_passes("AC0G-B4", ["pass"] * 7)
    assert retire_steps("AC0G-B4", nights=7)


def test_force_retires_untied_receiver_once_partner_passes(b2_states):
    # AC0G-B1A passed; AC0G-B1B reads "insufficient" (hf-timestd ties B1A's radiod)
    retire_steps("AC0G-B1B", force_reason="no T6 tie on B1B; partner B1A passed")
    st = json.loads(STATE("AC0G-B1B").read_text())
    assert st["forced"] == "no T6 tie on B1B; partner B1A passed" and st["forced_partner"] == "AC0G-B1A"


def test_force_refused_without_a_passing_partner(state_after_convert):
    with pytest.raises(MigrateRefused, match="partner"):
        retire_steps("AC0G-B4", force_reason="codar-sounder down since 11-18")


# -- night_covered -------------------------------------------------------------

def test_night_covered_needs_the_whole_local_night_and_12_hours():
    # local solar midnight at -90 = 06:00Z, so 00:00-06:00 local = 06-12Z
    assert not night_covered(STARTED, STARTED + timedelta(hours=17), LON)   # 11:00Z
    assert night_covered(STARTED, STARTED + timedelta(hours=18), LON)       # 12:00Z
    late = datetime(2026, 11, 21, 5, 0, tzinfo=timezone.utc)   # 23:00 local
    assert not night_covered(late, late + timedelta(hours=7), LON)          # 7 h only
    assert night_covered(late, late + timedelta(hours=12), LON)
    after = datetime(2026, 11, 21, 7, 0, tzinfo=timezone.utc)  # 01:00 local
    assert not night_covered(after, after + timedelta(hours=20), LON)
    assert night_covered(after, after + timedelta(hours=29), LON)          # next night
    # east of Greenwich: +135 -> local midnight at 15:00Z
    s = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    assert not night_covered(s, s + timedelta(hours=8, minutes=59), 135.0)
    assert night_covered(s, s + timedelta(hours=12), 135.0)


# -- check ---------------------------------------------------------------------

def test_check_runs_compare_over_the_night_and_stores_the_verdict(state_after_convert, check_run):
    out = check("AC0G-B4", run=check_run, now=STARTED + timedelta(hours=20))
    (c,) = [c for c in check_run.calls if c[:2] == ["chirp-recorder", "compare"]]
    assert c == ["chirp-recorder", "compare", "--instance", "AC0G-B4",
                 "--since", "2026-11-20T18:00:00Z",
                 "--until", "2026-11-21T14:00:00Z",
                 "--codar-rows", str(state_after_convert / "var/lib/codar-sounder/arrivals"),
                 "--json"]
    assert out["status"] == "pass" and out["night"] == "2026-11-21"
    st = json.loads(STATE("AC0G-B4").read_text())
    (v,) = st["verdicts"]
    assert v["night"] == "2026-11-21" and v["status"] == "pass"
    assert v["rows"] == VERDICT["rows"] and v["match"] == "bin"
    text = check_run.output
    for word in ("matched", "coverage", "doppler", "s4", "sigma_phi",
                 "gates", "reported", "explained", "unmatched", "61000000"):
        assert word in text, word


def test_check_tolerates_a_verdict_without_match(state_after_convert, check_run):
    del check_run.verdict["match"]
    check("AC0G-B4", run=check_run, now=STARTED + timedelta(hours=20))
    (v,) = json.loads(STATE("AC0G-B4").read_text())["verdicts"]
    assert "match" not in v and v["status"] == "pass"


def test_check_takes_mode_when_match_is_absent(state_after_convert, check_run):  # R1 I-1
    del check_run.verdict["match"]
    check_run.verdict["mode"] = "exact"
    check("AC0G-B4", run=check_run, now=STARTED + timedelta(hours=20))
    (v,) = json.loads(STATE("AC0G-B4").read_text())["verdicts"]
    assert v["match"] == "exact"


def test_check_keeps_one_verdict_per_night(state_after_convert, check_run):
    check_run.verdict["status"] = "fail"
    check("AC0G-B4", run=check_run, now=STARTED + timedelta(hours=19))
    check_run.verdict["status"] = "pass"
    check("AC0G-B4", run=check_run, now=STARTED + timedelta(hours=22))
    check("AC0G-B4", run=check_run, now=STARTED + timedelta(hours=44))
    vs = json.loads(STATE("AC0G-B4").read_text())["verdicts"]
    assert [(v["night"], v["status"]) for v in vs] == [
        ("2026-11-21", "pass"), ("2026-11-22", "pass")]


def test_check_stores_nothing_when_compare_breaks(state_after_convert, check_run):
    check_run.fail.add(("chirp-recorder", "compare"))
    out = check("AC0G-B4", run=check_run, now=STARTED + timedelta(hours=20))
    assert out["status"] == "error" and "no sink to read" in check_run.output
    assert json.loads(STATE("AC0G-B4").read_text())["verdicts"] == []


def test_check_refuses_an_unknown_status(state_after_convert, check_run):
    check_run.verdict["status"] = "maybe"
    out = check("AC0G-B4", run=check_run, now=STARTED + timedelta(hours=20))
    assert out["status"] == "error"
    assert json.loads(STATE("AC0G-B4").read_text())["verdicts"] == []


def test_check_refuses_an_unconverted_receiver(chirp_root, check_run):
    with pytest.raises(MigrateRefused, match="convert"):
        check("AC0G-B4", run=check_run, now=STARTED + timedelta(hours=20))


# -- retire_steps ----------------------------------------------------------------

def test_retire_steps_in_order(state_with_pass):
    steps = retire_steps("AC0G-B4")
    assert [s.phase for s in steps] == ["disable", "aside", "topology",
                                        "manifest", "sink", "state"]
    argv = [list(s.argv) for s in steps]
    assert argv[3] == ["smd", "admin", "uploader", "manifest", "--write"]
    assert argv[4] == ["hs-uploader", "sink", "set", "chirp-recorder", "upload",
                       "--reason", "codar-sounder retired",
                       "--by", "smd admin chirp retire"]


def test_a_failed_latest_night_refuses(state_with_passes):
    state_with_passes("AC0G-B4", ["pass", "fail"])
    with pytest.raises(MigrateRefused, match="no passing check"):
        retire_steps("AC0G-B4")


def test_passes_with_a_gap_are_not_consecutive(state_with_passes):
    state_with_passes("AC0G-B4", ["pass", "pass"])
    p = STATE("AC0G-B4")
    doc = json.loads(p.read_text())
    doc["verdicts"][0]["night"] = "2026-11-19"
    p.write_text(json.dumps(doc))
    with pytest.raises(MigrateRefused, match="2"):
        retire_steps("AC0G-B4", nights=2)


def test_force_refuses_when_own_check_did_not_read_insufficient(state_with_passes):
    state_with_passes("AC0G-B1A", ["pass"])
    state_with_passes("AC0G-B1B", ["fail"])
    with pytest.raises(MigrateRefused, match="insufficient"):
        retire_steps("AC0G-B1B", force_reason="x")


def test_force_needs_a_reason(b2_states):
    with pytest.raises(MigrateRefused, match="reason"):
        retire_steps("AC0G-B1B", force_reason="  ")


def test_force_partner_needs_as_many_nights(b2_states):
    with pytest.raises(MigrateRefused, match="partner"):
        retire_steps("AC0G-B1B", nights=7, force_reason="no T6 tie")


# -- apply_retire ----------------------------------------------------------------

def _topo_enable(root, name):
    from sigmond.topology import set_component_enabled
    _topology(root).parent.mkdir(parents=True, exist_ok=True)
    set_component_enabled(name, True, _topology(root))


def _enabled(root, name):
    from sigmond.topology import load_topology
    return load_topology(_topology(root)).is_enabled(name)


def test_retire_runs_every_step(state_with_pass, check_run):
    root = state_with_pass
    _topo_enable(root, "codar-sounder")
    assert apply_retire(root, key="AC0G-B4", run=check_run) == 0
    calls = check_run.calls
    assert calls[0] == ["hs-uploader", "sink", "status", "--json"]
    assert calls[1] == ["systemctl", "disable", "--now",
                        "codar-sounder@AC0G-B4.service"]
    assert ["smd", "admin", "uploader", "manifest", "--write"] in calls
    assert calls[-1][:5] == ["hs-uploader", "sink", "set", "chirp-recorder", "upload"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    etc = root / "etc/codar-sounder"
    assert not (etc / "AC0G-B4.toml").exists()
    assert (etc / f"AC0G-B4.toml.retired-{stamp}").exists()
    assert (etc / f"env/AC0G-B4.env.retired-{stamp}").exists()
    backups = list((root / "var/lib/sigmond/instance-rekey-backup").rglob("AC0G-B4.toml"))
    assert backups, "the /etc side is backed up first"
    assert not _enabled(root, "codar-sounder")
    st = json.loads(STATE("AC0G-B4").read_text())
    assert st["retired_at"].endswith("Z")
    assert st["sink_before_retire"]["file_state"] == "fill"


def test_retire_keeps_codar_in_topology_while_another_instance_remains(state_with_pass, check_run):
    root = state_with_pass
    codar_instance(root, "AC0G-B1A", "AC0G/B1A", "b1a-status.local", centres=[4_463_000])
    _topo_enable(root, "codar-sounder")
    assert apply_retire(root, key="AC0G-B4", run=check_run) == 0
    assert _enabled(root, "codar-sounder")
    assert "AC0G-B1A" in check_run.output


def test_retire_refuses_before_anything_when_sink_status_fails(state_with_pass, check_run):
    check_run.fail.add(("hs-uploader", "sink", "status"))
    assert apply_retire(state_with_pass, key="AC0G-B4", run=check_run) == 1
    assert not any(c[0] == "systemctl" for c in check_run.calls)
    assert (state_with_pass / "etc/codar-sounder/AC0G-B4.toml").exists()


def test_retire_refuses_a_switch_an_operator_set_off(state_with_pass, check_run):
    check_run.sink["clients"][0]["file_state"] = "off"
    assert apply_retire(state_with_pass, key="AC0G-B4", run=check_run) == 1
    assert not any(c[0] == "systemctl" for c in check_run.calls)
    assert "off" in check_run.output


def test_retire_dry_run_runs_and_writes_nothing(state_with_pass, check_run):
    before = STATE("AC0G-B4").read_text()
    assert apply_retire(state_with_pass, key="AC0G-B4", run=check_run,
                        dry_run=True) == 0
    assert check_run.calls == []
    assert STATE("AC0G-B4").read_text() == before
    out = check_run.output
    assert "codar-sounder@AC0G-B4.service" in out and "sink set chirp-recorder upload" in out


def test_retire_refusal_runs_nothing(state_after_convert, check_run):
    assert apply_retire(state_after_convert, key="AC0G-B4", run=check_run) == 1
    assert check_run.calls == [] and "no passing check" in check_run.output


def test_a_failed_step_stops_and_a_second_run_finishes(state_with_pass, check_run):
    root = state_with_pass
    check_run.fail.add(("smd", "admin"))
    assert apply_retire(root, key="AC0G-B4", run=check_run) == 1
    assert not any(c[:3] == ["hs-uploader", "sink", "set"] for c in check_run.calls)
    assert json.loads(STATE("AC0G-B4").read_text())["retired_at"] is None
    assert "again" in check_run.output
    check_run.fail.clear()
    check_run.sink["clients"][0]["file_state"] = "upload"   # must not overwrite
    assert apply_retire(root, key="AC0G-B4", run=check_run) == 0
    st = json.loads(STATE("AC0G-B4").read_text())
    assert st["retired_at"] and st["sink_before_retire"]["file_state"] == "fill"


def test_a_retired_receiver_retires_once(state_with_pass, check_run):
    assert apply_retire(state_with_pass, key="AC0G-B4", run=check_run) == 0
    n = len(check_run.calls)
    assert apply_retire(state_with_pass, key="AC0G-B4", run=check_run) == 0
    assert len(check_run.calls) == n and "retired already" in check_run.output


def test_retire_refuses_a_writable_codar_directory(state_with_pass, check_run):
    os.chmod(state_with_pass / "etc/codar-sounder", 0o777)
    assert apply_retire(state_with_pass, key="AC0G-B4", run=check_run) == 1
    assert (state_with_pass / "etc/codar-sounder/AC0G-B4.toml").exists()
    assert "by hand" in check_run.output


def test_retire_holds_the_chirp_lock(state_with_pass, check_run):
    d = state_with_pass / STATE_REL
    fd = os.open(d / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert apply_retire(state_with_pass, key="AC0G-B4", run=check_run) == 1
        assert check_run.calls == []
    finally:
        os.close(fd)


# -- Task 21's deferred minors -------------------------------------------------

def test_rollback_names_the_command_that_lifts_fill(tmp_root, fake_run):    # R4/M4
    _b4(tmp_root)
    fake_run.fail.add(("smd", "admin"))
    assert apply_convert(tmp_root, run=fake_run) == 1
    out = fake_run.output
    assert "smd admin chirp retire" in out
    assert "hs-uploader sink set chirp-recorder upload" in out


def test_non_root_dry_run_reads_what_it_can(tmp_root, fake_run):            # R1
    _b4(tmp_root)
    d = tmp_root / STATE_REL
    d.mkdir(parents=True, mode=0o700)
    os.chmod(d, 0o000)                 # as root's 0700 looks to anyone else
    try:
        rc = apply_convert(tmp_root, run=fake_run, dry_run=True)
    finally:
        os.chmod(d, 0o700)
    out = fake_run.output
    assert rc == 0 and fake_run.calls == []
    assert "not a plain directory" not in out
    assert "as root" in out and "chirp-recorder@AC0G-B4.service" in out


def test_non_root_retire_dry_run_says_why(state_with_pass, check_run):      # R1
    d = state_with_pass / STATE_REL
    os.chmod(d, 0o000)
    try:
        rc = apply_retire(state_with_pass, key="AC0G-B4", run=check_run,
                          dry_run=True)
    finally:
        os.chmod(d, 0o700)
    out = check_run.output
    assert rc == 1 and "not a plain directory" not in out and "as root" in out


# -- the verbs -------------------------------------------------------------------

def test_verb_parses_check_and_retire():
    smd = _load_smd()
    a = _args(smd, 'check', 'AC0G-B4')
    assert (a.chirp_command, a.key) == ('check', 'AC0G-B4')
    a = _args(smd, 'retire', 'AC0G-B1B', '--nights', '7', '--force',
              '--reason', 'no T6 tie', '--dry-run')
    assert (a.chirp_command, a.key, a.nights, a.force, a.reason, a.dry_run) == \
        ('retire', 'AC0G-B1B', 7, True, 'no T6 tie', True)
    assert _args(smd, 'retire', 'AC0G-B4').nights == 1


def test_verb_force_without_reason_refuses(state_with_pass, check_run):
    smd = _load_smd()
    a = _args(smd, 'retire', 'AC0G-B4', '--force')
    assert smd.cmd_chirp_retire(a, run=check_run, root=state_with_pass) == 1
    assert check_run.calls == []


@pytest.mark.parametrize("argv,fn", [
    (["convert", "AC0G-B4"], "apply_convert"),
    (["check", "AC0G-B4"], "check"),
    (["retire", "AC0G-B4"], "apply_retire")])
def test_each_verb_holds_the_lifecycle_lock(monkeypatch, argv, fn):        # ruling 4
    import contextlib
    import sys
    smd = _load_smd()
    held = []

    @contextlib.contextmanager
    def fake_lock(reason="", *, wait_s=None, **kw):
        held.append((reason, wait_s))
        yield
        held.append("released")

    def fake(*a, **kw):
        assert held and held[-1] != "released", "called outside the lock"
        return {"status": "pass"} if fn == "check" else 0
    monkeypatch.setattr(smd, "lifecycle_lock", fake_lock)
    monkeypatch.setattr(smd, "_need_root", lambda *a: False)
    monkeypatch.setattr(CM, fn, fake)
    monkeypatch.setattr(sys, "argv", ["smd", "admin", "chirp", *argv])
    assert smd.main() == 0
    assert held[0] == (f"chirp {argv[0]}", 0) and held[-1] == "released"


def test_no_child_command_takes_the_lifecycle_lock(state_with_pass, check_run):  # ruling 4
    """The only smd child is `admin uploader manifest --write`, which main()
    dispatches outside the lifecycle-locked set and which takes hs-uploader's
    install lock alone."""
    root = state_with_pass
    check("AC0G-B4", run=check_run, now=STARTED + timedelta(hours=20))
    apply_retire(root, key="AC0G-B4", run=check_run)
    smd_children = {tuple(c) for c in check_run.calls if c[0] == "smd"}
    assert smd_children == {("smd", "admin", "uploader", "manifest", "--write")}
    assert {c[0] for c in check_run.calls} <= {"smd", "systemctl",
                                               "hs-uploader", "chirp-recorder"}
    src = (Path(__file__).resolve().parent.parent / "lib/sigmond/commands/uploader.py").read_text()
    assert "lifecycle_lock" not in src
    smd_src = (Path(__file__).resolve().parent.parent / "bin/smd").read_text()
    assert "_MUTATING = {'install', 'apply', 'start', 'stop', 'restart', 'reload'}" in smd_src


# == Task 22, fix round 1 ======================================================

def test_check_stores_compares_real_match_field(state_after_convert, check_run):  # I-1
    assert "mode" not in check_run.verdict          # what compare emits
    check("AC0G-B4", run=check_run, now=STARTED + timedelta(hours=20))
    (v,) = json.loads(STATE("AC0G-B4").read_text())["verdicts"]
    assert v["match"] == "bin" and "mode" not in v


def test_check_reads_codar_rows_from_the_default_output_dir(state_after_convert):  # I-3
    root = state_after_convert
    cfg = root / "etc/codar-sounder/AC0G-B4.toml"
    cfg.write_text(cfg.read_text().split("[paths]")[0])
    assert CM.codar_rows_dir(root / "etc", "AC0G-B4") == \
        root / "var/lib/codar-sounder/arrivals"


def test_check_without_codar_arrivals_reads_insufficient(state_after_convert, check_run):  # I-3
    import shutil
    shutil.rmtree(state_after_convert / "var/lib/codar-sounder/arrivals")
    out = check("AC0G-B4", run=check_run, now=STARTED + timedelta(hours=20))
    assert out["status"] == "insufficient"
    assert "bf3f360" in out["message"] and "arrivals" in out["message"]
    assert not any(c[:2] == ["chirp-recorder", "compare"] for c in check_run.calls)
    assert json.loads(STATE("AC0G-B4").read_text())["verdicts"] == []


def test_a_check_cannot_strand_a_half_retired_receiver(state_with_pass, check_run):  # I-2
    root = state_with_pass
    check_run.fail.add(("smd", "admin"))                       # stops at the manifest
    assert apply_retire(root, key="AC0G-B4", run=check_run) == 1
    with pytest.raises(MigrateRefused, match="retire.*again"):
        check("AC0G-B4", run=check_run, now=STARTED + timedelta(hours=44))
    # even an insufficient night recorded meanwhile does not block the resume
    p = STATE("AC0G-B4")
    doc = json.loads(p.read_text())
    doc["verdicts"].append({"night": "2026-11-22", "status": "insufficient",
                            "rows": [], "covered": True})
    p.write_text(json.dumps(doc))
    check_run.fail.clear()
    assert apply_retire(root, key="AC0G-B4", run=check_run) == 0
    assert json.loads(p.read_text())["retired_at"]


def test_a_forced_record_waits_for_the_preflight(b2_states, check_run):  # M1
    check_run.fail.add(("hs-uploader", "sink", "status"))
    assert apply_retire(b2_states, key="AC0G-B1B", run=check_run,
                        force_reason="no T6 tie on B1B") == 1
    st = json.loads(STATE("AC0G-B1B").read_text())
    assert "forced" not in st and "forced_partner" not in st
    assert "nothing changed" in check_run.output
    check_run.fail.clear()
    assert apply_retire(b2_states, key="AC0G-B1B", run=check_run,
                        force_reason="no T6 tie on B1B") == 0
    st = json.loads(STATE("AC0G-B1B").read_text())
    assert st["forced"] == "no T6 tie on B1B" and st["forced_partner"] == "AC0G-B1A"


def test_aside_refusals_come_before_codar_stops(state_with_pass, check_run):  # M2
    os.chmod(state_with_pass / "etc/codar-sounder", 0o777)
    assert apply_retire(state_with_pass, key="AC0G-B4", run=check_run) == 1
    assert not any(c[0] == "systemctl" for c in check_run.calls)
    assert "by hand" in check_run.output


def test_a_stale_retired_file_refuses_before_codar_stops(state_with_pass, check_run):  # M2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    (state_with_pass / f"etc/codar-sounder/AC0G-B4.toml.retired-{stamp}").write_text("old\n")
    assert apply_retire(state_with_pass, key="AC0G-B4", run=check_run) == 1
    assert not any(c[0] == "systemctl" for c in check_run.calls)


def test_a_sink_without_a_chirp_switch_refuses_first(state_with_pass, check_run):  # M3
    check_run.sink["clients"] = []
    assert apply_retire(state_with_pass, key="AC0G-B4", run=check_run) == 1
    assert not any(c[0] == "systemctl" for c in check_run.calls)
    assert "no chirp-recorder" in check_run.output


def test_retire_moves_the_sources_file_aside(state_with_pass, check_run):  # M4
    root = state_with_pass
    assert apply_retire(root, key="AC0G-B4", run=check_run) == 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    src = root / "etc/sigmond/clients"
    assert not (src / "codar-sounder@AC0G-B4.sources.toml").exists()
    assert (src / f"codar-sounder@AC0G-B4.sources.toml.retired-{stamp}").exists()
    assert "drop-in" in check_run.output


def test_a_state_refusal_in_the_last_step_stops_truthfully(state_with_pass, check_run):  # M5
    p = STATE("AC0G-B4")
    def loosen():
        os.chmod(p, 0o666)
    check_run.after[("hs-uploader", "sink", "set")] = loosen
    assert apply_retire(state_with_pass, key="AC0G-B4", run=check_run) == 1
    out = check_run.output
    assert "nothing changed" not in out and "again" in out


def test_a_resume_on_another_day_keeps_the_first_stamp(state_with_pass, check_run):  # M7
    root = state_with_pass
    check_run.fail.add(("systemctl", "disable"))
    assert apply_retire(root, key="AC0G-B4", run=check_run) == 1
    p = STATE("AC0G-B4")
    doc = json.loads(p.read_text())
    assert doc["retire_stamp"] == datetime.now(timezone.utc).strftime("%Y%m%d")
    doc["retire_stamp"] = "20261101"                  # as if begun on 11-01
    p.write_text(json.dumps(doc))
    check_run.fail.clear()
    assert apply_retire(root, key="AC0G-B4", run=check_run) == 0
    etc = root / "etc/codar-sounder"
    assert (etc / "AC0G-B4.toml.retired-20261101").exists()
    assert (etc / "env/AC0G-B4.env.retired-20261101").exists()


def test_check_a_named_night(state_after_convert, check_run):  # M6
    now = STARTED + timedelta(hours=68)               # 14:00Z 11-23
    out = check("AC0G-B4", run=check_run, now=now, night="2026-11-21")
    assert out["night"] == "2026-11-21"
    (c,) = [c for c in check_run.calls if c[:2] == ["chirp-recorder", "compare"]]
    assert c[c.index("--since") + 1] == "2026-11-20T18:00:00Z"
    assert c[c.index("--until") + 1] == "2026-11-21T18:00:00Z"
    with pytest.raises(MigrateRefused, match="2026-11-20"):
        check("AC0G-B4", run=check_run, now=now, night="2026-11-20")   # before start
    with pytest.raises(MigrateRefused, match="2026-11-24"):
        check("AC0G-B4", run=check_run, now=now, night="2026-11-24")   # not closed


def test_verb_parses_check_night():  # M6
    smd = _load_smd()
    assert _args(smd, 'check', 'AC0G-B4', '--night', '2026-11-21').night == '2026-11-21'
    assert _args(smd, 'check', 'AC0G-B4').night is None
