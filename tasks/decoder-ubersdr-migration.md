# Moving to the UberSDR `wsprd` and `jt9`

**Status:** planned 2026-10-24, implementation started.  rob's ask: adopt
madpsy's optimised decoders, built from source on the host, selectable, with
the new ones as the default and the WSJT-X reference as the fallback.

## Why

`madpsy/ubersdr_wsprd` is WSJT-X's `wsprd.c` and `jt9` rewritten in C++ for a
fraction of the CPU, and it claims to be a **drop-in replacement**: the same
arguments (plus `-n`), and the same stdout, `ALL_WSPR.TXT`, `wspr_spots.txt`,
`hashtable.txt` and `-c` output, byte for byte, from the same samples.  It is
built from WSJT-X 3.2.0-rc1 `567ad29ce`.

Claimed gains, which we verify rather than trust:

| | x86-64 | Pi 5 |
|---|---|---|
| `wsprd` | ~10x less CPU | ~6x |
| `jt9` FST4W | ~7x | — |
| `jt9` FT8 | >2x | — |
| `jt9` FT4 | ~3x | — |

rob's reasoning: **we are not going backwards**.  A sigmond station with one
RX-888 has CPU to spare for WSJT-X's `jt9`, so this is not about survival —
it is about headroom for more channels and more modes.

Secondary win: the build gets much cheaper.

| | build deps |
|---|---|
| WSJT-X (today) | gfortran, qtbase5, qttools5, qt5serialport, qtmultimedia5, qt5websockets, **boost-all**, hamlib, fftw3, libusb, portaudio — ~5 min |
| UberSDR | build-essential, cmake, pkg-config, **libfftw3-dev** — minutes |

That removes Qt and boost from the critical path of a greenfield install.

## What exists today

- `bin/smd` → `_build_wsjtx_decoders()` clones `WSJTX/wsjtx` at `v3.0.2`
  (`ccdfaf3c…`), builds with `-march=native -O3`, installs `jt9` and `wsprd`
  to `/usr/local/bin`, shadowing Debian's `wsjtx` package.  Idempotent via
  `/usr/local/bin/.wsjtx-decoders.provenance`.
- ⛔ **`-fbounds-check` is RETAINED.** Stripping it miscompiles `wsprd` under
  `-O3 -march=native`: 0 spots and a ~70 s spin, proven by live parity
  2026-07-22.  Whatever we do, that lesson must not be lost — it applies to
  the Fortran in the WSJT-X build, and the new decoder's C++ must be checked
  on its own terms rather than assumed safe.
- `wspr-recorder` resolves both by name:
  `wsprd_path = shutil.which("wsprd") or "wsprd"` (same for `jt9`), and
  `Decoder.__init__` takes both as injectable parameters.
- `psk-recorder` already vendors `madpsy/jt9-decode` at
  `/opt/git/sigmond/psk-recorder/vendor/jt9-decode` — a *driver* that runs a
  Qt5 `jt9` over shared memory.  Different thing; out of scope here, but it
  constrains us (see Risks).

## Design

### Both built, one selected

    /usr/local/libexec/sigmond/decoders/ubersdr/{wsprd,jt9}
    /usr/local/libexec/sigmond/decoders/wsjtx/{wsprd,jt9}
    /usr/local/bin/wsprd  ->  ../libexec/sigmond/decoders/<selected>/wsprd
    /usr/local/bin/jt9    ->  ../libexec/sigmond/decoders/<selected>/jt9

**Why symlinks rather than a config path.** `shutil.which("wsprd")` already
works and every client already uses it; a symlink switch needs no client
change, no new config plumbing in five repos, and no risk that one client
reads the setting and another does not.  Switching is atomic, visible in
`ls -l`, and reversible in a second with no rebuild.

Both implementations stay installed.  A fallback must not require a 5-minute
Qt build on a station that is already failing.

### Selection

`[decoders] implementation = "ubersdr" | "wsjtx"` in `coordination.toml`,
default **ubersdr**.  `smd` applies it by repointing the symlinks; `smd
doctor` reports which is live and whether the two disagree.

### Verification is the point, not a formality

The whole claim is "byte for byte identical output".  That is **testable**,
and the repo ships the means: `./build.sh --check` compares against WSJT-X's
stored outputs, and `--deep-check` builds WSJT-X and runs both side by side
over every recording and option set.

