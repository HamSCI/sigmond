"""Tests for sigmond.fleet — the fleet inventory.

The inventory answers "what hosts are there, and how do I reach each
one?"  It is deliberately dumb about credentials: ``reach`` and ``hop``
are opaque ssh destinations, and which key reaches them is the
operator's ssh config (see hamsci-ops docs/fleet-ssh-access.md).

A silently-skipped host is worse than an error — it would be invisible
in every later fan-out — so malformed input fails loudly and names the
thing it could not parse.
"""

import pytest

from sigmond.fleet import DEFAULT_FLEET_PATH, Host, load_fleet


class TestLoadFleet:

    def test_load_fleet_reads_hosts_and_freeze(self, tmp_path):
        p = tmp_path / 'fleet.toml'
        p.write_text(
            '[host.alpha]\n'
            'reach   = "root@alpha.example"\n'
            'profile = "dasi2"\n'
            'role    = "field"\n'
            '\n'
            '[host.beta]\n'
            'reach   = "-J jump.example -p 2222 root@10.0.0.9"\n'
            'profile = "dasi2"\n'
            'role    = "field"\n'
            'frozen  = "capture window through 2026-08-20T00:00Z"\n'
        )
        fleet = load_fleet(str(p))
        assert set(fleet) == {'alpha', 'beta'}
        assert fleet['beta'].frozen.startswith('capture window')
        assert fleet['alpha'].frozen is None

    def test_reach_is_opaque(self, tmp_path):
        """The loader records WHERE, and never parses it into parts.

        A reach string carrying jump, port and user must survive
        verbatim — the ssh client parses it, not us.
        """
        p = tmp_path / 'fleet.toml'
        p.write_text(
            '[host.beta]\n'
            'reach = "-J jump.example -p 2222 root@10.0.0.9"\n'
        )
        fleet = load_fleet(str(p))
        assert fleet['beta'].reach == '-J jump.example -p 2222 root@10.0.0.9'

    def test_hop_is_optional_and_opaque(self, tmp_path):
        """``hop`` carries the inner destination of a nested ssh.

        Some sigmond hosts are guests whose credential lives on their
        hypervisor, not on the devbox — the outer host runs the inner
        ssh.  A host with no hop reaches its target directly.
        """
        p = tmp_path / 'fleet.toml'
        p.write_text(
            '[host.guest]\n'
            'reach = "hypervisor.example"\n'
            'hop   = "sigmond@10.0.0.5"\n'
            '\n'
            '[host.direct]\n'
            'reach = "server.example"\n'
        )
        fleet = load_fleet(str(p))
        assert fleet['guest'].hop == 'sigmond@10.0.0.5'
        assert fleet['direct'].hop is None

    def test_optional_fields_default_rather_than_raise(self, tmp_path):
        """Only ``reach`` is mandatory; the rest describe, not locate."""
        p = tmp_path / 'fleet.toml'
        p.write_text(
            '[host.spare]\n'
            'reach = "spare.example"\n'
        )
        fleet = load_fleet(str(p))
        host = fleet['spare']
        assert host.name == 'spare'
        assert host.profile is None
        assert host.role is None
        assert host.frozen is None


class TestMissingInventory:

    def test_absent_default_inventory_is_an_empty_fleet(self, tmp_path,
                                                        monkeypatch):
        """No inventory is a legitimate state, not a crash.

        Every host that is not the devbox has no fleet inventory, and
        `smd` must keep working there.
        """
        monkeypatch.delenv('SIGMOND_FLEET', raising=False)
        monkeypatch.setattr('sigmond.fleet.DEFAULT_FLEET_PATH',
                            tmp_path / 'absent' / 'fleet.toml')
        assert load_fleet() == {}

    def test_explicit_missing_path_fails_loudly(self, tmp_path):
        """An operator who names a file wants that file.

        Returning an empty fleet here would report "no hosts" for a
        typo'd --inventory, which reads exactly like a healthy fleet
        with nothing in it.
        """
        missing = tmp_path / 'nope.toml'
        with pytest.raises(FileNotFoundError) as exc:
            load_fleet(str(missing))
        assert 'nope.toml' in str(exc.value)


class TestMalformedInventory:

    def test_malformed_toml_names_the_file(self, tmp_path):
        p = tmp_path / 'broken.toml'
        p.write_text('[host.alpha\nreach = "x"\n')
        with pytest.raises(ValueError) as exc:
            load_fleet(str(p))
        assert 'broken.toml' in str(exc.value)

    def test_host_without_reach_names_the_host(self, tmp_path):
        """Skipping it silently would hide the host from every fan-out."""
        p = tmp_path / 'fleet.toml'
        p.write_text(
            '[host.alpha]\n'
            'reach = "root@alpha.example"\n'
            '\n'
            '[host.beta]\n'
            'profile = "dasi2"\n'
        )
        with pytest.raises(ValueError) as exc:
            load_fleet(str(p))
        assert 'beta' in str(exc.value)

    def test_host_block_that_is_not_a_table_names_the_host(self, tmp_path):
        p = tmp_path / 'fleet.toml'
        p.write_text('[host]\nalpha = "root@alpha.example"\n')
        with pytest.raises(ValueError) as exc:
            load_fleet(str(p))
        assert 'alpha' in str(exc.value)

    def test_empty_inventory_file_is_an_empty_fleet(self, tmp_path):
        """A file with no [host.*] blocks is empty, not malformed."""
        p = tmp_path / 'fleet.toml'
        p.write_text('# nothing here yet\n')
        assert load_fleet(str(p)) == {}


class TestPrecedence:

    def test_env_override_beats_the_default_path(self, tmp_path, monkeypatch):
        """$SIGMOND_FLEET lets a non-install host carry an inventory.

        The devbox has no /etc/sigmond at all, so the default path can
        never resolve there.
        """
        env_file = tmp_path / 'from-env.toml'
        env_file.write_text('[host.fromenv]\nreach = "env.example"\n')
        default_file = tmp_path / 'from-etc.toml'
        default_file.write_text('[host.fromdefault]\nreach = "etc.example"\n')

        monkeypatch.setenv('SIGMOND_FLEET', str(env_file))
        monkeypatch.setattr('sigmond.fleet.DEFAULT_FLEET_PATH', default_file)

        assert set(load_fleet()) == {'fromenv'}

    def test_default_path_is_the_etc_operator_file(self):
        """The documented default, matching catalog.toml's /etc layer."""
        assert DEFAULT_FLEET_PATH.as_posix() == '/etc/sigmond/fleet.toml'


class TestShippedExample:

    def test_example_inventory_parses_and_uses_placeholders_only(self):
        """etc/fleet.toml.example ships in a PUBLIC repo.

        It must parse (it is the schema documentation) and must not
        leak a single real hostname, IP, port or username.
        """
        from pathlib import Path
        example = (Path(__file__).resolve().parent.parent
                   / 'etc' / 'fleet.toml.example')
        fleet = load_fleet(str(example))
        assert fleet, 'the example must define at least one host'

        blob = example.read_text()
        for leak in ('192.168.1.', '10.112.0.', 'wd30', 'b4pm', 'dasi002',
                     'ac0g', 'wsprdaemon', 'hamsci.org'):
            assert leak not in blob.lower(), f'real fleet data in example: {leak}'


class TestHost:

    def test_frozen_host_is_still_a_host(self):
        """Freeze prevents changes, not visibility."""
        host = Host(name='alpha', reach='root@alpha.example',
                    frozen='capture window')
        assert host.frozen == 'capture window'
        assert host.reach == 'root@alpha.example'
