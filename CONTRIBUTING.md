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
| development | the devbox VM on B3 | writing code and running tests, as yourself |
| build + staging | B3's PM, and a nested VM on it (`test-nested-v3.sh`) | building images and validating an install before it ships |
| production | B4, DASI002 and the field units | collecting science data |

**B4 is a reference station, not a development machine.** Its value is
being representative of a real deployment; developing on it destroys
exactly that. (It currently carries `.vscode`, `.ruff_cache` and `dist`
directories that no deployed unit has — that is the drift we are trying
to avoid.)

**B3's PM is a hypervisor and the image build rig, not a development
machine — same argument, one level up.** Its value is being representative
enough to indicate whether an install image is complete; human accounts
and dev tooling on it would introduce the drift it exists to detect. It
deliberately has zero human accounts. Development happens on the devbox
VM it hosts, where `mjh` and `rob` each work as themselves — own SSH key,
own `gh` auth, own clone under `~/hamsci`. Before the devbox existed,
every login on B3 was `root`, Rob's included; `last` showed no other
account had ever been used, so git history could not attribute a change
to a person.

Reaching a fleet host from the devbox is a function of two separate
things — where it is, and which key it has actually authorized — and
getting the second one wrong looks exactly like a broken config while
naming the wrong host in the error. See `hamsci-ops/docs/fleet-ssh-access.md`
(private repo — site topology and fleet ssh aliases do not belong in this
public one).

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

## 6. Cutting a release

An appliance image moves through four rungs — **built → tested → blessed
→ rolled** — and nothing is blessed without evidence. Full detail lives in
`sigmond-appliance/docs/RELEASE.md`; this is the summary a contributor
needs to know the mechanism exists.

* `build-usb-v3.sh` derives the image version from the **git tag on
  `HEAD`** and refuses to build from a dirty tree. Before this, version
  was a positional argument nobody recorded — "v3.31" existed only inside
  a filename, with no way to reconstruct which commit produced a shipped
  image.
* The **component pin manifest** is generated from `smd version` inside
  the golden VM template at build time, never hand-written, and ships
  beside the image.
* `bless-release.sh <version> [--apply]` enforces seven gates (version
  format, tag reachable from `origin/main`, clean tree, verified image
  checksum, a real manifest, test evidence tied to *that* image, no
  pre-existing Release) and cannot publish without a human typing a
  confirmation at a terminal — it reads `/dev/tty` explicitly so a pipe
  or an automated caller can't satisfy it.
* The first Release is
  [v3.32](https://github.com/HamSCI/sigmond-appliance/releases/tag/v3.32):
  the record carries the manifest, the sha256, and the test verdict. The
  image itself stays on the artifact store — GitHub caps release assets
  at 2 GiB and images run ~4.9 GB.

**The gate earned its keep immediately.** A v3.32 build from a proper
tag, with a valid 22-component manifest and a verified checksum, shipped
a 127-byte `sigmond.tar.gz` containing a single broken symlink instead of
the 7.7 MB sigmond source tree — a rig-sibling directory had become a
symlink during a disk migration, and `tar` archived the link instead of
its contents. Every install from that image would have booted with no
sigmond payload. The gate refused to bless it.

## 7. Shell and tooling traps

None of these are sigmond-specific, and all of them have cost real time
on this project recently enough to write down.

* **`grep -c` prints `0` and exits 1 when it matches nothing.** Under
  `set -e`, a bare `VAR=$(grep -c ...)` aborts the script silently before
  any of your own error handling runs. `VAR=$(grep -c ... || echo 0)` is
  not the fix — it appends a second `0` line on top of the one `grep -c`
  already printed, so `$VAR` becomes `"0\n0"` and corrupts whatever row
  it's written into. Use `|| true`, not `|| echo 0`. This bit three
  separate times in two days, including corrupting 461 rows of a capacity
  dataset.
* **`systemd-run` does not inherit `HOME`**, and its transient unit
  defaults to `KillMode=control-group`. A process started with
  `-daemonize` (or any other double-fork) stays in that cgroup unless
  something explicitly moves it out — so the instant the driver script
  exits, systemd tears down the whole cgroup and SIGTERMs the "detached"
  child anyway. This destroyed post-mortem evidence mid-investigation and
  sent it down a wrong path before the cgroup teardown itself was found.
  Pass `-E HOME=...`, and give anything that must outlive the driver its
  own transient scope (`systemd-run --scope --collect`) rather than
  trusting `-daemonize` to escape.
* **`pgrep -f <string>` matches the shell running it.** It has reported a
  VM as still running when it had already stopped. Match on something the
  invoking shell can't contain, or use `ps -C <comm>`.
* **`ProxyJump` does not inherit `-i`** — the jump is a separate ssh
  process — and each fleet host authorizes keys independently, so
  reaching the jump host does not imply reaching anything behind it. See
  "Where work happens" above and `hamsci-ops/docs/fleet-ssh-access.md`.

## 8. Deploy trees are not workspaces

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

## 9. Verify the thing that runs, not the thing you installed

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

**As of Stage 3, `smd doctor` checks this for you.** It reports:

* **drift** — do this host's live component SHAs still match the
  manifest it was installed from (`/etc/sigmond-appliance/manifest.txt`,
  the same manifest the image shipped with — see §6)? This lets a field
  unit answer "am I what my image says I am" with no network access.
* **exec mismatch** — is each running service's `/proc/PID/exe` actually
  the binary its deploy tree provides? It reads `ExecStart` from the
  *base* unit file, not the merged effective value, so a drop-in override
  can't hide from it — the exact mechanism behind the radiod swap that
  was once silently a no-op.
* **venv skew** — across every declared editable sibling
  (`[tool.uv.sources]`), not just `ka9q-python`.
* **"cannot assess"**, reported distinctly from "healthy" — a host with
  no usable manifest (an older image, or B3's PM, which is not an
  appliance install) says so rather than reporting clean.

The manual commands above are still how you check by hand, and are what
`smd doctor` runs under the hood.

## 10. Using graphify

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

## 11. Pull requests

* Branch from `main`, small and focused.
* State what you observed, what you changed, and how you verified it.
  Commit messages here carry the *why* — read a few before writing one.
* CI must be green.
* If your change touches a pin, a systemd unit list, or an install
  script, say so explicitly — those are the changes that reach every
  field unit at once.

## 12. When something is wrong on a deployed unit

1. `smd doctor` on the unit — most problems name themselves.
2. Reproduce on staging, not on the unit.
3. Fix in the repo, with a test.
4. Roll to one unit first, watch it, then the rest.

Comparing a broken unit against B4 is a reasonable instinct, but it only
finds *differences*. It cannot find a defect both machines share — which
is precisely how a stale library sat undetected on both for a day.
