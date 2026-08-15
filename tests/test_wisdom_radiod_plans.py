"""The baked wisdom must cover the transforms radiod actually plans.

Two faults compounded on every DASI2 install:

1. `FFT_WISDOM_PROFILES` lists only `cob*` (complex, out-of-place,
   backward) plus two `rof*` front-end sizes.  radiod's own channel
   filter also plans IN-PLACE forward (`cif*`) and out-of-place forward
   (`cof*`) transforms -- see filter.c, which encodes the mnemonic as
   complex / in-place-or-out / forward-or-backward / N.  Those families
   were never planned at all.
2. `sigmond-wisdom.service` is conditioned on `/etc/fftw/wisdomf` being
   ABSENT, and provision-components.sh BAKES that file -- so the
   generator is a permanent no-op on every install, and could never
   notice the gap.

radiod plans `FFTW_WISDOM_ONLY|FFTW_PATIENT` and silently falls back to
`FFTW_ESTIMATE` on a miss (filter.c:105-108): fast to plan, suboptimal
forever, and invisible in startup time.  The only detector is
`/var/lib/ka9q-radio/fft.log`, which is written ONLY on a miss.

Observed on AC0G-B4 2026-08-15, all seven falling back to ESTIMATE:
cif2400 cif300 cif512 cif600 cob2400 cob512 cof512
"""
from sigmond.wisdom import FFT_WISDOM_PROFILES, plans_from_fft_log

# Exactly what B4 reported as missing.
B4_MISSES = ('cif2400', 'cif300', 'cif512', 'cif600',
             'cob2400', 'cob512', 'cof512')


def test_the_profile_list_covers_the_transforms_radiod_missed():
    missing = [p for p in B4_MISSES if p not in FFT_WISDOM_PROFILES]

    assert not missing, f"still unplanned, will fall back to ESTIMATE: {missing}"


def test_in_place_and_forward_families_are_represented():
    """The structural gap: the list was all `cob`, so no in-place (`ci*`)
    and no forward complex (`c*f`) transform was ever planned."""
    assert any(p.startswith('cif') for p in FFT_WISDOM_PROFILES)
    assert any(p.startswith('cof') for p in FFT_WISDOM_PROFILES)


def test_fft_log_misses_are_read_back(tmp_path):
    """fft.log is the authoritative miss list — radiod writes a line
    there only when wisdom did NOT have the plan."""
    log = tmp_path / "fft.log"
    log.write_text("cif300\ncif300\ncob512\ncif300\ncof512\n")

    assert plans_from_fft_log(log) == ['cif300', 'cob512', 'cof512']


def test_an_empty_fft_log_means_full_coverage(tmp_path):
    log = tmp_path / "fft.log"
    log.write_text("")

    assert plans_from_fft_log(log) == []


def test_a_missing_fft_log_is_not_an_error(tmp_path):
    """Absent before radiod has ever run."""
    assert plans_from_fft_log(tmp_path / "nope.log") == []


def test_garbage_lines_are_ignored(tmp_path):
    log = tmp_path / "fft.log"
    log.write_text("cif300\n\n  \nnot-a-plan\ncob512\n")

    assert plans_from_fft_log(log) == ['cif300', 'cob512']
