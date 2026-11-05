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
    except (OSError, ValueError):   # ValueError: not UTF-8
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


def cpu_model(cpuinfo: Path | None = None) -> str:
    """The /proc/cpuinfo 'model name' string, or '' if unreadable."""
    cpuinfo = CPUINFO if cpuinfo is None else cpuinfo
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
    except (OSError, ValueError):   # ValueError: not UTF-8 reads as no sidecar
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


def identify_cpu(dirs, cpuinfo: Path | None = None) -> dict:
    """What this host is, and whether we have wisdom for it.

    Returns a verdict dict the caller prints verbatim — never None, never
    a bare bool, so a miss cannot be mistaken for "nothing to report".
    """
    cpuinfo = CPUINFO if cpuinfo is None else cpuinfo
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


def install_wisdom(tmp: Path | None = None, dst: Path | None = None) -> None:
    """Atomically replace ``dst`` with ``tmp`` after a successful plan run.

    Uses rename rather than copy so the swap is atomic — radiod readers
    never see a half-written file.  Both paths are on /etc/fftw so this
    is a same-filesystem rename.
    """
    tmp = WISDOM_TMP if tmp is None else tmp
    dst = WISDOM_FILE if dst is None else dst
    if not tmp.is_file():
        raise FileNotFoundError(f'{tmp} not present — planning did not finish')
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp.replace(dst)


# ── Is the installed wisdom current? ─────────────────────────────────────
#
# sigma, 2026-11-05: a June /etc/fftw/wisdomf, planned for other sizes, kept
# the planner from ever running.  The unit's ConditionPathExists=! and
# bring-up's exists() both asked "is there a file?".  A file says nothing
# about which CPU timed it, which sizes it holds, or what cache radiod had.
# The sidecar beside it records those; wisdom_is_current() compares them.
WISDOM_SIDECAR = Path('/etc/fftw/wisdomf.cpu')
SYS_CPU = Path('/sys/devices/system/cpu')


def _size_bytes(text: str) -> int:
    """'16384K' / '8M' / '4096' from a sysfs cache `size` file -> bytes."""
    text = text.strip().upper()
    mult = {'K': 1024, 'M': 1024 * 1024, 'G': 1024 ** 3}.get(text[-1:], 1)
    try:
        return int(text.rstrip('KMG')) * mult
    except ValueError:
        return 0


def radiod_placement(cpus, performance_cpus, cache_root: Path = SYS_CPU) -> dict:
    """The conditions FFTW's timings depend on, for radiod on ``cpus``.

    radiod_core_type: 'performance' on a hybrid part (performance_cpus
    non-empty), else 'uniform'.  radiod_l3_bytes: the size of the L3 domain
    the first of radiod's cores sits in (0 if sysfs does not say).
    Which cores within a like set radiod holds does not matter, so this
    records no CPU numbers.
    """
    l3 = 0
    for cpu in sorted(cpus):
        base = Path(cache_root) / f'cpu{cpu}' / 'cache'
        for idx in sorted(base.glob('index*')):
            try:
                if (idx / 'level').read_text().strip() != '3':
                    continue
                l3 = _size_bytes((idx / 'size').read_text())
            except OSError:
                continue
            break
        if l3:
            break
    return {'radiod_core_type': 'performance' if performance_cpus else 'uniform',
            'radiod_l3_bytes': l3}


def write_sidecar(path: Path, model: str, planned_profiles, placement=None,
                  extra=None) -> None:
    """Record, in the bundles' v2 format, what a wisdom file was made for."""
    lines = [f'model={model}', f'planned_profiles={",".join(planned_profiles)}']
    for k, v in {**(extra or {}), **(placement or {})}.items():
        lines.append(f'{k}={v}')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.new')
    tmp.write_text('\n'.join(lines) + '\n')
    tmp.replace(path)


