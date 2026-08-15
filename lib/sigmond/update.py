"""Plan a component update from observed state — safe to re-run.

Updating DASI002 on 2026-08-15 took eight manual steps and hit six
traps, every one of them found only when a command failed partway
through.  At twenty units that is untenable, and it is where mistakes
get made.

This module turns that sequence into a plan derived from observed
state, so re-running is a no-op and each trap is a check rather than a
surprise.  The planner is deliberately pure — state in, ordered actions
out — so the ordering and, more importantly, the REFUSALS are testable
without a host.

The traps, and how each is encoded:

* **service-user ownership** — component checkouts belong to `timestd`,
  `wsprrec` and friends, and the login user has no sudo.  Every pull
  carries the owner to run as.  (Repairing the historic root-owned
  damage is `smd doctor --fix`; this planner assumes it has run.)
* **a dirty tree may be a real fix** — DASI002's was: StartLimit keys
  moved out of `[Service]`, where systemd silently ignores them.  The
  plan REFUSES that component and says to diff it against the incoming
  version.  It never discards, and it never blocks the other
  components.
* **`deploy.sh` does not re-resolve siblings** — it runs
  `pip install -e .` for the consumer alone, so a venv holding a private
  copy of ka9q-python stays stale through any number of pull+deploy
  cycles.  `install.sh` (uv sync, which honours `[tool.uv.sources]`) is
  the repair.
* **`config_paths.h` is not regenerated when HEAD moves** — rebuild
  without removing it and the new binary reports the OLD commit hash,
  quietly defeating the provenance it exists to provide.
* **wisdom coverage** — radiod plans `WISDOM_ONLY|PATIENT` and falls
  back to `FFTW_ESTIMATE` on a miss: fast to plan, suboptimal forever,
  invisible in startup time.  `fft.log` is written only on a miss.
* **restart order** — radiod first, recorders after it is stable; a
  recorder started against a restarting radiod picks up a bad anchor.

Every action carries a `verify`, because today's failures were uniformly
of the form "looked fine, wasn't done" — a deploy that restarted
nothing, a checkout at the right commit whose venv imported something
else.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Action:
    """One step, with how to confirm it actually happened."""

    kind: str
    target: str
    detail: str = ''
    run_as: Optional[str] = None
    verify: str = ''


@dataclass
class Refusal:
    """A component deliberately left alone, and why."""

    target: str
    reason: str
    kind: str = 'refuse'


# Ordering is load-bearing, not cosmetic: sources land before they are
# installed, radiod is rebuilt before wisdom is planned against it, and
# everything settles before services are bounced.
_ORDER = ('pull', 'install', 'rebuild-radiod', 'wisdom', 'restart')


def plan_update(state: dict) -> list:
    """Return ordered actions (and refusals) to bring a host current.

    An already-current host yields an empty plan — that is what makes
    the command safe to re-run and safe to schedule.
    """
    actions: list = []
    refusals: list = []
    needs_restart = False

    repos = state.get('repos') or {}
    skew = set(state.get('venv_skew') or [])
    to_install = set()

    for name in sorted(repos):
        info = repos[name] or {}
        dirty = info.get('dirty') or []
        if dirty:
            # Never discard: on DASI002 this was a real uncommitted fix.
            refusals.append(Refusal(
                name,
                f'{len(dirty)} modified file(s) ({", ".join(dirty[:3])}) — '
                f'diff against origin/main before discarding; the local '
                f'change may be a fix that never got committed'))
            continue
        if info.get('behind'):
            actions.append(Action(
                'pull', name,
                detail=f'{info["behind"]} commit(s) behind upstream',
                run_as=info.get('owner'),
                verify='git rev-parse HEAD == origin/main'))
            to_install.add(name)
            needs_restart = True

    # A skewed venv needs install.sh even when the source is current.
    for name in sorted(skew | to_install):
        actions.append(Action(
            'install', name,
            detail='run scripts/install.sh (uv sync honours '
                   '[tool.uv.sources]); NOT deploy.sh, which re-installs '
                   'the consumer alone and leaves sibling copies stale',
            run_as='root',
            verify='<venv>/bin/python -c "import ka9q,os;'
                   'print(os.path.dirname(ka9q.__file__))" resolves to the '
                   'shared checkout, not the venv'))
        needs_restart = True

    radiod = state.get('radiod') or {}
    if radiod.get('pin') and radiod.get('running') is None:
        # A parse failure is not a finding.  B4 reported `running None`
        # because radiod prints its banner to STDERR, and that masqueraded
        # as "needs rebuild".  Say so instead of guessing.
        refusals.append(Refusal(
            'ka9q-radio',
            'could not read the running radiod version — refusing to guess '
            'whether a rebuild is needed (the banner goes to stderr)'))
    elif (radiod.get('pin') and radiod.get('running') != radiod.get('pin')
            and not radiod.get('contains_pin')):
        actions.append(Action(
            'rebuild-radiod', 'ka9q-radio',
            detail=f'running {radiod.get("running")} != pin '
                   f'{radiod["pin"]}; rm config_paths.h main.o first — it '
                   f'is not regenerated when HEAD moves, so the binary '
                   f'would embed a stale commit hash',
            run_as='root',
            verify='readlink -f /proc/<pid>/exe and radiod --version report '
                   'the pinned commit — check the RUNNING binary, not the '
                   'file installed (a drop-in may override ExecStart)'))
        needs_restart = True

    misses = state.get('wisdom_misses') or []
    if misses:
        actions.append(Action(
            'wisdom', 'ka9q-radio',
            detail=f'{len(misses)} plan(s) falling back to FFTW_ESTIMATE: '
                   f'{" ".join(misses)} — fft-gen --patient -T <fft-threads>',
            run_as='root',
            verify='wc -l /var/lib/ka9q-radio/fft.log == 0'))
        needs_restart = True

    if needs_restart:
        actions.append(Action(
            'restart', 'services',
            detail='radiod first, then the recorders once it is stable — a '
                   'recorder started against a restarting radiod picks up a '
                   'bad anchor',
            run_as='root',
            verify='systemctl is-active for each unit; journal free of '
                   'errors'))

    actions.sort(key=lambda a: _ORDER.index(a.kind))
    return refusals + actions
