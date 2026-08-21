"""`smd admin manifest restore` — the remote-rollback half of the
blessed-manifest mechanism; `manifest_adopt`'s mirror.

Rollback today means a road trip: `smd component update` / `smd update`
only move a host forward, and the only fallback when a change turns out
bad is reimaging from a stick on-site. The blessed manifest (22 pinned
component SHAs, captured at release time — see `manifest_adopt.py`) is
already a complete restore point; this module turns "roll back to what
we blessed" into a station-inward, remote-safe, fail-closed operation
instead of a physical visit.

Where `plan_adopt` asks "does live already equal the manifest, so
recording it is a provable no-op?", `plan_restore` asks the opposite
question: "what would it take to MAKE live equal the manifest, and is
every one of those moves safe to make?" It is deliberately built the
same way — pure planning core (no filesystem, no process state), same
whole-diff refusal promise, same `None`-never-matches treatment of an
unusable manifest — so operating it feels like the same family as
`adopt`. The CLI wrapper in `bin/smd` (`cmd_manifest_restore`) owns
reading the manifest file, computing live components, fetching, the
dirty-tree check, and (on `--apply`) actually moving checkouts.

Post-apply verification re-runs `plan_restore` itself against the
recomputed live components (`resolvable` stubbed to always return
`False` — nothing should need a fresh checkout move immediately after
`--apply` just landed), demanding "ok AND every action is `'keep'`".
It deliberately does NOT reuse `manifest_adopt.plan_adopt` STRICT for
this, even though that was the first cut: `plan_adopt` refuses on any
live component absent from the manifest, so a perfectly successful
restore on a host carrying a sanctioned extra (a `RestorePlan.strays`
entry) would report verification FAILURE — contradicting this
module's own documented promise that strays are informational and
never a refusal. Re-running `plan_restore` states restore's actual
contract instead.

Shared behavior with `cmd_update` worth knowing about: the CLI
wrapper's `_owner_of` helper (used for every `runuser -u <owner>`
fetch/checkout) falls back to `'root'` when `Path.owner()` raises —
the same fallback `cmd_update`'s pull step uses for `step.run_as`.
On a real deploy tree this should never trigger (every checkout is
service-user-owned), so it is left matching `cmd_update` rather than
diverging into a refusal here; an ownership problem severe enough to
hit this path is `smd doctor`'s finding to report, not restore's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sigmond.doctor import _parse_manifest_components, _sha_equal


@dataclass
class RestorePlan:
    """The result of comparing a candidate (blessed) manifest against
    live component SHAs, to plan a ROLLBACK rather than an adoption.

    ``ok`` is True only when every component named in the manifest can
    be accounted for: already matching live, or resolvable to a real
    commit in its own checkout so a `git checkout --detach` can reach
    it. ``refusals`` holds one human-readable line per component that
    blocks the restore — the WHOLE diff, not just the first blocker, so
    an operator can fix everything in one pass rather than being sent
    back one component at a time (the same promise `plan_adopt` makes).

    ``actions`` holds one 4-tuple ``(name, action, from_sha, to_sha)``
    per manifest component, where ``action`` is ``'keep'`` (already at
    the manifest SHA — nothing to do) or ``'checkout'`` (needs to move;
    ``from_sha`` is the live SHA today, ``to_sha`` is the manifest SHA
    to land on). Components that refuse never appear in ``actions``.

    ``strays`` lists live components NOT named in the manifest, purely
    informational — restore reproduces the blessed baseline, it does
    not delete extras a host has picked up since. A stray is never a
    refusal and never touched, on either a dry-run or `--apply`.
    """

    ok: bool
    refusals: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    strays: list = field(default_factory=list)


def plan_restore(manifest_text: str, live_components: dict,
                 resolvable: Callable[[str, str], bool]) -> RestorePlan:
    """Fail-closed plan to roll live component checkouts BACK to a
    blessed manifest.

    Parses ``manifest_text`` with the SAME parser `smd doctor` and
    `plan_adopt` use (``sigmond.doctor._parse_manifest_components``),
    which returns ``None`` — not ``{}`` — for a manifest whose
    ``components (live):`` block is missing or too thin to trust (see
    ``sigmond.doctor.MIN_COMPONENT_ROWS``). A ``None`` parse never
    matches anything and always refuses with a single reason
    (``"manifest unusable: no trustworthy components block"`` — the
    same wording `plan_adopt` uses for the identical condition) rather
    than being treated as an empty-but-valid manifest.

    For each component NAMED IN THE MANIFEST (sorted, for a stable and
    reproducible plan):

    * absent from ``live_components`` (no checkout on this host to
      restore) — refusal: ``"<name>: in the manifest but no checkout to
      restore"``. Restore only moves checkouts that already exist; it
      never clones a new one (that is `smd install`'s job, on an
      already-imaged host this should not happen).
    * live SHA already ``_sha_equal`` to the manifest SHA — action
      ``'keep'``. Nothing to do; still recorded so the plan accounts
      for every manifest component.
    * otherwise, ``resolvable(name, manifest_sha)`` decides: if it
      returns ``True`` (the CLI wrapper answers this with
      ``git rev-parse --verify --quiet <sha>^{commit}}`` inside the
      component's own checkout, after a fetch — the SHA is a real,
      reachable commit there), action ``'checkout'``. If ``False`` —
      refusal: ``"<name>: manifest SHA <sha> not resolvable in the
      checkout (fetch first?)"``. ``resolvable`` is INJECTED so this
      function stays pure: it never runs git itself, and a caller that
      wants to fail closed on any git error (missing checkout, no
      network, corrupt repo) does so by having ``resolvable`` return
      ``False`` for that error — this function trusts what it's told,
      the same way `plan_adopt`'s ``superset_components`` trusts its
      caller's ancestry proof.

    Live components NOT named in the manifest are never visited by the
    loop above — they land in ``strays`` instead (see ``RestorePlan``),
    which is never a refusal and never touched: restore restores the
    baseline, it does not delete extras a host has picked up since
    (that is a separate, deliberate operator decision if ever wanted).

    The refusal list holds EVERY divergent/blocking component, not just
    the first found — an operator planning a restore needs the whole
    picture to decide whether to fetch more, fix a dirty tree, or
    accept that a component genuinely cannot be rolled back here.
    """
    manifest = _parse_manifest_components(manifest_text)
    if manifest is None:
        return RestorePlan(
            ok=False,
            refusals=['manifest unusable: no trustworthy components block'],
        )

    refusals = []
    actions = []
    for name in sorted(manifest):
        manifest_sha = manifest[name]
        live_sha = live_components.get(name)
        if live_sha is None:
            refusals.append(
                f'{name}: in the manifest but no checkout to restore')
        elif _sha_equal(live_sha, manifest_sha):
            actions.append((name, 'keep', live_sha, manifest_sha))
        elif resolvable(name, manifest_sha):
            actions.append((name, 'checkout', live_sha, manifest_sha))
        else:
            refusals.append(
                f'{name}: manifest SHA {manifest_sha} not resolvable in '
                f'the checkout (fetch first?)')

    strays = sorted(
        f'{name}: {live_components[name]}'
        for name in set(live_components) - set(manifest))

    return RestorePlan(ok=not refusals, refusals=refusals, actions=actions,
                       strays=strays)
