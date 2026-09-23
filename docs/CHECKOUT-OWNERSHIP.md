# Who owns a checkout

**Status:** design, 2026-09-23. Written after the fifth recurrence.
**Constraint that shapes everything below:** a client must still run on its
own, cloned by someone who has never heard of sigmond.

## The thing that keeps happening

On 2026-09-23 the doctor reported 117 paths in `hamsci-physics` on AC0G-B4
owned by `timestd`, thirty-six of them under `.git/objects`, with mtimes
from the deploys of 09-05 and 09-06. ND carried 109 root-owned paths in
`ka9q-radio`. Neither surprised anyone. The same fault had already appeared
on DASI-009.AI6VN twice in two days on two different images, on DASI002
where a root-owned `.git/index` reappeared the instant `smd doctor --fix`
repaired it, and in an earlier case the doctor's own comments record at
2996 paths.

Five occurrences, four components, four stations. Each one got its own fix.
The fault kept returning, which tells you the fixes addressed instances
rather than the cause.

## What actually causes it

One directory tree, `/opt/git/sigmond/<name>`, does three jobs. Each job
implies a different natural owner.

| The tree serves as | which wants to be owned by | measured on 2026-09-23 |
|---|---|---|
| the git source of truth for updates | one identity, consistently `sigmond` | `git pull` breaks outright on split ownership |
| the running artifact | the service account | **29** units `ExecStart` out of the checkout, under **8** non-sigmond identities |
| the build and venv workspace | whoever builds | **10** venvs live *inside* the checkouts |

Nine identities write into a tree that exactly one identity must own. No
mechanism enforces who may write. The rule lives only in the install
scripts, so every script becomes a fresh chance to forget — and the bug
found today was a `chown` remembered for the venv and omitted for the
sibling clone **six lines apart**.

ND demonstrates the split right now: nine venvs owned by `sigmond`, and
`wspr-recorder/venv` owned by `wsprrec`.

## Why the previous fixes did not hold

Every remedy so far acts *after* the write lands.

- A `chown` at the end of an installer helps only where someone remembered.
- `smd doctor --fix` repairs, and the next service start recreates.
- `OWNERSHIP_SKIP_DIRS` skips `venv` and `.venv`.

That last one deserves attention, because it tells the truth plainly. Its
comment concedes that a venv "legitimately belongs to its SERVICE user."
The tree therefore holds two owners by design, and we answered that by
teaching the detector to look away. A checker configured to ignore the
condition it exists to find reports nothing when the condition spreads.

Two of the remedies make matters worse. The skip list hides the venvs. And
`git status` run as root rewrites `.git/index`, so on DASI002 the damage
returned the moment the repair finished — the diagnostic caused the fault
it had just cleared. `--no-optional-locks` now blunts that, which treats a
symptom of the same confusion.

## The rule

An earlier draft of this note proposed moving the venvs out of the
checkouts, to `/opt/sigmond/<name>/venv`. That proposal fails, and the way
it fails clarifies the problem. It writes sigmond's name and a system path
into a client that must run without sigmond. A stranger cloning
`hf-timestd` should put a venv where Python users put venvs — in the
project. The layout belongs to the client; only the *deployment* belongs
to sigmond.

So the rule governs identity, not location:

> **A checkout has exactly one writer identity, and that identity is
> whoever OWNS the checkout — never whoever happens to run the script.**

Both worlds satisfy it without changing shape.

- **Standalone.** One person clones, owns, installs and runs. A single
  identity touches the tree, the rule holds for free, and the operator
  never learns it exists.
- **Managed.** `sigmond` owns the checkout, so every install and build step
  must adopt `sigmond` — not the invoking user, and not `$INSTALL_USER`.

`INSTALL_USER` keeps a real job: it names the account services *run* as,
and it owns everything **outside** the tree — `/var/lib`, `/dev/shm`,
config files, logs. It must never determine ownership of anything inside
the tree. Conflating the two produced the v3.40 split, and it produced
today's.

## What follows, in order

**1 · Each client's installer derives the owner once, at the top.**

```sh
OWNER=$(stat -c '%U:%G' "$PROJECT_DIR")
```

Every file the installer creates inside the tree takes `$OWNER`. Nothing
inside the tree takes `$INSTALL_USER`. `hf-timestd` commit `ff9cec9` already
does this for the sibling clone; the work promotes that one call site to
the rule, across every client that writes into a checkout.

Cost: about three lines per installer. Standalone installs see no change,
because there `$OWNER` resolves to the person running the script.

**2 · Stop the service accounts writing at all.**

Two writers remain after step 1, and both come from running rather than
installing.

- **Bytecode.** Exactly one of 48 units sets `PYTHONDONTWRITEBYTECODE=1`.
  The other 47 let a service account scatter `.pyc` files through a tree it
  does not own. Setting it costs one line per unit and costs a standalone
  user nothing.
- **In-tree C build output.** ND's `src/main.o` arrived root-owned because
  the build ran as root. Step 1 covers it once the build runs as `$OWNER`.

**3 · Make a violation fail where it happens.**

With steps 1 and 2 done, no legitimate writer remains inside a managed
checkout except its owner. Drop group and other write on the tree. A stray
write then fails immediately, names the process, and points at the line
that did it — instead of surfacing months later as "117 paths, mtimes from
three weeks ago, cause unknown."

This mirrors the rule the timing work already follows: expose the fault,
never silently correct it. It also enumerates every remaining violator,
which is what makes any further separation tractable rather than
speculative.

Hardening belongs to the deployment, never to the repo. Sigmond applies it;
a standalone clone never meets it.

**4 · Stop skipping venvs in the doctor.**

Once steps 1–3 hold, a venv owned by anyone but the checkout owner marks a
real regression. Remove `venv` and `.venv` from `OWNERSHIP_SKIP_DIRS` and
let the finding stand. Editor and build caches can stay skipped; they never
blocked an update.

## What this design refuses to do

It adds no timer that chowns the tree. That would make a fourth
after-the-fact remedy, it would race the writers exactly as `--fix` raced
`git status` on DASI002, and it would convert a visible fault into an
invisible one. A repair that runs on a schedule teaches everyone to stop
asking why the damage appears.

It also leaves the deeper separation — pristine checkout, artifact
installed elsewhere, state under `/var/lib` — for later. That separation
may well prove right. It touches every component, and it should not gate
three lines per installer and one line per unit.

## How you will know it worked

`smd doctor` on a station that has run for a month, updated at least once,
and restarted its services reports no ownership findings at all — not
findings suppressed by a skip list, but none produced. Until then the
question stays open, and this note stays a design.
