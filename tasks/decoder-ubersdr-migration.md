# Moving to the UberSDR `wsprd` and `jt9`

**Status:** 2026-10-24 — **built, verified, and now RUNNING on a real
station (AI6VN, rob's lab box).  Still NOT pushed.**  rob's ask: adopt
madpsy's optimised decoders, built from source on the host, selectable, with
the new ones as the default and the WSJT-X reference as the fallback.

Steps 1-7 are done.  The station run is below ("On a station: AI6VN"), and it
closed the one open regression risk — in an unexpected way.  What remains is
the push, which waits on mjh's rig run.

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

## Implementation steps — all done

1. ✅ **`_build_ubersdr_decoders()`** in `bin/smd`: pinned repo + commit,
   light apt deps, an AVX2 probe driving `-DWSPRD_X86_ARCH`, build, install
   to `libexec/.../ubersdr/`, provenance marker.  **No `-march=native`.**
2. ✅ **The WSJT-X build installs to `libexec/.../wsjtx/`**, so neither
   implementation owns `/usr/local/bin`.  An upgrading station's existing
   binaries are *moved* there and linked back in the same breath.
3. ✅ **`_select_decoders(impl)`** — repoints both symlinks, refusing an
   implementation that is not installed or has not passed its parity check.
4. ✅ **Parity gate** — upstream's own check runs after the build, its
   result is recorded in the provenance marker, and selection refuses on
   failure.  Reads the script's exit status, so arm64's accepted FFTW
   differences stay a pass.
5. ✅ **Config + doctor** — `[decoders] implementation`, and findings for a
   split pair, an unapplied config change, a dangling link and a
   hand-placed binary.  Plus `smd admin decoders [--select IMPL]`.
6. ✅ **Bring-up** — both install paths call `_build_decoders()`, which
   builds both, selects the configured one, and falls back with a warning
   rather than failing the install if UberSDR cannot be built or verified.
7. ✅ **Tests** — 41, covering selection, every refusal path, graceful
   degradation, adoption and the clone-ownership rule.  Every mutation
   tried against them was caught; one exposed a test passing for the wrong
   reason, and one reproduced a bug that had already been committed.

## What was actually measured

Upstream's own comparison against WSJT-X's stored outputs, on a sigmond
build of the pinned commit, on B3 (Ryzen 5825U, GCC 14, `-march=x86-64-v3`):

| check | result |
|---|---|
| `wsprd` vs WSJT-X's stored outputs | 201 cases, **0 differing** |
| `jt9` vs WSJT-X's stored outputs | 222 cases (119 FT8, 73 FT4, 19 FT2), **0 differing** |
| 32-bit float input vs the same samples as 16-bit | 40 runs, **0 differing** |
| FT8/FT4/FT2 float input and `--feed` vs 16-bit file mode | 72 runs, **0 differing** |
| FT8/FT4/FT2 `--fast-subtract` (and `-w 2`) vs its own WSJT-X decodes | 48 runs held to 99 %, **0 short** |

The whole `_build_ubersdr_decoders` → `_select_decoders` path then ran on a
greenfield box (no `/opt/git/sigmond`, no decoders): it cloned, configured
with `-march=x86-64-v3` *chosen by the probe, not hard-coded*, built,
checked, installed, linked, and `wsprd` off PATH decoded a real 40 m
recording to the same three spots.

### The A/B, both builds on the same host

Both built by sigmond on B3, run over the same five real 40 m WSPR cycles:

| | UberSDR | WSJT-X (ours) | speedup |
|---|---|---|---|
| `wsprd`, plain | 2.41 s CPU | 5.51 s | **2.3x** |
| `wsprd -d` (deep) | 8.01 s CPU | 19.73 s | **2.5x** |
| `jt9 -8 -d 3` (FT8) | 1.60 s CPU | 2.74 s | **1.7x** |

and the decodes are **identical**: byte-identical stdout across all five
cycles (21 spots) and an identical `ALL_WSPR.TXT`.

⚠ **This is 2.5x, not the ~10x upstream reports, and the difference is
expected rather than a disappointment.**  Upstream measures against a stock
WSJT-X build; ours is already `-march=native -O3`, which is a large part of
that gap.  The arithmetic is consistent: the native WSJT-X build was
measured at ~3.7x the stock Debian package (0.73 s vs 2.71 s on a real
window, 2026-07-22), and 10 / 3.7 ≈ 2.7 — close to the 2.3-2.5x measured
here.  That is an inference from two separate measurements, not a
controlled experiment; the honest claim is "2.5x over what we ship today".

2.5x on `wsprd` and 1.7x on FT8 is still the headroom rob asked for, and it
arrives with a build that drops Qt, boost, hamlib, portaudio and gfortran.

Two faults surfaced only because that real run happened, not from the unit
tests: a `sudo` clone that made the *next* build impossible by creating a
root-owned `/opt/git/sigmond`, and an adoption step that left no decoder on
PATH between moving the old binaries and linking the new ones.  Both fixed,
both now guarded by tests.

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

**psk-recorder's `jt9_decode` now gets the UberSDR `jt9`.**  This was
recorded here as the one live regression risk.  ✅ **Closed on AI6VN
2026-10-24** — and the answer was not the one expected; see "On a station"
below.  Two things turned out to be true at once: stations do not use this
path at all (`decoder_kind = "decode_ft8"` is the configured default), and
when it is turned on it fails *identically on both builds*, for a reason
that has nothing to do with the decoder.  The original reasoning is kept
below because the shared-memory evidence it weighs is what the interop test
went on to confirm.  `core/slot.py:594` derives the
decoder as the *sibling of the wrapper* —
`jt9_bin = Path(wrapper).with_name("jt9")` — so with `jt9_decode` at
`/usr/local/bin/jt9_decode` it resolves `/usr/local/bin/jt9`, which is now
our symlink.  FT8 and FT4 on every DASI2 station run through that path.

The evidence says it works, and it is better evidence than "upstream claims
drop-in":

- UberSDR's `src/jt9_shm.cpp` implements `jt9 -s` as *"lib/jt9a.f90 at
  WSJT-X v2.7.0, its loop exactly"*, over the same `dec_data_t`.
- `src/qt_shm.h` reproduces Qt 5's `QSharedMemory` System V wire format —
  native name, key file, `ftok`, the `qipc_systemsem_` lock — and its own
  header says it **came from jt9-decode's `tests/test_shm_interop.cpp`**
  (`qt_shm_compat.h`) and is verified against Qt 5.15.
- The direction matches ours exactly: the Qt side (`jt9_decode`) creates the
  segment, the UberSDR `jt9` attaches to it.

The residual risk is that **our `jt9_decode` is a *triggered-mode fork*** of
madpsy/jt9-decode, and a fork can have moved the protocol.  So this is
strong evidence, not a proof, and it has not been exercised against our fork.

✅ **That pre-deployment check ran on AI6VN on 2026-10-24** — see the
next section.  The fallback it names was exercised too, live, in both
directions.

**Two upstreams instead of one.** We now track `madpsy/ubersdr_wsprd` as well
as `WSJTX/wsjtx`.  Both get pinned commits and both appear in the manifest.

## On a station: AI6VN, 2026-10-24

rob's lab box (DASI-009, image v3.78, Ryzen 5 5560U, almost no antenna but a
live receiver).  The four commits were cherry-picked onto the box's own
sigmond commit (`f362461`) and delivered as a git bundle, so what ran there
was the decoder change against exactly the code the box already had — not
`main` plus a sink release.  Branch `ai6vn-decoder-test` in
`/opt/git/sigmond/sigmond`; the box is still on it.

