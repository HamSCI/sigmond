"""radiod's FFTW-version wisdom slot, and whose plans are in it.

radiod (ka9q-radio src/filter.c) imports wisdom from three places at startup
and merges whatever imports:

  1. the system file, /etc/fftw/wisdomf          (fftwf_import_system_wisdom)
  2. /var/lib/ka9q-radio/wisdom                    (generic)
  3. /var/lib/ka9q-radio/wisdom-<fftwf_version>[-threaded]

The third is KA9Q's convention (and what ka9q-radio's own planner, fft-gen,
writes).  ``fftwf_version`` is the library's full version string, SIMD build
flags included -- "fftw-3.3.8-sse2-avx" from Ubuntu 22.04's libfftw3f,
"fftw-3.3.11-sse2-avx-avx2-avx2_128" from a hand-built 3.3.11 -- and the
suffix is "-threaded" whenever radiod plans with internal threads, which
filter.c always does (N_internal_threads = 1).  FFTW refuses wisdom made by
another version, so a slot can only ever be read by the library that made
it: two FFTW builds on one host keep two slots and never clobber each other.
The system and generic files are shared, and only the build that wrote them
last can use them.

The slot names the LIBRARY.  It says nothing about the CONDITIONS the plans
were timed under, which matter as much: sigmond plans on radiod's own FFT
core, every movable IRQ herded off it, isolcpus/nohz_full, inside radiod's
L3 CAT slice, at its pinned clock.  A plan timed on the same hardware without
those describes a different machine.  So the sidecar beside the slot
(``<slot>.cpu``) records who planned it and under what, and sigmond treats
a slot without its own sidecar as FOREIGN:

  * where ka9q-radio is locally managed (an operator owns radiod -- KA9Q on
    the KFS hosts), sigmond does not plan, seed or publish wisdom at all;
  * on a host sigmond owns, a foreign slot is renamed aside (kept, never
    deleted) when sigmond publishes its own plans there.
"""
from __future__ import annotations

import ctypes
import re
import subprocess
import time
from pathlib import Path

KA9Q_STATEDIR = Path('/var/lib/ka9q-radio')
RADIOD_BIN = Path('/usr/local/sbin/radiod')
MADE_BY = 'sigmond'

# The wisdom text's first line: "(fftw-3.3.8 fftwf_wisdom #x... #x... ...".
_HEADER = re.compile(rb'^\(fftw-(\d[\w.]*) fftwf_wisdom\b')
_LIBFFTW3F = re.compile(r'^\s*libfftw3f\.so\S*\s+=>\s+(\S+)')


def header_version(path: Path) -> str:
    """'3.3.8' from a wisdom file's header, or '' when it has none."""
    try:
        with open(path, 'rb') as fh:
            m = _HEADER.match(fh.read(96))
    except OSError:
        return ''
    return m.group(1).decode() if m else ''


def numeric_version(fftwf_version: str) -> str:
    """'fftw-3.3.8-sse2-avx' -> '3.3.8' (what a wisdom header carries)."""
    m = re.match(r'^fftw-(\d[\d.]*)', fftwf_version or '')
    return m.group(1).rstrip('.') if m else ''


