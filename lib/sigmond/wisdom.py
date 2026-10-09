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
# Transform mnemonic: [cr] [iod] [fb] <size>.  The middle letter is
# placement: i = in place, o = out of place with the input preserved,
# d = out of place with the input destroyed.  radiod logs `d` since
# ka9q-radio bc224260 (2026-10-07), and fftwf-wisdom accepts it.
_PLAN_RE = re.compile(r'[cr][iod][fb]\d+')

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
    # ── ka9q-web spectrum zoom ladder ─────────────────────────────────
    # EVERY zoom level in the web UI is a DIFFERENT transform size.  A
    # browser walking the zoom control creates each one in turn:
    # spectrum.c:650 plans the real forward (`rof<bins>`) and filter.c
    # plans that channel's cif/cof/cob trio.  None of these were listed,
    # so every zoom level a user touched ran on FFTW_ESTIMATE forever —
    # and the cost lands in the `fft` worker thread, which is the one an
    # operator watches in top.
    #
    # rob walked all the zoom levels on the AI6VN lab box and planned
    # them.  That work was LOST, because nothing ever wrote the list
    # down — plans_from_fft_log() existed but fed a status field, never
    # the planner.  Recovered 2026-10-02 from WB6CXC-7's fft.log with
    # three browser sessions open: 25 distinct transforms, not one of
    # them covered here.  This block is the durable record; the planner
    # now also reads fft.log at run time, so a size nobody listed still
    # gets planned on whichever station first meets it.
    'cof1625', 'cof1638', 'cof1650', 'cof1664',
    'cif1650', 'cif2080', 'cif3250', 'cif4095', 'cif8125',
    'cob1650', 'cob2080', 'cob3250', 'cob4095', 'cob8125',
    'rof3240',   'rof6480',   'rof12960',  'rof16200',  'rof25920',
    'rof32400',  'rof64800',  'rof129600', 'rof162000', 'rof259200',
    'rof324000',
    # ── input-destroying channel outputs (radiod >= 2026-10-07) ────────
    # ka9q-radio bc224260 plans every channel's output transform with
    # FFTW_DESTROY_INPUT (filter.c: slave->rev_plan = plan_complex(...,
    # false)) and logs it with a `d`: cdb<N>, not cob<N>.  Wisdom for cob<N>
    # does not satisfy a cdb<N> request, so on current radiod every cob
    # entry above misses and the channel runs on FFTW_ESTIMATE.  One cdb
    # twin per cob size; the cob entries stay for stations on older radiod.
    # Found on DP0GVN (dp0) 2026-10-08, where fft.log listed 26 cdb misses.
    'cdb15',   'cdb45',   'cdb85',
    'cdb160',  'cdb200',  'cdb205',  'cdb300',   'cdb320',
    'cdb400',  'cdb405',  'cdb480',  'cdb512',   'cdb600',  'cdb800',
    'cdb810',  'cdb960',  'cdb1200', 'cdb1600',  'cdb1620', 'cdb1650',
    'cdb1920', 'cdb2080', 'cdb2400', 'cdb3200',  'cdb3240', 'cdb3250',
    'cdb4095', 'cdb4800', 'cdb4860', 'cdb6930',  'cdb8100', 'cdb8125',
    'cdb9600', 'cdb16200', 'cdb32400', 'cdb40500', 'cdb81000', 'cdb162000',
    # ── front-end forward real FFTs — the expensive pair, planned LAST ──
    'rof1620000',   # RX888 MkII @  64.8 MHz, 20 ms block, overlap 5
    'rof3240000',   # RX888 MkII @ 129.6 MHz, 20 ms block, overlap 5  ← hours
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


def profiles_for_planning(log: Path = FFT_MISS_LOG) -> list[str]:
    """The static list PLUS whatever this station actually missed.

    The static list can only cover sizes someone thought of.  fft.log is
    what radiod really asked for and could not find, so the union is the
    only list that converges: plan it, radiod stops missing, the log stops
    growing.  Order is preserved — static first (smallest-first, the
    multi-hour front-end pair last), then the misses.

    Reading the log is the entire self-healing mechanism.  It existed as
    plans_from_fft_log() from 8fe856c but fed only a status field, so the
    gap was reported and never closed; the ka9q-web zoom-level plans built
    by hand on the AI6VN lab box were lost for exactly that reason.
    """
    return [*FFT_WISDOM_PROFILES,
            *(p for p in plans_from_fft_log(log) if p not in FFT_WISDOM_PROFILES)]


