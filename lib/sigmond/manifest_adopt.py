"""`smd admin manifest adopt` — let an already-running host adopt a
blessed manifest in place.

The blessed-manifest pipeline (`build-usb-v3.sh` / `build-golden-vm.sh`
+ firstboot) only installs `/etc/sigmond-appliance/manifest.txt` on
hosts IMAGED from a release. Live hosts (b4, dasi002, ...) are never
reimaged, so they need a way to adopt a manifest fetched after the
fact — but only when doing so is provably a no-op for drift detection:
the host's live component SHAs must already match the manifest
exactly. Anything else is refused; this module never guesses or
partially applies.

``plan_adopt`` is the pure planning core (no filesystem, no process
state) — the CLI wrapper in ``bin/smd`` owns reading the manifest file,
computing live components, and (on ``--apply``) writing
`/etc/sigmond-appliance/manifest.txt`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sigmond.doctor import _parse_manifest_components, _sha_equal


@dataclass
class AdoptPlan:
    """The result of comparing a candidate manifest against live
    component SHAs.

    ``ok`` is True only when every component present in EITHER side
    matches the other exactly (per ``_sha_equal``'s shared-prefix
    rule). ``refusals`` holds one human-readable line per divergent
    component — the whole diff, not just the first mismatch, since an
    operator adopting a manifest by hand needs the full picture to fix
    it rather than being sent back one component at a time.
    ``matches`` holds one line per component that already agrees.
    """

    ok: bool
    refusals: list = field(default_factory=list)
    matches: list = field(default_factory=list)


def plan_adopt(manifest_text: str, live_components: dict) -> AdoptPlan:
    """Fail-closed comparison of a candidate manifest against the live
    component SHAs already installed on this host.

    Parses ``manifest_text`` with the SAME parser `smd doctor` uses
    (``sigmond.doctor._parse_manifest_components``), which returns
    ``None`` — not ``{}`` — for a manifest whose ``components (live):``
    block is missing or too thin to trust (see
    ``sigmond.doctor.MIN_COMPONENT_ROWS``). A ``None`` parse never
    matches anything and always refuses with a single reason
    (``"manifest unusable: no trustworthy components block"``) rather
    than being treated as an empty-but-valid manifest, which would
    otherwise report every live component as a spurious "manifest
    only" refusal instead of naming the real problem.

    Fails closed on every other divergence too: a component named in
    the manifest but absent from ``live_components``, a component
    installed live but absent from the manifest, and a SHA mismatch
    (compared via ``sigmond.doctor._sha_equal`` — manifests carry
    mixed-length short SHAs, so exact string equality is the wrong
    test) are ALL refusals. An empty ``live_components`` against a
    valid (non-empty) manifest refuses on every manifest entry — there
    is no code path that adopts a manifest this host cannot corroborate
    against something it actually has installed.

    The docstring promise that matters most operationally: a refusal
    lists EVERY divergent component in ``refusals``, not just the
    first one found, because the operator adopting a manifest by hand
    needs the complete diff to decide whether to fix the host or the
    manifest — not one line at a time.
    """
    manifest = _parse_manifest_components(manifest_text)
    if manifest is None:
        return AdoptPlan(
            ok=False,
            refusals=['manifest unusable: no trustworthy components block'],
            matches=[],
        )

    refusals = []
    matches = []
    for name in sorted(set(manifest) | set(live_components)):
        manifest_sha = manifest.get(name)
        live_sha = live_components.get(name)
        if manifest_sha is None:
            refusals.append(
                f'{name}: installed live ({live_sha}) but not in the manifest')
        elif live_sha is None:
            refusals.append(
                f'{name}: in the manifest ({manifest_sha}) but not installed live')
        elif not _sha_equal(live_sha, manifest_sha):
            refusals.append(
                f'{name}: SHA mismatch — manifest {manifest_sha} != live {live_sha}')
        else:
            matches.append(f'{name}: {live_sha}')

    return AdoptPlan(ok=not refusals, refusals=refusals, matches=matches)
