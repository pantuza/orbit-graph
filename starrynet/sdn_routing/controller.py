"""SDN controller: snapshot computation and route installation."""

from __future__ import annotations

import json
import os
import time
from typing import List, Optional, Set

from starrynet.sdn_routing.config import SdnConfig
from starrynet.sdn_routing.dataplane import DockerDataplane
from starrynet.sdn_routing.routing import Fib, compute_fib, fib_equal
from starrynet.sdn_routing.topology import load_graph


class SdnController:
    """Centralized control plane for StarryNet SDN experiments."""

    def __init__(
        self,
        config: SdnConfig,
        remote_ssh,
        container_id_list,
    ):
        self.config = config
        self.remote_ssh = remote_ssh
        self.container_id_list = list(container_id_list)
        self.dataplane = DockerDataplane(
            remote_ssh,
            self.container_id_list,
            config.constellation_size,
            parallel_workers=config.parallel_workers,
        )
        self._damaged_nodes: Set[int] = set()
        self._last_fib: Optional[Fib] = None
        os.makedirs(config.metrics_dir, exist_ok=True)
        self._route_dump_dir = os.path.join(config.metrics_dir, "route_dumps")

    def set_damaged_nodes(self, damaged: Set[int]) -> None:
        """Satellite indices (1-indexed) with 100% loss on all interfaces."""
        self._damaged_nodes = set(damaged)

    def install_snapshot(self, time_index: int, reason: str = "periodic") -> dict:
        """
        Load topology for time_index, compute FIB, push routes to containers.
        Returns a metrics dict suitable for JSON logging.
        """
        refresh = reason in ("init", "topology_change", "damage_recovery")

        # wall_start/wall_end are epoch seconds (same clock as the container-side
        # outage probe's `ping -D` timestamps), so outage analysis can align the
        # data-plane recovery to this control-plane event. compute_ms is the
        # algorithmic controller cost (graph load + Dijkstra); install_ms is the
        # dataplane push cost (docker-exec route install) and is a harness
        # artifact, not inherent to SDN -- we keep them separate for fairness.
        wall_start = time.time()
        t0 = time.perf_counter()
        graph = load_graph(
            self.config.delay_dir,
            time_index,
            link_threshold=self.config.link_threshold,
            damaged_nodes=self._damaged_nodes,
        )
        fib = compute_fib(graph, self.config.node_count)
        fib_unchanged = (
            reason == "delay_update"
            and self._last_fib is not None
            and fib_equal(fib, self._last_fib)
        )
        compute_ms = (time.perf_counter() - t0) * 1000.0

        if fib_unchanged:
            install_stats = {
                "installed": 0,
                "skipped": 0,
                "failed": 0,
                "deleted": 0,
                "on_link": 0,
            }
            install_ms = 0.0
        else:
            ti0 = time.perf_counter()
            install_stats = self.dataplane.install_fib(
                fib, refresh_addresses=refresh)
            install_ms = (time.perf_counter() - ti0) * 1000.0
            self._last_fib = fib

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        wall_end = time.time()

        metrics = {
            "time_index": time_index,
            "reason": reason,
            "recompute_ms": round(elapsed_ms, 2),
            "compute_ms": round(compute_ms, 2),
            "install_ms": round(install_ms, 2),
            "wall_start": round(wall_start, 6),
            "wall_end": round(wall_end, 6),
            "fib_entries": sum(len(v) for v in fib.values()),
            "damaged_nodes": sorted(self._damaged_nodes),
            "fib_unchanged": fib_unchanged,
            **install_stats,
        }
        self._write_metrics(metrics, time_index, reason)
        if fib_unchanged:
            print(
                f"[SDN] t={time_index} ({reason}): "
                f"fib unchanged ({metrics['fib_entries']} entries), "
                f"no dataplane push in {metrics['recompute_ms']}ms"
            )
        else:
            print(
                f"[SDN] t={time_index} ({reason}): "
                f"fib={metrics['fib_entries']} "
                f"installed={metrics['installed']} "
                f"skipped={metrics['skipped']} "
                f"deleted={metrics.get('deleted', 0)} "
                f"on_link={metrics.get('on_link', 0)} "
                f"failed={metrics['failed']} "
                f"in {metrics['recompute_ms']}ms"
            )
        return metrics

    def dump_routes(self, label: str, node_ids: Optional[List[int]] = None) -> List[str]:
        """Capture kernel routing tables before teardown."""
        nodes = list(node_ids or self.config.route_dump_nodes)
        paths = self.dataplane.dump_route_tables(
            nodes, self._route_dump_dir, label)
        print(f"[SDN] Route dumps ({label}): {', '.join(os.path.basename(p) for p in paths)}")
        return paths

    def _write_metrics(self, metrics: dict, time_index: int, reason: str) -> None:
        path = os.path.join(
            self.config.metrics_dir,
            f"snapshot_{time_index}_{reason}.json",
        )
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(metrics, fh, indent=2)
