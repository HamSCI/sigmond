# Pre-planned FFTW wisdom cache, keyed by CPU model

Planning the RX888 MkII forward FFT (`rof3240000`, 129.6 MHz / 20 ms
block / overlap 5) from scratch takes **hours** on a fresh host, which
makes greenfield installs painful.  DASI builds share a small set of CPU
models, so this directory caches wisdom pre-planned on a reference
machine.

It lives in sigmond (not the ka9q-radio fork) deliberately: the fork's
`main` feeds pull requests to upstream ka9q-radio (e.g. ka9q#222), and
host-tuning data must never ride along in one.  sigmond is our own repo
and is present on every install.

Layout — one pair per CPU model:

    wisdomf-<slug>       FFTW single-precision system wisdom
                         (the /etc/fftw/wisdomf produced by fftwf-wisdom
                         over sigmond's full profile list)
    wisdomf-<slug>.cpu   the exact /proc/cpuinfo "model name" line of
                         the machine that planned it

smd's `_seed_bundled_wisdom` (called at the top of the wisdom step)
compares the host's model name against each `.cpu` sidecar; on an exact
match it seeds `/etc/fftw/wisdomf` from the paired file (only when no
wisdom exists yet), then runs `fftwf-wisdom` as usual — which verifies
and skips the already-planned transforms in seconds.  On a mismatch
nothing happens and the full planning pass runs exactly as before.

To add a new CPU model: on a machine of that model whose wisdom is fully
planned, copy `/etc/fftw/wisdomf` here as `wisdomf-<slug>` and write its
`model name` line to `wisdomf-<slug>.cpu`.

## FFTW version, radiod's slot, and whose plans count

FFTW refuses wisdom made by another FFTW version: the version is part of the
header it expects (`(fftw-3.3.8 fftwf_wisdom #x... ...`), and a mismatched
file imports nothing.  The bundles here are FFTW **3.3.10** (Debian 13);
Ubuntu 22.04 ships **3.3.8**; KA9Q runs a hand-built **3.3.11**.  So:

- **Seeding checks the version.**  A bundle whose header names a different
  FFTW than the library radiod links (`ldd` on radiod, then that library's
  `fftwf_version`) is not offered, and an installed wisdom file from another
  version is judged `fresh`, never `current`.
- **radiod's version-named slot.**  radiod imports, in order and merging,
  `/etc/fftw/wisdomf`, `/var/lib/ka9q-radio/wisdom`, and
  `/var/lib/ka9q-radio/wisdom-<fftwf_version>-threaded` (KA9Q's naming,
  e.g. `wisdom-fftw-3.3.8-sse2-avx-threaded`; `fftwf_version` carries the
  SIMD build flags).  Only the build that made the slot can read it, so two
  FFTW builds on one host keep two slots and never clobber each other.
  After every plan or seed, sigmond also publishes its wisdom into the slot
  for radiod's library, with a sidecar (`<slot>.cpu`, `made_by=sigmond`).
- **Plans are timings, and sigmond's are taken under its tuning:** radiod's
  own FFT core, movable IRQs off it, `isolcpus`/`nohz_full`, the L3 CAT
  slice, the pinned clock.  The same hardware without that tuning is a
  different machine to FFTW.  So:
  - where ka9q-radio is **locally managed** (an operator owns radiod, as
    KA9Q does on the KFS hosts), sigmond plans, seeds and publishes nothing
    and calls the operator's wisdom current;
  - on a host sigmond owns, a slot without sigmond's sidecar is **foreign**:
    it is renamed `<slot>.foreign-<epoch>` (kept, never deleted) and
    sigmond's own plans take its place;
  - bundles hold only plans made under sigmond's tuning.  The slot sidecar
    records the conditions (`radiod_cpus`, `radiod_max_khz`,
    `radiod_cat_l3`, `isolcpus`); nothing compares them yet.

Not yet (stage 2): planning with ka9q-radio's `fft-gen -f` (built against
radiod's own FFTW, so a host whose radiod uses a non-distro FFTW gets plans
from the right library), bundles keyed by FFTW version as well as CPU, and
deciding which recorded conditions should force a replan.

Entries:
- `wisdomf-amd-ryzen-7-5825u` — AMD Ryzen 7 5825U (B4, 2026-07-23)