**What the box was before:** the pre-split layout — real `wsprd` and `jt9` at
`/usr/local/bin`, no `decoders/` directory.  So this is the first real run of
`_adopt_legacy_decoders`, which until now existed only under test.

**The build.**  `_build_decoders()` end to end, unattended:

| step | result |
|---|---|
| adopt the legacy binaries | both moved into `decoders/wsjtx/`, **sha256 unchanged**, symlinked back |
| wsjtx build | skipped — adopted marker already matched the pin.  No Qt rebuild. |
| `jt9_decode` | already installed, source unchanged |
| arch probe | `avx2 fma bmi2` present → `-march=x86-64-v3`, chosen not assumed |
| clone | 182 MB, ~4 min, unprivileged, tree stayed `sigmond:sigmond` |
| parity: wsprd | **194 cases as WSJT-X's stored outputs, 0 differing** |
| parity: jt9 | **222 cases (119 FT8, 73 FT4, 19 FT2), 0 differing** |
| select | `ubersdr`, matching `coordination.toml` |

**The swap is live-safe.**  Adoption moved the live binaries at 13:42 while
`wspr-recorder` was decoding.  The cycles either side of it (13:42, 13:44,
13:46) each produced spots.  The link-back-immediately fix is what makes that
true, and it was observed rather than argued.

**WSPR on the new `wsprd`, live:** selected 13:51; 12 cycles to 14:14 gave 14
spots, 1.17/cycle, against a 7-hour baseline of 0.67-1.13/cycle on the old
one.  At or above baseline, no errors, 17/17 bands reporting noise.

