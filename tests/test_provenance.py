"""What is actually installed here — computed, never remembered.

`/etc/sigmond-appliance/version` is written once by firstboot and copied
into the VM by the wizard, and NOTHING updates it afterwards.  On
2026-08-15 that made DASI002 assert `v3.20` while running radiod
cd44bbdd, ka9q-python 3.24.0 and hf-timestd 55e8797 — all v3.31-era
components installed in place that morning.  The file was not wrong when
written; it went stale, silently, and there was no way to tell from the
box.

In-place update is the normal path for the DASI fleet, so any single
stored version string will drift the same way.  The fix is therefore not
a better stamp: it is to COMPUTE component state from the checkouts on
every call, and to keep the image string only for what it honestly
is — the lineage that laid this box down, not a claim about the present.
"""
import json

import pytest

from sigmond.provenance import (
    image_lineage, component_versions, record_update, update_history,
    format_report,
)


def test_the_image_string_is_labelled_lineage_not_current_version():
    """DASI002 says v3.20 and is not a v3.20 box.  The word 'version'
    invites exactly that misreading, so the report must not use it for
    this field."""
    lineage = image_lineage(_version_file('v3.20'))

    assert lineage['image'] == 'v3.20'
    assert 'installed' in lineage['means'].lower()
    assert 'current' not in lineage['means'].lower()


def test_a_box_with_no_stamp_is_reported_not_failed():
    """Units older than the stamp exist in the field.  A diagnostic that
    dies on them is useless precisely where it is needed."""
    lineage = image_lineage('/nonexistent/version')

    assert lineage['image'] is None
    assert lineage['means']


def test_component_versions_are_read_live_so_they_cannot_go_stale():
    seen = {}

    def fake_git(path, *args):
        seen[path] = args
        return 'cd44bbdd76aa042'

    vers = component_versions(['/opt/git/hf-timestd'], git=fake_git)

    assert vers['hf-timestd'] == 'cd44bbdd76aa042'
    assert seen                      # it asked git, it did not read a cache


def test_a_component_moved_past_the_image_is_visible_in_the_report():
    """The DASI002 case end to end: the report must make the divergence
    legible rather than hiding it behind a single number."""
    report = format_report(
        lineage={'image': 'v3.20', 'means': 'image installed on this host'},
        components={'hf-timestd': '55e8797', 'ka9q-python': '8b847b8'},
        history=[{'at': '2026-08-15T14:30:00Z', 'what': 'smd update --apply'}])

    assert 'v3.20' in report
    assert '55e8797' in report
    assert '2026-08-15' in report        # updated since install, and when


def test_update_history_is_append_only(tmp_path):
    """A record that overwrites answers 'what happened last', which is
    the question that already had a stale answer."""
    p = tmp_path / 'history.jsonl'
    record_update(p, {'at': '2026-08-15T14:30:00Z', 'what': 'first'})
    record_update(p, {'at': '2026-08-15T18:00:00Z', 'what': 'second'})

    hist = update_history(p)
    assert [h['what'] for h in hist] == ['first', 'second']


def test_history_survives_a_corrupt_line(tmp_path):
    """A truncated write during a power cut must not blind the tool."""
    p = tmp_path / 'history.jsonl'
    record_update(p, {'at': 'a', 'what': 'good'})
    p.write_text(p.read_text() + '{"at": "b", "wh\n')
    record_update(p, {'at': 'c', 'what': 'later'})

    assert [h['what'] for h in update_history(p)] == ['good', 'later']


def test_no_history_is_not_an_error(tmp_path):
    assert update_history(tmp_path / 'absent.jsonl') == []


def _version_file(text):
    import tempfile, os
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, 'w') as fh:
        fh.write(text + '\n')
    return path
