"""FFTW3 wisdom-file planning — paths, profile list, install helper.

CLI-only: the standalone TUI screen for this was deleted (Task 2 of the
TUI reconciliation, 2026-08); the planner now has two callers, both of
which shell out to the same CLI verb rather than importing this module
directly:

  * The CLI verb itself (``smd admin wisdom plan``) — foreground run for
    operators on a tmux/screen session who want to disconnect and
    reconnect over an hours-long planning job.
  * ``systemd/sigmond-wisdom.service`` — the first-boot oneshot Guided
    bring-up enables, which execs ``smd admin wisdom plan`` when
    ``/etc/fftw/wisdomf`` is absent.

Both callers land on the same ``fftwf-wisdom`` profile list, so the
generated ``/etc/fftw/wisdomf`` is bit-identical regardless of which
surface the operator chose.

Profile list mirrors ka9q-radio's docs/FFTW3.md recommendations:
inverse FFTs (``cob…``) for every demodulator-channel size radiod
ever uses, plus the forward FFTs (``rof…``) for the two RX-888 sample
rates that dominate planning time on x86 / Pi5.
"""

from __future__ import annotations

import re
from pathlib import Path

# Where the wisdom files live.  Radiod reads system-wide first, then
# the app-specific fallback.  Sigmond plans into the system-wide path
# because it survives package upgrades and is shared across any other
# FFTW user on the host.
# fftwf-wisdom transform mnemonic: [cr] [io] [fb] <size>.
_PLAN_RE = re.compile(r'[cr][io][fb]\d+')

WISDOM_FILE = Path('/etc/fftw/wisdomf')
WISDOM_TMP  = Path('/etc/fftw/wisdomf.new')

# Where progress logs land — useful for operators reattaching to a
# screen session and for post-run forensics ("which transform took
# 47 minutes?").
WISDOM_LOG = Path('/tmp/ka9q-wisdom.log')


# Transform sizes to plan, smallest first so quick wins land before
# the multi-hour rof3240000.  Adding a new size: append it here, both
# the TUI progress meter and CLI runner pick it up automatically.
FFT_WISDOM_PROFILES: tuple[str, ...] = (
    # Inverse FFTs for demodulator channels.
    'cob15',   'cob45',   'cob85',
    'cob160',  'cob200',  'cob205',  'cob300',   'cob320',
    'cob400',  'cob405',  'cob480',  'cob600',   'cob800',  'cob810',
    'cob960',  'cob1200', 'cob1600', 'cob1620',  'cob1920',
    'cob3200', 'cob3240', 'cob4800', 'cob4860',  'cob6930',
    'cob8100', 'cob9600', 'cob16200', 'cob32400', 'cob40500',
    'cob81000', 'cob162000',
    # Forward real FFTs.
    'rof1620000',   # RX888 MkII @  64.8 MHz, 20 ms block, overlap 5
    'rof3240000',   # RX888 MkII @ 129.6 MHz, 20 ms block, overlap 5  ← hours
    # ── radiod's own channel-filter transforms ────────────────────────
    # The list above is all `cob` (complex, OUT-of-place, BACKWARD) plus
    # the two front-end `rof` sizes.  radiod also plans IN-PLACE forward
    # (`cif`) and out-of-place forward (`cof`) transforms — filter.c
    # encodes the mnemonic as complex / in-place-or-out / forward-or-
    # backward / N — so those families were never planned at ALL.  On a
    # miss radiod silently falls back to FFTW_ESTIMATE (filter.c:105-108):
    # fast to plan, suboptimal forever, and invisible in startup time.
    # These seven were observed falling back on AC0G-B4 2026-08-15 via
    # /var/lib/ka9q-radio/fft.log, which radiod writes ONLY on a miss.
    'cif300',  'cif512',  'cif600',  'cif2400',
    'cof512',
    'cob512',  'cob2400',
)


# radiod records every wisdom MISS here — and nothing else.  A non-empty
# file means it is running estimate plans for those transforms right now,
# which is the only way to observe the gap: planning stays fast either
# way, so startup time proves nothing.
FFT_MISS_LOG = Path('/var/lib/ka9q-radio/fft.log')


def plans_from_fft_log(log: Path = FFT_MISS_LOG) -> list[str]:
    """Distinct transform names radiod could not find in wisdom.

    The authoritative top-up list: whatever is here is what the static
    profiles above failed to cover on this host's actual channel set.
    Absent file (radiod has never run) and blank/garbage lines are not
    errors — they simply mean nothing to add.
    """
    try:
        raw = log.read_text().split()
    except OSError:
        return []
    seen, out = set(), []
    for tok in raw:
        if not _PLAN_RE.fullmatch(tok) or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def install_wisdom(tmp: Path = WISDOM_TMP, dst: Path = WISDOM_FILE) -> None:
    """Atomically replace ``dst`` with ``tmp`` after a successful plan run.

    Uses rename rather than copy so the swap is atomic — radiod readers
    never see a half-written file.  Both paths are on /etc/fftw so this
    is a same-filesystem rename.
    """
    if not tmp.is_file():
        raise FileNotFoundError(f'{tmp} not present — planning did not finish')
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp.replace(dst)
