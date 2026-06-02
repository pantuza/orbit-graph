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

    parallel_workers: int = 8
    """Parallel docker exec workers for dataplane pushes."""

    route_dump_nodes: Tuple[int, ...] = (26, 27)
    """Node ids to dump kernel routes for verification."""