def wisdom_verdict(wisdom_file: Path | None = None,
                   sidecar: Path | None = None,
                   miss_log: Path | None = None,
                   cpuinfo: Path | None = None,
                   placement: dict | None = None) -> str:
    """'current', 'top-up' or 'fresh': what the planner should do.

    fresh: no file; no trustworthy sidecar; a sidecar for another CPU; or,
    with ``placement`` (radiod_placement()'s dict), another core type or L3
    size.  Plans timed elsewhere are worth nothing here, so the planner
    discards them (fftwf-wisdom -n).
    top-up: wisdom made here (planned, bundle-seeded or adopted from an
    image) that lacks a size radiod logged in fft.log.  The planner keeps
    its plans and measures only what is missing.
    current: neither.

    A sidecar that records no placement (a bundled one: its radiod_l3_bytes
    reads "unknown-from-guest") cannot be compared and does not block.  An
    image's adopted sidecar (adopted=legacy-image) keeps the image's old
    behaviour: no fft.log check, no placement check, and an empty model on
    either side never counts as another CPU.
    """
    wisdom_file = WISDOM_FILE if wisdom_file is None else wisdom_file
    sidecar = WISDOM_SIDECAR if sidecar is None else sidecar
    miss_log = FFT_MISS_LOG if miss_log is None else miss_log
    cpuinfo = CPUINFO if cpuinfo is None else cpuinfo
    if not Path(wisdom_file).is_file():
        return 'fresh'
    side = read_sidecar(sidecar)
    model = cpu_model(cpuinfo)
    adopted = side.get('adopted') == 'legacy-image'
    if adopted:
        # A host whose /proc/cpuinfo gave no model at adoption wrote `model=`;
        # reading that as another CPU replanned the image for hours (final
        # review M5, 2026-11-05).
        was = side.get('model', '')
        if was and model and was != model:
            return 'fresh'
        return 'current'
    if not model or side.get('model') != model:
        return 'fresh'
    for key, now in (placement or {}).items():
        was = side.get(key)
        if was is None or was.startswith('unknown'):
            continue
        if str(was) != str(now):
            return 'fresh'
    planned = {p for p in side.get('planned_profiles', '').split(',') if p}
    if any(p not in planned for p in plans_from_fft_log(miss_log)):
        return 'top-up'
    return 'current'


def wisdom_is_current(wisdom_file: Path | None = None,
                      sidecar: Path | None = None,
                      miss_log: Path | None = None,
                      cpuinfo: Path | None = None,
                      placement: dict | None = None) -> bool:
    """True only when wisdom_verdict() finds nothing to plan."""
    return wisdom_verdict(wisdom_file, sidecar, miss_log, cpuinfo,
                          placement) == 'current'


def xhci_irqs_on(cpus, proc_interrupts: Path = Path('/proc/interrupts'),
                 irq_root: Path = Path('/proc/irq')) -> list:
    """[(irq, affinity_list)] for each xhci_hcd IRQ whose affinity touches ``cpus``."""
    from sigmond.cpu import parse_cpu_mask
    cpus = set(cpus)
    out = []
    try:
        lines = Path(proc_interrupts).read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        head, _, _ = line.partition(':')
        if 'xhci_hcd' not in line or not head.strip().isdigit():
            continue
        try:
            aff = (Path(irq_root) / head.strip() / 'smp_affinity_list').read_text().strip()
        except OSError:
            continue
        if parse_cpu_mask(aff) & cpus:
            out.append((int(head), aff))
    return out


def adopt_legacy_wisdom(wisdom_file: Path, sidecar: Path, cpuinfo: Path,
                        bundle_dirs, appliance: bool) -> bool:
    """Judge a wisdom file that has no sidecar (the image bakes one; every
    station that predates the sidecar has one).  True means current.

    Image install: current as before, and a sidecar (adopted=legacy-image) is
    written so later checks have one.  ``placement`` stays out of that
    sidecar: recorded there, a later layout change made an image station
    "not current", so apply refused to start radiod and the next boot
    planned for hours (final review I4, 2026-11-05).  Repo install: current only when the
    file is byte-identical to this CPU's bundle, whose sidecar is then
    installed beside it; sigma's June file is not, so it replans.  Writing
    needs root; a failed write still reports the verdict.
    """
    if not Path(wisdom_file).is_file():
        return False
    model = cpu_model(cpuinfo)
    if appliance:
        try:
            write_sidecar(sidecar, model, [],
                          extra={'adopted': 'legacy-image'})
        except OSError:
            pass
        return True
    bundle = find_bundle(bundle_dirs, model)
    if bundle is None:
        return False
    try:
        if Path(wisdom_file).read_bytes() != bundle.read_bytes():
            return False
        import shutil
        shutil.copyfile(str(bundle) + '.cpu', sidecar)
    except OSError:
        pass
    return True
