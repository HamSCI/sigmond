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

Entries:
- `wisdomf-amd-ryzen-7-5825u` — AMD Ryzen 7 5825U (B4, 2026-07-23)