def radiod_libfftw3f(binary: Path = RADIOD_BIN, run=subprocess.run) -> str:
    """Path of the libfftw3f.so radiod is linked against, or ''."""
    try:
        r = run(['ldd', str(binary)], capture_output=True, text=True,
                timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return ''
    for line in (r.stdout or '').splitlines():
        m = _LIBFFTW3F.match(line)
        if m and m.group(1) != 'not':
            return m.group(1)
    return ''


def library_version(lib_path: str, loader=ctypes.CDLL) -> str:
    """``fftwf_version`` exported by a libfftw3f, e.g. 'fftw-3.3.8-sse2-avx'."""
    if not lib_path:
        return ''
    # FFTW declares ``const char fftwf_version[]``: an ARRAY, not a char *.
    # Reading it as c_char_p takes the string's first bytes for an address
    # and segfaults; take the symbol's address and read the string there.
    try:
        lib = loader(lib_path)
        raw = ctypes.string_at(ctypes.addressof(ctypes.c_char.in_dll(lib, 'fftwf_version')))
    except (OSError, ValueError, AttributeError):
        return ''
    return (raw or b'').decode(errors='replace')


def radiod_fftw_version(binary: Path = RADIOD_BIN, run=subprocess.run,
                        loader=ctypes.CDLL) -> str:
    """The fftwf_version of the library radiod actually loads, or ''."""
    return library_version(radiod_libfftw3f(binary, run=run), loader=loader)


def slot_path(fftwf_version: str, threaded: bool = True,
              statedir: Path = KA9Q_STATEDIR) -> Path:
    """radiod's wisdom slot for this library: the exact name filter.c builds."""
    return Path(statedir) / f"wisdom-{fftwf_version}{'-threaded' if threaded else ''}"


def sidecar_of(slot: Path) -> Path:
    return slot.with_name(slot.name + '.cpu')


def slot_owner(slot: Path, read_sidecar=None) -> str:
    """'absent', 'sigmond' (its sidecar says sigmond planned it) or 'foreign'."""
    if not slot.exists():
        return 'absent'
    if read_sidecar is None:
        from sigmond.wisdom import read_sidecar
    return 'sigmond' if read_sidecar(sidecar_of(slot)).get('made_by') == MADE_BY \
        else 'foreign'


def compatible(wisdom_file: Path, fftwf_version: str) -> bool:
    """True when the wisdom file's header names radiod's FFTW version.

    FFTW refuses wisdom made by another version, so a mismatched file is
    worth nothing to radiod however current its sidecar looks.  Unknown on
    either side (no radiod yet, no header) is not a mismatch."""
    have = header_version(wisdom_file)
    want = numeric_version(fftwf_version)
    return not have or not want or have == want


def conditions(radiod_cpus, resctrl: Path = Path('/sys/fs/resctrl'),
               sysfs: Path = Path('/sys/devices/system/cpu'),
               cmdline: Path = Path('/proc/cmdline')) -> dict:
    """What the plans are being timed under, for the sidecar (informational:
    nothing compares these yet; see wisdom/README.md)."""
    cpus = sorted(radiod_cpus or ())
    out: dict = {'radiod_cpus': ','.join(str(c) for c in cpus)}
    if not cpus:
        return out
    try:
        out['radiod_max_khz'] = (sysfs / f'cpu{cpus[0]}' / 'cpufreq'
                                 / 'scaling_max_freq').read_text().strip()
    except OSError:
        pass
    try:
        out['radiod_cat_l3'] = ' '.join(
            l.strip() for l in (resctrl / 'radiod' / 'schemata').read_text().splitlines()
            if l.strip().startswith('L3'))
    except OSError:
        out['radiod_cat_l3'] = 'none'
    try:
        cl = cmdline.read_text()
        m = re.search(r'\bisolcpus=(\S+)', cl)
        out['isolcpus'] = m.group(1) if m else 'none'
    except OSError:
        pass
    return out


def publish(src: Path, src_sidecar: Path, fftwf_version: str, *, threaded: bool = True,
            statedir: Path = KA9Q_STATEDIR, extra: dict | None = None,
            install=None, now=time.time) -> tuple:
    """Copy sigmond's planned wisdom into radiod's slot for this library.

    Returns (slot, action, moved_aside): action is 'published', 'current'
    (slot already holds these bytes) or 'skipped: <why>'.  A foreign slot is
    renamed to <slot>.foreign-<epoch> first -- kept, never deleted.
    ``install(src, dst)`` does the privileged copy (default: shutil.copyfile).
    """
    import shutil
    from sigmond.wisdom import read_sidecar
    if not fftwf_version:
        return None, 'skipped: radiod FFTW version unknown', None
    if not compatible(src, fftwf_version):
        return None, (f'skipped: {src} is FFTW {header_version(src)}, radiod loads '
                      f'{fftwf_version}'), None
    slot = slot_path(fftwf_version, threaded, statedir)
    side = sidecar_of(slot)
    copy = install or (lambda a, b: shutil.copyfile(a, b))
    moved = None
    owner = slot_owner(slot, read_sidecar)
    if owner == 'sigmond':
        try:
            if slot.read_bytes() == Path(src).read_bytes():
                return slot, 'current', None
        except OSError:
            pass
    elif owner == 'foreign':
        moved = slot.with_name(f'{slot.name}.foreign-{int(now())}')
        slot.rename(moved)
    copy(Path(src), slot)
    lines = [l for l in _sidecar_text(src_sidecar).splitlines()
             if l and not l.split('=', 1)[0] in ('made_by', 'fftw_version', 'threaded')]
    meta = {'made_by': MADE_BY, 'fftw_version': fftwf_version,
            'threaded': 'yes' if threaded else 'no', **(extra or {})}
    lines += [f'{k}={v}' for k, v in meta.items() if k not in
              {l.split('=', 1)[0] for l in lines}]
    tmp = side.with_name(side.name + '.new')
    tmp.write_text('\n'.join(lines) + '\n')
    copy(tmp, side)
    tmp.unlink(missing_ok=True)
    return slot, 'published', moved


def _sidecar_text(path: Path) -> str:
    try:
        text = Path(path).read_text()
    except (OSError, ValueError):
        return ''
    if '=' not in text:                       # v1: a bare model line
        return f'model={text.strip()}' if text.strip() else ''
    return text