So the build gate is: **build it, then prove it matches before it is
selected.**  A decoder that is merely fast is worthless; one that silently
decodes differently is worse than worthless, because the station keeps
reporting and the spots are wrong.

⚠ On arm64 the repo documents known FFTW NEON rounding differences
(`tests/data/jt9-arm64-accepted.txt`) that print as "accepted".  Our gate
must treat "accepted" as a pass and "differs:" as a failure, exactly as
upstream's script does — not grep for the word "differ".

## Implementation steps

1. **`_build_ubersdr_decoders()`** in `bin/smd`, mirroring
   `_build_wsjtx_decoders()`: pinned repo + commit, light apt deps, an AVX2
   probe driving `-DWSPRD_X86_ARCH`, build, install to `libexec/.../ubersdr/`,
   provenance marker.  **No `-march=native`.**  *(in progress)*
2. **Move the WSJT-X build's output** into `libexec/.../wsjtx/` instead of
   `/usr/local/bin`, so neither implementation owns the name.
3. **`_select_decoders(impl)`** — repoint the two symlinks atomically,
   refusing to select an implementation that is not installed or has not
   passed its parity check.
4. **Parity gate** — run the repo's own check after building; record the
   result in the provenance marker; refuse selection on failure.
5. **Config + doctor** — read the setting, apply on `smd apply`, report in
   `smd doctor` (which implementation, parity result, when checked).
6. **Bring-up** — build both, check, select the default.
7. **Tests** — selection logic, refusal paths, the arm64 "accepted" rule,
   and that the fallback needs no rebuild.

## Risks, and what they would look like

**Different decodes that nobody notices.** The failure mode of a wrong
decoder is not a crash — it is slightly different spots, uploaded as
authoritative, for months.  This is why parity gates selection rather than
being a post-hoc check, and why `smd doctor` surfaces it.

**⛔ Do NOT add `-march=native`.  Upstream already chose, and chose
carefully.**  This is the direct descendant of the `-fbounds-check` lesson,
and it points the opposite way: there the fix was to *keep* an upstream flag,
here it is to *add none*.

`CMakeLists.txt` sets `-ffp-contract=off` on every decoder source, and on
GCC < 13 adds `-mno-fma` to the FT8/FT4/FT2 files because that compiler fuses
complex multiplies into `vfmaddsub` under `-march=x86-64-v3` *despite*
`-ffp-contract=off`.  The whole point is that every multiply and add is
rounded exactly as WSJT-X rounds it — that is what makes the output
bit-identical.  `-march=native` on top of that is us reaching into an
arithmetic contract we did not write and cannot see the edges of.

The default `WSPRD_X86_ARCH=x86-64-v3` is already the tuned value (AVX2, FMA,
BMI2 — eight lanes wide).  Upstream exposes `native` as a cache option and
says it "tunes for the build machine alone … the same results", so the
headroom over v3 is tuning, not vectorisation.  We take the default.

**But `x86-64-v3` is not universal, and that is a real hazard.**  It requires
Haswell/Zen or newer.  On an older host the binary does not run slowly — it
dies with SIGILL on the first AVX2 instruction, which from the recorder's
side looks like a decoder that produces nothing.  So the build must *probe*
for `avx2`, `fma` and `bmi2` and configure `-DWSPRD_X86_ARCH=` (empty,
the compiler baseline, same results) when any is missing, recording which it
used in the provenance marker.  Our own fleet (Ryzen 5825U, Zen 3) has all
three; a greenfield on salvaged hardware may not.

**psk-recorder's `jt9-decode` expects a Qt5 `jt9`.** It attaches over
`QSharedMemory`.  The UberSDR `jt9` supports `-s KEY` shared memory, but this
needs proving before the WSJT-X `jt9` can be removed from any host running
psk-recorder.  Until then: both stay installed, which the design already
requires.

**Two upstreams instead of one.** We now track `madpsy/ubersdr_wsprd` as well
as `WSJTX/wsjtx`.  Both get pinned commits and both appear in the manifest.

## Open questions for rob

- Pin to a release tag or a commit?  The repo publishes a rolling `latest`
  release with prebuilt binaries; we build from source, so a commit pin is
  the honest choice — but it needs a human to move it.
- Should `wsprd -n` (no drift search, plus spread) be adopted?  It is the one
  behaviour the new decoder adds, and it is not a drop-in question: it would
  change what we report.  Default off until asked for.