# ── CPU identity: which bundled wisdom, if any, fits THIS machine ────────
#
# Planning rof3240000 from scratch took 1 h 46 m on a Ryzen 5 5560U.  A
# bring-up that has to do that is an afternoon, so the repo carries
# pre-planned bundles (wisdom/wisdomf-<slug> + a .cpu sidecar) and a host
# whose CPU matches gets seeded instead of planning.
#
# ⛔ A MISS MUST BE LOUD.  The failure mode this is written against is the
# silent one: no bundle matches, nobody says so, and the operator watches
# an apparently-hung bring-up for an hour.  identify_cpu() always returns
# a verdict, and the caller is expected to print it either way.
#
# An FFTW plan is only valid for the machine it was MEASURED on — FFTW
# chooses algorithms by timing them, so cache size, cache partition and
# clock all change the answer.  That is why the sidecar records more than
# a model name: two hosts with the same CPU and different L3 partitions
# are, for this purpose, different machines.
CPUINFO = Path('/proc/cpuinfo')


def cpu_model(cpuinfo: Path = CPUINFO) -> str:
    """The /proc/cpuinfo 'model name' string, or '' if unreadable."""
    try:
        for line in cpuinfo.read_text().splitlines():
            if line.startswith('model name'):
                return line.split(':', 1)[1].strip()
    except (OSError, IndexError):
        pass
    return ''


def cpu_slug(model: str) -> str:
    """Filesystem-safe bundle slug: 'AMD Ryzen 5 5560U with Radeon Graphics'
    -> 'amd-ryzen-5-5560u'.  Trailing marketing words are dropped so the
    5825U and 5560U bundles sit next to each other legibly."""
    m = re.sub(r'\s+with\s+.*$', '', model.strip(), flags=re.I)
    m = re.sub(r'\(R\)|\(TM\)|CPU|Processor', ' ', m, flags=re.I)
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', m.lower())).strip('-')


def read_sidecar(path: Path) -> dict:
    """Parse a bundle sidecar.

    Two formats, because the first bundle predates the second: a bare
    model-name line (v1), or key=value lines (v2, which also records L3
    geometry, the radiod cache partition and the memory configuration).
    Both yield at least {'model': ...} so matching logic stays one branch.
    """
    try:
        text = path.read_text()
    except OSError:
        return {}
    if '=' not in text:
        model = text.strip()
        return {'model': model} if model else {}
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        out[k.strip()] = v.strip()
    return out


def find_bundle(dirs, model: str) -> Path | None:
    """The bundled wisdom whose sidecar names exactly this CPU model.

    Exact match only.  Wisdom from a different CPU still *works* — FFTW
    falls back to planning anything it cannot find — but it defeats the
    purpose, because every plan in it was timed on the wrong machine.
    """
    if not model:
        return None
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for sidecar in sorted(d.glob('wisdomf-*.cpu')):
            if read_sidecar(sidecar).get('model') != model:
                continue
            bundle = sidecar.with_suffix('')
            if bundle.is_file() and bundle.stat().st_size > 0:
                return bundle
    return None


def identify_cpu(dirs, cpuinfo: Path = CPUINFO) -> dict:
    """What this host is, and whether we have wisdom for it.

    Returns a verdict dict the caller prints verbatim — never None, never
    a bare bool, so a miss cannot be mistaken for "nothing to report".
    """
    model = cpu_model(cpuinfo)
    bundle = find_bundle(dirs, model)
    return {
        'model': model or 'unknown',
        'slug': cpu_slug(model) if model else '',
        'bundle': bundle,
        'have_wisdom': bundle is not None,
        'known_bundles': sorted(
            read_sidecar(s).get('model', s.name)
            for d in dirs if Path(d).is_dir()
            for s in Path(d).glob('wisdomf-*.cpu')
        ),
    }


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
