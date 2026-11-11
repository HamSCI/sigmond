"""SystemView — the merged picture the harmonize rules see.

Assembled once per command invocation from:
  - the sigmond coordination config
  - every enabled client adapter's read_view()

Rules then operate purely on the SystemView and never touch the
filesystem themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .clients import load_adapter
from .clients.base import ClientView
from .coordination import Coordination, load_coordination
from .environment import EnvironmentView
from .paths import COORDINATION_PATH, RADIO_CONF_DIR
from .sdr_map import conf_adc_samprate_hz
from .topology import Topology, load_topology


@dataclass
class SystemView:
    coordination: Coordination
    topology: Topology
    client_views: dict = field(default_factory=dict)    # component name -> ClientView
    environment: Optional[EnvironmentView] = None       # populated only by `smd admin environment`

    def is_enabled(self, component: str) -> bool:
        return self.topology.is_enabled(component)

    def client(self, name: str) -> Optional[ClientView]:
        return self.client_views.get(name)

    def all_instances(self):
        """Iterate (client_type, instance) across all client views."""
        for cv in self.client_views.values():
            for iv in cv.instances:
                yield cv.client_type, iv


def resolve_radiod_samprates(coord: Coordination,
                             conf_dir: Path = RADIO_CONF_DIR) -> None:
    """Set each local radiod's samprate_hz to the rate its conf runs.

    The conf is what radiod reads, so it wins over a `samprate_hz` in
    coordination, which an operator's later hand edit of the conf leaves
    stale.  The conf is `radio_conf`, else radiod@<id>.conf.  When neither
    gives a rate, the declared value stands; 0 means unknown, and the
    frequency_coverage rule then says it checked nothing."""
    for rid, radiod in coord.radiods.items():
        if not radiod.is_local:
            continue
        path = (Path(radiod.radio_conf) if radiod.radio_conf
                else conf_dir / f"radiod@{rid}.conf")
        try:
            hz = conf_adc_samprate_hz(path.read_text())
        except OSError:
            continue
        if hz:
            radiod.samprate_hz = hz


def build_system_view(topology: Optional[Topology] = None,
                      coordination: Optional[Coordination] = None) -> SystemView:
    """Load everything needed to run harmonize rules."""
    topo = topology or load_topology()
    coord = coordination if coordination is not None else load_coordination(COORDINATION_PATH)
    resolve_radiod_samprates(coord)

    client_views: dict = {}
    for comp in topo.enabled_components():
        # load_adapter consults the bespoke REGISTRY first, then falls
        # through to a generic ContractAdapter for any catalog entry with
        # `contract` set — that's how Wave 2 clients get a ClientView
        # without a per-name adapter module.
        adapter = load_adapter(comp)
        if adapter is None:
            continue
        cv = adapter.read_view()
        # Keep the topology component name as the dict key so downstream
        # code can re-ask "is 'grape' enabled?" without knowing the
        # adapter's canonical .name.
        client_views[comp] = cv

    return SystemView(coordination=coord, topology=topo, client_views=client_views)
