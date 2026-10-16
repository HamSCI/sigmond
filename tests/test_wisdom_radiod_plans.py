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


# ── ka9q-web spectrum zoom ladder ────────────────────────────────────────
#
# Every zoom level in the web UI is a different transform size, so a
# browser walking the zoom control creates each in turn.  None were in
# the profile list, so each ran on FFTW_ESTIMATE forever, and the cost
# lands in the `fft` worker thread.  Observed on WB6CXC-7 2026-10-02 with
# three ka9q-web sessions open — 25 distinct transforms, none covered.
#
# rob had already planned these by hand on the AI6VN lab box; that work
# was lost because nothing wrote the list down.  This test is what makes
# losing it again a test failure rather than a silent regression.
CXC_ZOOM_MISSES = (
    'cof1625', 'cof1638', 'cof1650', 'cof1664',
    'cif1650', 'cif2080', 'cif3250', 'cif4095', 'cif8125',
    'cob1650', 'cob2080', 'cob3250', 'cob4095', 'cob8125',
    'rof3240', 'rof6480', 'rof12960', 'rof16200', 'rof25920',
    'rof32400', 'rof64800', 'rof129600', 'rof162000', 'rof259200',
    'rof324000',
)


def test_the_zoom_ladder_is_covered():
    missing = [p for p in CXC_ZOOM_MISSES if p not in FFT_WISDOM_PROFILES]

    assert not missing, (
        f"ka9q-web zoom levels left unplanned, will run on FFTW_ESTIMATE: {missing}"
    )


def test_the_expensive_front_end_pair_is_planned_last():
    """Smallest-first is a usability property: the operator must see
    progress in seconds, and rof3240000 alone takes ~1 h 46 m (measured
    on WB6CXC-7, a 5560U).  If it drifts earlier the run looks hung."""
    assert FFT_WISDOM_PROFILES[-2:] == ('rof1620000', 'rof3240000')


def test_planner_plans_the_misses_not_just_the_static_list(tmp_path):
    """The whole point: fft.log must reach the PLANNER, not just a status
    field.  plans_from_fft_log() shipped in 8fe856c wired only to
    `wisdom_misses` in `smd diag` — it reported the gap for seven weeks
    and never closed it.  Delete the union below and this is the test
    that fails."""
    from sigmond.wisdom import profiles_for_planning

    log = tmp_path / "fft.log"
    log.write_text("rof99999 cob77777 rof99999\ncob15\n")   # 2 new, 1 dup, 1 known

    profiles = profiles_for_planning(log)

    assert 'rof99999' in profiles and 'cob77777' in profiles
    assert profiles.count('rof99999') == 1, "a repeated miss must not be planned twice"
    assert profiles.count('cob15') == 1, "a miss already in the static list is not re-added"
    assert profiles[:len(FFT_WISDOM_PROFILES)] == list(FFT_WISDOM_PROFILES)


def test_planner_is_unchanged_when_nothing_missed(tmp_path):
    """The null: an empty fft.log must add nothing.  Without this, a test
    that only ever sees misses would pass against a function that
    unconditionally appended garbage."""
    from sigmond.wisdom import profiles_for_planning

    log = tmp_path / "fft.log"
    log.write_text("")

    assert profiles_for_planning(log) == list(FFT_WISDOM_PROFILES)


# ── input-destroying channel outputs ─────────────────────────────────────
#
# ka9q-radio bc224260 (2026-10-07) plans each channel's output transform
# with FFTW_DESTROY_INPUT and logs it as cdb<N>.  Wisdom for cob<N> does
# not satisfy that request, so every cob size needs a cdb twin, and the
# fft.log reader must accept the `d` placement letter or the miss never
# reaches the planner.  Observed on DP0GVN 2026-10-08: 26 cdb misses.

def test_every_channel_output_size_has_an_input_destroying_twin():
    cob = [p for p in FFT_WISDOM_PROFILES if p.startswith('cob')]
    missing = ['cdb' + p[3:] for p in cob if 'cdb' + p[3:] not in FFT_WISDOM_PROFILES]

    assert not missing, f"current radiod will miss these and run on FFTW_ESTIMATE: {missing}"


def test_input_destroying_misses_are_read_back(tmp_path):
    log = tmp_path / "fft.log"
    log.write_text("cdb1200\ncob1200\ncdb1200\nrdb640\n")

    assert plans_from_fft_log(log) == ['cdb1200', 'cob1200', 'rdb640']


def test_the_placement_letter_is_still_checked(tmp_path):
    """Accepting `d` must not mean accepting any letter."""
    log = tmp_path / "fft.log"
    log.write_text("cqb1200\ncdb1200\n")

    assert plans_from_fft_log(log) == ['cdb1200']
