"""The drop-in that keeps a recorder from starting before the LAN exists.

The failure it prevents leaves no trace: every unit reports active, the
recorder joins a multicast group that reaches nobody, and the station records
nothing while looking healthy.  So these tests pin the two properties that
make the drop-in safe to ship fleet-wide -- it must be inert on an old radiod,
and it must not pull lan.target in on a host with no radiod at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))

from sigmond.lan_order import (  # noqa: E402
    CONTENT, DROP_IN_NAME, drop_in_path, ensure_lan_ordering,
)

FORBIDDEN = ('Wants=', 'Requires=', 'BindsTo=', 'PartOf=')


def directives(text):
    """Only the real directives -- comments explaining WHY we avoid Wants=
    must not read as issuing it."""
    return [l.strip() for l in text.splitlines()
            if l.strip() and not l.strip().startswith('#')]


class TestWhatItWrites:

    def test_writes_the_drop_in(self, tmp_path):
        msgs = ensure_lan_ordering(['timestd-core-recorder.service'], root=tmp_path)
        p = tmp_path / 'timestd-core-recorder.service.d' / DROP_IN_NAME
        assert p.exists()
        assert 'After=lan.target' in directives(p.read_text())
        assert len(msgs) == 1

    def test_orders_only_never_requires(self, tmp_path):
        """A Wants= or Requires= here would start wait-for-lan on a
        decode-only host that has no radiod and nothing to wait for."""
        ensure_lan_ordering(['psk-recorder@AC0G-B4.service'], root=tmp_path)
        d = directives((tmp_path / 'psk-recorder@AC0G-B4.service.d'
                        / DROP_IN_NAME).read_text())
        assert 'After=lan.target' in d
        for bad in FORBIDDEN:
            assert not any(x.startswith(bad) for x in d), bad

    def test_instance_path_not_template_path(self, tmp_path):
        """Removing one station's instance takes its drop-in with it."""
        p = drop_in_path('wspr-recorder@AC0G-B4.service', root=tmp_path)
        assert p.parent.name == 'wspr-recorder@AC0G-B4.service.d'
        assert '@.service.d' not in str(p)


class TestIdempotence:

    def test_second_call_changes_nothing(self, tmp_path):
        u = ['timestd-fusion.service']
        assert len(ensure_lan_ordering(u, root=tmp_path)) == 1
        assert ensure_lan_ordering(u, root=tmp_path) == []

    def test_rewrites_a_drop_in_someone_edited(self, tmp_path):
        u = ['timestd-fusion.service']
        ensure_lan_ordering(u, root=tmp_path)
        p = drop_in_path('timestd-fusion.service', root=tmp_path)
        p.write_text('[Unit]\n# someone edited this\n')
        assert len(ensure_lan_ordering(u, root=tmp_path)) == 1
        assert p.read_text() == CONTENT


class TestItRefusesNonsense:

    @pytest.mark.parametrize('bad', ['', 'not-a-unit', 'radiod@AC0G-B4', 'x.conf'])
    def test_skips_things_that_are_not_units(self, bad, tmp_path):
        assert ensure_lan_ordering([bad], root=tmp_path) == []
        assert not list(tmp_path.glob('*'))

    def test_accepts_service_target_and_timer(self, tmp_path):
        assert len(ensure_lan_ordering(
            ['a.service', 'b.target', 'c.timer'], root=tmp_path)) == 3

    def test_dry_run_writes_nothing(self, tmp_path):
        msgs = ensure_lan_ordering(['timestd-vtec.service'],
                                   dry_run=True, root=tmp_path)
        assert len(msgs) == 1 and 'would' in msgs[0]
        assert not list(tmp_path.glob('*'))

    def test_an_unwritable_root_reports_rather_than_raises(self, tmp_path):
        """Install must not die because one drop-in could not be written."""
        (tmp_path / 'timestd-fusion.service.d').write_text('a file, not a dir')
        msgs = ensure_lan_ordering(['timestd-fusion.service'], root=tmp_path)
        assert len(msgs) == 1 and 'warning' in msgs[0]
