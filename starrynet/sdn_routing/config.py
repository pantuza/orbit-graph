"""SDN routing configuration."""

from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class SdnConfig:
    """Parameters for the SDN control plane."""

    constellation_size: int
    node_count: int
    delay_dir: str
    metrics_dir: str
    link_threshold: float = 0.01
    """Treat matrix entries above this value (ms) as adjacency."""

    reinstall_on_delay_update: bool = True
    """
    On link-delay ticks: recompute FIB from the delay matrix.
    Routes are pushed only when next-hops change (production-like).
    Set False to skip delay ticks entirely (fast basic runs).
    """

    incremental_install: bool = True
    """
    On routing events (topology_change, damage_recovery): refresh address caches
    to learn the new topology, but KEEP the installed route baseline so only the
    next-hops that actually changed are pushed (make-before-break, production-
    like). This is what an SDN controller does -- it ships deltas, not the whole
    table. Set False to force a full reinstall on every event (the naive
    baseline; useful as a comparison point in the paper to show the cost of not
    doing incremental updates).
    """

    parallel_workers: int = 8
    """Parallel docker exec workers for dataplane pushes."""

    route_dump_nodes: Tuple[int, ...] = (26, 27)
    """Node ids to dump kernel routes for verification."""