**The `jt9_decode` interop test — the thing the plan was waiting for.**  Run
offline against the repo's own recordings, both builds driven through *our*
`jt9_decode` over Qt5 shared memory, same files, same flags:

| mode | files | wsjtx decodes | ubersdr decodes | verdict |
|---|---|---|---|---|
| FT8 | 69 | 425 | 425 | 3 lines differ of 425 |
| FT4 | 19 | 44 | 44 | **identical** |
| FT2 | 31 | 0 | 192 | v3.0.2 decodes no FT2 here at all |

(jt9's own `HHMMSS` label is excluded — psk-recorder discards it, and the two
arms ran seconds apart.)  The three FT8 differences are one reordering, one
decode at 997 Hz vs 998 Hz, and one at -4 dB vs -5 dB.  None is a defect:
UberSDR is byte-identical to **WSJT-X 3.2.0-rc1** and our fallback is pinned
to **v3.0.2**, so these are two WSJT-X versions disagreeing, not a rewrite
disagreeing with its original.  FT2 is the same story at full volume — the
recordings are 3.2.0's FT2, which 3.0.2 cannot decode.

So: **our triggered-mode fork of `jt9_decode` drives UberSDR's `jt9`
correctly.**  That risk is closed.

**⛔ But psk-recorder's `jt9` decoder path does not work on this box at all —
with EITHER build.**  Switching `decoder_kind` to `"jt9"` and restarting gave
19 resident `jt9_decode` processes, **zero** `jt9` children, 105 restarts in
two minutes and `decodes=0/14`.  Flipping the symlink to the wsjtx build
changed nothing: 67 restarts a minute, still zero `jt9`.  The decoder is not
the variable.

Reproduced outside psk-recorder, under a replica of its own unit sandbox:

    Failed to create shared memory:
    QSharedMemoryPrivate::initKey: unable to set key on lock

`psk-recorder@.service` sets `ProtectSystem=strict` with `PrivateTmp=false`
and does not list `/tmp` in `ReadWritePaths`.  Under `strict` the whole
hierarchy is read-only but for `/dev`, `/proc` and `/sys` — so `/tmp` is
read-only, and Qt 5's `QSharedMemory` cannot create the `ftok` key file its
System V lock needs.  Adding `/tmp` to `ReadWritePaths` in that replica makes
it decode immediately.  The same command run outside the sandbox, as
`pskrec`, from psk-recorder's own read-only working directory, always worked
— which is why this never showed up anywhere but under systemd.

This is a **pre-existing psk-recorder defect, not a regression**, and it is
evidence the path has never run: the unit even carries a sizing comment about
"19+ concurrent jt9 children at ~60 MB RSS each", so somebody provisioned for
it without getting it up.  (Measured here: wsjtx `jt9` peaks at 54 MiB, the
UberSDR one at 27 MiB — half.)  Fleet exposure is nil, because every station
is configured `decoder_kind = "decode_ft8"`.  Raised for psk-recorder's
owner; not fixed here, because it is a separate repo and a separate change.

**The fallback switch, live and both ways.**  `smd admin decoders --select
wsjtx` then `--select ubersdr`, each under a second, no rebuild, the station
decoding afterwards either way.

**Left behind on the box:** `/opt/git/sigmond/sigmond` is on branch
`ai6vn-decoder-test`, not `main`.  Leave it there until this is pushed, then
`git checkout main && git pull`.  Going back early is worse than leaving it:
the old `_build_wsjtx_decoders` installs real files to `/usr/local/bin` and
would overwrite the symlinks, and the old `smd` has no `admin decoders` and
no decoder findings in `doctor`.

## What is left

- **Push.**  The work is committed on main locally and deliberately not
  pushed: it changes what every station decodes with, and
  `smd component update` would carry it.  Rebased cleanly onto mjh's
  sink-2b-part2 main (`8134d4b`) on 2026-10-24; suite is 5306 passed, 0
  failed, against 5264/0 on that main — the 7 `test_sink_paths_contract` /
  `test_sink_state` errors are present on clean `origin/main` too.  Held at
  mjh's request until his v3.80 rig update test finishes, since it targets
  `origin/main`.
- **AI6VN's checkout** goes back to `main` once this is pushed (above).

## Open questions for rob

- Pin to a release tag or a commit?  The repo publishes a rolling `latest`
  release with prebuilt binaries; we build from source, so a commit pin is
  the honest choice — but it needs a human to move it.
- Should `wsprd -n` (no drift search, plus spread) be adopted?  It is the one
  behaviour the new decoder adds, and it is not a drop-in question: it would
  change what we report.  Default off until asked for.
