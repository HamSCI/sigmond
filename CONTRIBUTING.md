# Contributing to the HamSCI / DASI2 suite

This is a small research project running instruments that collect data
nobody can collect again. The rules below exist because each one has
already cost us something. Where a rule can be checked by a machine, it
is — conventions that rely on memory decay, and we have the scars to
prove it.

If you read nothing else: **never hand-fix a production box and leave it
there.**

---

## 1. Where work happens

| role | machine | what it is for |
|---|---|---|
| development | your workstation / B3 | writing code and running tests |
| staging | a nested VM on B3 (`test-nested-v3.sh`) | validating an install before it ships |
| production | B4, DASI002 and the field units | collecting science data |

**B4 is a reference station, not a development machine.** Its value is
being representative of a real deployment; developing on it destroys
exactly that. (It currently carries `.vscode`, `.ruff_cache` and `dist`
directories that no deployed unit has — that is the drift we are trying
to avoid.)

## 2. The repository is the source of truth — no machine is

Every host must be reconstructible from a version. Any difference
between a host and its declared version is a defect.

* Fix in the repo, cut a version, roll it out. Not: fix on a box and
  copy it around.
* **Push before you leave a box.** In August 2026 ten commits sat only
  on B4 — a machine with no GitHub credentials, so the work could not
  have been pushed from where it was written. `smd doctor` reports
  unpushed commits; run it.
* Emergency hot-fixes on production are allowed to restore service, but
  land in the repo the same day.

## 3. Updating a host

```bash
smd version         # what is actually installed here?
smd doctor          # what is wrong here?
smd doctor --fix    # repairs ownership only — never touches your edits
smd update          # what would change? (dry run)
smd update --apply  # do the mechanical steps
```

`smd update` is idempotent: a current host produces an empty plan, so it
is safe to re-run and safe to schedule. It refuses rather than
discarding when a local change collides with an incoming one — on
DASI002 that local change was a real uncommitted fix.

`smd version` reports the image string as *lineage* and the component
commits *live from the checkouts*. Do not read
`/etc/sigmond-appliance/version` on its own: firstboot writes it once and
nothing updates it, so after an in-place update it states something
false. DASI002 read `v3.20` while running v3.31-era components.

Never prefix `smd` with `sudo`; it elevates itself.

## 4. Tests

```bash
PYTHONPATH=lib python3 -m pytest tests/      # sigmond
uv run pytest                                # the client repos
```

The suite is the main asset — over 1200 tests in sigmond alone. A change
without a test that would have failed before it is not finished.

**Write the test first and watch it fail.** Several bugs this project
has shipped were "tested" only after the fact, and the tests passed
against the bug.

## 5. Pins and versions

Changing what version of something we build is a deliberate act, never a
side effect.

* `ka9q-radio` is pinned by **two files that must agree** —
  `ka9q-python/ka9q/compat.py` (the importable constant, preferred) and
  `ka9q-python/ka9q_radio_compat` (the text fallback). `sync_types.py
  --apply` writes both. Editing one and not the other looks like a fix
  and changes nothing.
* **A pin must be reachable from the remote we clone.** sigmond clones
  *upstream* `ka9q/ka9q-radio`, so a fork-only commit makes the image
  unbuildable. `sync_types.py` now refuses to write such a pin.
* A host may legitimately run a *superset* of the pin. Test containment,
  not equality.

## 6. Deploy trees are not workspaces

Component checkouts on a host are owned by their **service users**
(`timestd`, `wsprrec`, …) and are simultaneously a git working tree, an
install target, and the runtime source. That conflation is the source of
most of our operational pain.

* Run git as the checkout's owner, or via `smd`, which handles it.
* After changing a shared library, verify **inside the consumer's venv**
  — a checkout at the right commit proves nothing about what a service
  imports:
  ```bash
  <venv>/bin/python -c "import ka9q, os; print(os.path.dirname(ka9q.__file__))"
  ```
* Repair a consumer with its **`install.sh`** (uv sync, which honours
  `[tool.uv.sources]`), not `deploy.sh`, which re-installs the consumer
  alone and leaves stale sibling copies in place.
* Never `git add -A` in a deploy tree. A stray test artifact committed
  that way broke `git pull` on a field unit.

## 7. Verify the thing that runs, not the thing you installed

Nearly every failure this project has had looked like success:

* a deploy that reported a service bounce and restarted nothing;
* a rebuilt binary that reported the previous commit;
* a completeness metric that returned 100% by construction;
* a checkout at the right commit whose venv imported something else.

So check the running artifact:

```bash
readlink -f /proc/$(systemctl show UNIT -p MainPID --value)/exe
systemctl show UNIT -p ExecStart      # a drop-in may override it
```

## 8. Using graphify

The suite has a cross-repo knowledge graph at
`/root/appliance/repos/graphify-out/`, covering all the component repos.

* **Orientation.** `graphify query "<question>"`, `graphify explain
  "<symbol>"` and `graphify path "<A>" "<B>"` answer "where does this
  live and what touches it" across repo boundaries, which is exactly the
  question a newcomer has and which grep answers badly. `wiki/index.md`
  is the readable entry point; `GRAPH_REPORT.md` is for broad
  architecture review.
* **It is an aid, not a gate.** Use it when it helps; nobody needs to
  route an IDE search through it. (AI assistants working here are asked
  to query it before grepping, because it is far more context-efficient
  for them — see `CLAUDE.md`. That instruction is for agents, not a
  requirement on humans.)
* **Keep it current.** After changing code:
  ```bash
  cd /root/appliance/repos && graphify update .
  ```
  Two traps, both of which have bitten:
  * **Run it from the repos root**, never inside a single repo —
    running it in a subdirectory silently creates a second, repo-scoped
    graph there instead of updating the real one.
  * `update` **never drops deleted files**. After removing or renaming
    anything, do a clean rebuild or the graph keeps answering with code
    that no longer exists.

## 9. Pull requests

* Branch from `main`, small and focused.
* State what you observed, what you changed, and how you verified it.
  Commit messages here carry the *why* — read a few before writing one.
* CI must be green.
* If your change touches a pin, a systemd unit list, or an install
  script, say so explicitly — those are the changes that reach every
  field unit at once.

## 10. When something is wrong on a deployed unit

1. `smd doctor` on the unit — most problems name themselves.
2. Reproduce on staging, not on the unit.
3. Fix in the repo, with a test.
4. Roll to one unit first, watch it, then the rest.

Comparing a broken unit against B4 is a reasonable instinct, but it only
finds *differences*. It cannot find a defect both machines share — which
is precisely how a stale library sat undetected on both for a day.
