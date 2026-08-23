# Dev setup — build and test the suite

> **Audience:** contributor
> **Status:** current
> **Verified against:** sigmond 978c80a on 2026-08-23 — code + commands run in the checkout
> **Canonical for:** setting up and testing a development environment for the suite

This page gets a fresh clone of `sigmond` to a state where you can run `smd`,
run the test suite, and check the docs — nothing more. For *where* that
clone should live relative to a running station, read
[`CONTRIBUTING.md` §1](../../CONTRIBUTING.md#1-where-work-happens) first:
development happens on a workstation or devbox VM, never a reference
station or the build host.

## Prereqs

- **Python 3.11.** `pyproject.toml` sets `requires-python = ">=3.11"`;
  `.python-version` pins the interpreter to `3.11` for `uv`.
- **git.**
- **uv, optional but standard.** [astral.sh/uv](https://astral.sh/uv) is
  what every client repo's `install.sh` uses in production (`CLAUDE.md`
  §Fleet upgrade pattern); a `pip`/`venv` fallback exists (below) for a
  machine without it. Confirmed present here: `uv --version` → `uv 0.12.5
  (x86_64-unknown-linux-gnu)`.

## Clone layout

Sigmond and its sibling repos (`ka9q-python`, `ka9q-radio`, `hs-uploader`,
`wspr-recorder`, `psk-recorder`, `hf-timestd`, `mag-recorder`,
`meteor-scatter`, `gpsdo-monitor`, `hf-tec`/`hamsci-dsp`, `codar-sounder`,
`sigmond-appliance`, …) are cloned **flat, as siblings, under one parent
directory** — this checkout lives at `.../repos/sigmond` next to
`.../repos/ka9q-python`. That layout is not just convenience:
`pyproject.toml`'s `[tool.uv.sources]` resolves the editable dependency by
relative path —

```toml
[tool.uv.sources]
ka9q-python = { path = "../ka9q-python", editable = true }
```

— so `uv sync` (or `scripts/dev-setup.sh`'s pip fallback) only finds a local,
editable `ka9q-python` if it sits at `../ka9q-python` from this repo. Clone
it as a sibling, not nested inside `sigmond/` or somewhere unrelated.

**This is a *development* layout, not the one a running station uses.** A
station's checkouts live at `/opt/git/sigmond/<name>`, owned by that
client's service user, simultaneously a git working tree, install target,
and systemd unit's runtime source — see [`CONTRIBUTING.md` §8, "Deploy
trees are not workspaces"](../../CONTRIBUTING.md#8-deploy-trees-are-not-workspaces).
Don't treat a station's tree as a place to develop.

## Dev venv

`smd` itself needs nothing beyond the standard library (`CLAUDE.md`
§Developer commands), but the test suite and the TUI (`smd tui`) do. Those
extras live in a venv at `.venv/`, kept separate from the production venv
a station builds at `/opt/git/sigmond/sigmond/venv/`.

**uv path** (`README.md` §Development):

```bash
uv sync --extra tui --extra dev        # creates .venv/ with textual, rich, pytest
```

**pip fallback**, from `scripts/dev-setup.sh`'s text (not executed here —
it recreates the venv, and this checkout's `.venv/` is already live): `cd`s
to the repo root; if `uv` is on `$PATH`, `uv venv .venv` + `uv pip install
--python .venv/bin/python -e '.[tui,dev]'`, otherwise `python3 -m venv
.venv` + `.venv/bin/pip install -e '.[tui,dev]'`. Either branch then looks
for a sibling `ka9q-python` at `../ka9q-python`, `/home/mjh/git/ka9q-python`,
or `/opt/git/ka9q-python` (first match wins) and installs it editable too —
making `[tool.uv.sources]` sibling resolution concrete for plain pip. Safe
to re-run; it recreates `.venv` each time.

Both paths are driven by the same `pyproject.toml` extras: `tui` (textual,
rich, ka9q-python) and `dev` (pytest, textual, rich).

**Verification** (read-only; not a fresh `uv sync`): `.venv/bin/pytest
--version` → `pytest 9.0.3` confirms a dev venv exists here already, wired
up as `pyproject.toml` expects.

## Running `smd` from the dev tree

`bin/smd` is plain Python and runs straight from a checkout — no install
step required for the core (`CLAUDE.md` §Running `smd` from the dev tree
without reinstalling):

```bash
PYTHONPATH=lib ./bin/smd list           # uses your editable source tree
PYTHONPATH=lib ./bin/smd tui            # TUI also (needs .venv with [tui])
```

Verified here, read-only: `PYTHONPATH=lib SIGMOND_NO_VENV_REEXEC=1 python3
bin/smd version | head -3` →
```
image:      (unstamped)   [no image stamp on this host — predates the stamp, or was not installed from an appliance image]

components (live):
```

`SIGMOND_NO_VENV_REEXEC=1` matters once `/opt/git/sigmond/sigmond/venv/`
exists: production `smd` then re-execs into that installed venv's
interpreter on **every** invocation (not just `tui`), so the full
dependency closure including `ka9q-python` is always available to the
harmonization rules. The re-exec is skipped when that venv is absent (true
of a plain dev checkout), when `smd` is already running from it, or when
`SIGMOND_NO_VENV_REEXEC=1` is set — the escape hatch for a dev checkout
that sits next to, or is, an installed tree.

## Tests

`pyproject.toml` sets `testpaths = ["tests"]`. **`.venv/bin/pytest` is the
canonical runner** (`CLAUDE.md` §Tests) — not a bare `pytest`, which would
run outside the venv and miss the dev extras.

```bash
.venv/bin/pytest tests/          # or: uv run pytest tests/
```

Fixtures live in `tests/fixtures/` and `tests/conftest.py`, which prints a
loud stderr banner if `textual` is missing: without it every
`tests/test_tui_*.py` class self-skips via `@unittest.skipUnless(...)` and
the run still reports green — the gap that let the TUI suite go
unexercised in CI from June to August 2026 unnoticed. Installing the `tui`
extra (either dev-venv path above) makes the banner go away.

**What CI runs** (`.github/workflows/test.yml`): `pip install pytest
textual`, then `PYTHONPATH=lib python -m pytest tests/ -q` on Python 3.11.
Its comment records the trap: CI previously ran `python -m unittest
discover tests`, which collects only `unittest.TestCase` subclasses — every
module-level bare `def test_*()` function was silently skipped, hiding 247
of 1273 tests (all of `test_bringup.py`, `test_timing.py`,
`test_wizard_dispatch.py`, `test_instance.py`, `test_catalog_prune.py`,
`test_timing_show.py`), two of which were already failing for weeks.
`pytest` collects both styles; `unittest discover` only one. **Don't
revert CI to `unittest discover`.**

## Docs checks

Three checks exist today and run against this repo's `docs/`, `README.md`,
`CONTRIBUTING.md`, and `CLAUDE.md`:

- **`scripts/docs-linkcheck.py`** — stdlib-only relative-Markdown-link and
  `#anchor` checker; exits non-zero on the first broken link:
  `python3 scripts/docs-linkcheck.py docs README.md CONTRIBUTING.md
  CLAUDE.md`. Verified here (narrower scope, read-only): `python3
  scripts/docs-linkcheck.py docs README.md` → `docs-linkcheck: 0 broken
  link(s)` (exit 0).
- **`tests/test_docs_links.py`** — runs the same checker as a pytest test
  over the doc surface, plus unit tests of the checker itself.
- **`tests/test_docs_cli_table.py`** — asserts `orchestration.md`'s CLI
  table matches `bin/smd --help` / `bin/smd admin --help` exactly
  (verb-for-verb, both directions).
- **`docs-freshness`** *(being written)* — `scripts/docs-freshness.py` +
  `tests/test_docs_freshness.py`, a warn-only check on each page's
  `Verified against` line; not yet implemented.

```bash
.venv/bin/pytest tests/test_docs_links.py tests/test_docs_cli_table.py -q
```

## graphify

The suite keeps a cross-repo knowledge graph for orienting in code across
repo boundaries — see [`CONTRIBUTING.md` §11, "Using graphify"](../../CONTRIBUTING.md#11-using-graphify)
for how to query it and keep it current. Not restated here.

## Building native pieces

Some clients depend on native (C/C++/Fortran) binaries not available via
apt or PyPI (`dumphfdl`, `mag-usb`, `wsprd`/`jt9`, PHaRLAP). The
prebuilt-vs-build-from-source contract each client's `install.sh` follows
is [`native-binaries.md`](../native-binaries.md). Not restated here.

## The appliance rig and the build host

Turning a sigmond checkout into a bootable station image is a separate,
nested pipeline — a different repo (`sigmond-appliance`), run on a
different machine (B3's PM and a nested build/test VM), not part of this
dev setup. See [`sigmond-appliance/README.md`](https://github.com/HamSCI/sigmond-appliance/blob/main/README.md)
and [`appliance-boundary.md`](appliance-boundary.md) ★ for what the image
bakes vs. first boot vs. how a change reaches a station. Not restated.

## Shell and tooling traps

Before scripting anything around this workflow, read
[`CONTRIBUTING.md` §7, "Shell and tooling traps"](../../CONTRIBUTING.md#7-shell-and-tooling-traps)
— `grep -c` exiting 1 on no match under `set -e`, `systemd-run` not
inheriting `HOME`, `pgrep -f` matching its own invoking shell, a pipeline's
exit status being only its last command's, `ProxyJump` not inheriting
`-i`. None are sigmond-specific; all have cost real time here. Not
restated.

## See also

- [`orchestration.md`](orchestration.md) ★ — layers, verb→module map.
- [`appliance-boundary.md`](appliance-boundary.md) ★ — how a change reaches a station.
- [`client-authoring.md`](client-authoring.md) — writing a new client.
- [`README.md`](README.md) ★ — the contributor's table of contents.
