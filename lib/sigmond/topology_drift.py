"""Where the station's running clients disagree with topology.toml.

topology.toml states what the operator configured to run here.  Two kinds of
disagreement matter:

  running, not enabled  a unit runs that topology does not list.  sigmond
                        honours topology, so it renders that client's upload
                        pipelines nowhere and a bare `smd restart` skips it.
  enabled, not running  topology lists an installed client whose units all
                        stand stopped.

A catalog entry that declares ``services`` (hs-uploader) runs outside
topology by design, so it reports neither way.  An enabled component that is
not installed is `next_steps`'s to report, not this module's.

`smd status` and `smd check` both read :func:`topology_drift`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass(frozen=True)
class Drift:
    running_not_enabled: List[str] = field(default_factory=list)
    enabled_not_running: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.running_not_enabled or self.enabled_not_running)


def _has_units(name: str, alias: Optional[str]) -> bool:
    """True when the component declares a systemd unit (deploy.toml) or the
    lifecycle shim knows its units (radiod): only then can it 'run'."""
    from .component_state import _read_deploy_toml, _systemd_unit_names
    deploy = _read_deploy_toml(name)
    if deploy is not None:
        return bool(_systemd_unit_names(deploy))
    try:
        from .lifecycle import resolve_units
    except ImportError:
        return False
    for n in (name, alias):
        if not n:
            continue
        try:
            if any(not u.orphaned for u in resolve_units([n], [n])):
                return True
        except Exception:
            continue
    return False


def topology_drift(topology, catalog=None,
                   state: Optional[Callable] = None,
                   has_units: Optional[Callable[[str, Optional[str]], bool]] = None) -> Drift:
    """Compare every catalogued component's systemd state with topology.

    ``state`` (name, topology, alias -> ComponentState) and ``has_units``
    default to the real probes; the tests pass fakes."""
    if catalog is None:
        from .catalog import load_catalog
        catalog = load_catalog()
    if state is None:
        from .component_state import compute_state
        state = compute_state
    has_units = has_units or _has_units
    running, stopped = [], []
    seen = set()
    for name, entry in sorted(catalog.items()):
        name = getattr(entry, 'name', name)
        if name in seen or getattr(entry, 'services', ()):
            continue
        seen.add(name)
        alias = getattr(entry, 'topology_alias', None)
        st = state(name, topology, alias)
        if st.active and not st.enabled:
            running.append(name)
        elif st.enabled and st.installed and not st.active and has_units(name, alias):
            stopped.append(name)
    return Drift(running, stopped)
