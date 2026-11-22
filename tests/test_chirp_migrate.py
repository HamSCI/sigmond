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

STATE = "var/lib/sigmond/chirp-migrate"


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
    d = tmp_root / STATE
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
    d = tmp_root / STATE
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
    d = tmp_root / STATE
    d.mkdir(parents=True, mode=0o700)
    (d / "AC0G-B4.json").write_text(json.dumps({"key": "AC0G-B4"}))
    os.chmod(d / "AC0G-B4.json", 0o664)
    assert apply_convert(tmp_root, run=fake_run) == 1
    assert fake_run.calls == []


def test_a_group_writable_state_directory_refuses(tmp_root, fake_run):      # C1
    _b4(tmp_root)
    d = tmp_root / STATE
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
    (tmp_root / STATE).symlink_to(elsewhere)
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
    d = tmp_root / STATE
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
    d = tmp_root / STATE
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
