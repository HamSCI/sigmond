"""Bring-up must know whether it has wisdom for THIS CPU, and say so.

An FFTW plan is chosen by timing candidate algorithms, so it is only
optimal on the machine it was measured on.  The repo carries pre-planned
bundles keyed by CPU model; a host that matches gets seeded in a second,
a host that does not plans from scratch for HOURS.

The fault this guards: `_seed_bundled_wisdom()` was written, tested by
nobody, and left UNREACHABLE -- its only caller `_ensure_fft_wisdom()` is
never invoked -- so no station was ever seeded and every bring-up planned
from scratch in silence.  A miss must be loud; that is the whole point.

rob, 2026-10-02: "we need to identify the CPU and identify during bring up
if we do not have a wisdom for the CPU that we're installing on."
"""
import pytest

from sigmond.wisdom import cpu_slug, read_sidecar, find_bundle, identify_cpu

CXC = 'AMD Ryzen 5 5560U with Radeon Graphics'
B4 = 'AMD Ryzen 7 5825U with Radeon Graphics'


def _bundle(d, slug, sidecar_text, content='(fftwf_codelet_x)'):
    (d / f'wisdomf-{slug}').write_text(content)
    (d / f'wisdomf-{slug}.cpu').write_text(sidecar_text)


def test_slug_is_stable_and_filesystem_safe():
    assert cpu_slug(CXC) == 'amd-ryzen-5-5560u'
    assert cpu_slug(B4) == 'amd-ryzen-7-5825u'
    # The two parts that caused a whole day of confusion must not collide.
    assert cpu_slug(CXC) != cpu_slug(B4)


def test_slug_survives_intel_decoration():
    slug = cpu_slug('Intel(R) Core(TM) i7-8700 CPU @ 3.20GHz')
    assert slug and '(' not in slug and ' ' not in slug


def test_sidecar_v1_bare_model_line_still_parses():
    """The first bundle shipped a bare model name; it must keep working."""
    assert read_sidecar_text(B4 + '\n') == {'model': B4}


def read_sidecar_text(text, tmp=None):
    import tempfile, pathlib
    p = pathlib.Path(tempfile.mkdtemp()) / 'x.cpu'
    p.write_text(text)
    return read_sidecar(p)


def test_sidecar_v2_records_what_makes_a_plan_valid():
    got = read_sidecar_text(
        f'model={CXC}\nl3_bytes=8388608\nradiod_l3_bytes=5242880\n'
        'samprate=129600000\n# a comment\n\n')
    assert got['model'] == CXC
    assert got['l3_bytes'] == '8388608'
    assert got['radiod_l3_bytes'] == '5242880'
    assert 'a comment' not in str(got)


def test_exact_cpu_match_is_found(tmp_path):
    _bundle(tmp_path, 'amd-ryzen-5-5560u', f'model={CXC}\n')

    assert find_bundle([tmp_path], CXC).name == 'wisdomf-amd-ryzen-5-5560u'


def test_a_different_cpu_is_NOT_matched(tmp_path):
    """The null.  Wisdom from another CPU still *works* -- FFTW re-plans
    anything missing -- which is exactly why a loose match would be
    invisible: you would ship plans timed on the wrong machine and never
    see an error."""
    _bundle(tmp_path, 'amd-ryzen-7-5825u', f'model={B4}\n')

    assert find_bundle([tmp_path], CXC) is None


def test_empty_bundle_file_does_not_count(tmp_path):
    _bundle(tmp_path, 'amd-ryzen-5-5560u', f'model={CXC}\n', content='')

    assert find_bundle([tmp_path], CXC) is None


def test_a_miss_reports_the_cpu_and_what_IS_available(tmp_path):
    """A miss must carry enough to act on: which CPU this is, the slug the
    bundle would need, and what the repo does have."""
    _bundle(tmp_path, 'amd-ryzen-7-5825u', f'model={B4}\n')
    cpuinfo = tmp_path / 'cpuinfo'
    cpuinfo.write_text(f'processor\t: 0\nmodel name\t: {CXC}\n')

    v = identify_cpu([tmp_path], cpuinfo)

    assert v['have_wisdom'] is False
    assert v['model'] == CXC
    assert v['slug'] == 'amd-ryzen-5-5560u'
    assert B4 in v['known_bundles']


def test_a_hit_reports_the_bundle(tmp_path):
    _bundle(tmp_path, 'amd-ryzen-5-5560u', f'model={CXC}\n')
    cpuinfo = tmp_path / 'cpuinfo'
    cpuinfo.write_text(f'model name\t: {CXC}\n')

    v = identify_cpu([tmp_path], cpuinfo)

    assert v['have_wisdom'] is True
    assert v['bundle'].name == 'wisdomf-amd-ryzen-5-5560u'


def test_unreadable_cpuinfo_is_a_miss_not_a_crash(tmp_path):
    """Bring-up must not die here; it must report 'unknown' and carry on
    to the planner."""
    v = identify_cpu([tmp_path], tmp_path / 'nope')

    assert v['have_wisdom'] is False
    assert v['model'] == 'unknown'
