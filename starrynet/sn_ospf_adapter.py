"""
OSPF/BIRD metrics collection for fair comparison with SDN experiments.

Writes ospf_metrics/snapshot_*.json plus route and birdc dumps at the same
lifecycle points as the SDN controller (init, damage/recovery, topology change).
Delay ticks are skipped (tc-only; no routing change).
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Set, Tuple

from starrynet.sn_sdn_adapter import _work_dir, resolve_dest_ip
from starrynet.sn_utils import sn_get_container_info, sn_remote_cmd

DEFAULT_ROUTE_DUMP_NODES: Tuple[int, ...] = (7, 8, 13, 26, 27)
DEFAULT_BIRD_DEST = "9.27.27.10"


def is_ospf_mode(intra_routing: str) -> bool:
    return intra_routing.strip().upper() != "SDN"


class OspfMetricsCollector:
    """Collect kernel routes and BIRD state into ospf_metrics/."""

    def __init__(
        self,
        sn,
        *,
        route_dump_nodes: Tuple[int, ...] = DEFAULT_ROUTE_DUMP_NODES,
        bird_dest: str = DEFAULT_BIRD_DEST,
        parallel_workers: int = 8,
    ) -> None:
        self.sn = sn
        self.route_dump_nodes = tuple(route_dump_nodes)
        self.bird_dest = bird_dest
        self.parallel_workers = parallel_workers
        base = _work_dir(sn)
        self.metrics_dir = os.path.join(base, "ospf_metrics")
        self.route_dump_dir = os.path.join(self.metrics_dir, "route_dumps")
        self.bird_dump_dir = os.path.join(self.metrics_dir, "bird_dumps")
        os.makedirs(self.route_dump_dir, exist_ok=True)
        os.makedirs(self.bird_dump_dir, exist_ok=True)

    def _container(self, node_id: int) -> str:
        return str(self.sn.container_id_list[node_id - 1])

    def _run(self, cmd: str) -> List[str]:
        return sn_remote_cmd(self.sn.remote_ssh, cmd)

    def _route_lines(self, node_id: int) -> int:
        cid = self._container(node_id)
        lines = self._run(f"docker exec {cid} ip route show")
        return sum(1 for line in lines if line.strip())

    def _dump_route_table(self, node_id: int, label: str) -> str:
        cid = self._container(node_id)
        lines = self._run(f"docker exec {cid} ip route show")
        path = os.path.join(
            self.route_dump_dir, f"routes_B{node_id}_{label}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        return path

    def _dump_bird(self, node_id: int, label: str) -> Tuple[str, str, bool, bool]:
        cid = self._container(node_id)
        route_lines = self._run(
            f"docker exec {cid} birdc show route for {self.bird_dest} 2>&1")
        proto_lines = self._run(
            f"docker exec {cid} birdc show protocols all 2>&1")
        route_ok = route_lines and "bird:" not in "".join(route_lines[:3]).lower()
        proto_ok = proto_lines and "bird:" not in "".join(proto_lines[:3]).lower()
        route_path = os.path.join(
            self.bird_dump_dir,
            f"bird_B{node_id}_{label}_route.txt",
        )
        proto_path = os.path.join(
            self.bird_dump_dir,
            f"bird_B{node_id}_{label}_protocols.txt",
        )
        with open(route_path, "w", encoding="utf-8") as fh:
            fh.writelines(route_lines)
        with open(proto_path, "w", encoding="utf-8") as fh:
            fh.writelines(proto_lines)
        return route_path, proto_path, route_ok, proto_ok

    def record_snapshot(
        self,
        time_index: int,
        reason: str,
        *,
        damaged_nodes: Optional[Set[int]] = None,
        label: Optional[str] = None,
    ) -> dict:
        """Dump routes + birdc on selected nodes; write snapshot JSON."""
        dump_label = label or f"t{time_index}_{reason}"
        # wall_start ~ when this event was applied by the emulator (this handler
        # runs right after the topology mutation). Recorded in epoch seconds so
        # the outage probe can align data-plane recovery to the event; symmetric
        # with the SDN snapshot. OSPF reconverges in the background after this.
        wall_start = time.time()
        t0 = time.perf_counter()

        route_lines: dict[str, int] = {}
        bird_route_ok = 0
        bird_proto_ok = 0

        def _one(node_id: int) -> Tuple[int, int, bool, bool]:
            nlines = self._route_lines(node_id)
            self._dump_route_table(node_id, dump_label)
            _rp, _pp, rok, pok = self._dump_bird(node_id, dump_label)
            return node_id, nlines, rok, pok

        with ThreadPoolExecutor(max_workers=self.parallel_workers) as pool:
            futures = [pool.submit(_one, nid) for nid in self.route_dump_nodes]
            for fut in as_completed(futures):
                nid, nlines, rok, pok = fut.result()
                route_lines[str(nid)] = nlines
                if rok:
                    bird_route_ok += 1
                if pok:
                    bird_proto_ok += 1

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        metrics = {
            "time_index": time_index,
            "reason": reason,
            "collection_ms": round(elapsed_ms, 2),
            "wall_start": round(wall_start, 6),
            "wall_end": round(time.time(), 6),
            "damaged_nodes": sorted(damaged_nodes or []),
            "nodes_dumped": len(self.route_dump_nodes),
            "bird_dest": self.bird_dest,
            "bird_route_ok": bird_route_ok,
            "bird_proto_ok": bird_proto_ok,
            "route_lines": route_lines,
        }
        path = os.path.join(
            self.metrics_dir,
            f"snapshot_{time_index}_{reason}.json",
        )
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(metrics, fh, indent=2)
        print(
            f"[OSPF] t={time_index} ({reason}): collected in "
            f"{metrics['collection_ms']}ms "
            f"(bird route ok {bird_route_ok}/{len(self.route_dump_nodes)})"
        )
        return metrics

    def dump_routes(self, label: str, node_ids: Optional[List[int]] = None) -> List[str]:
        nodes = list(node_ids or self.route_dump_nodes)
        paths = []
        for nid in nodes:
            paths.append(self._dump_route_table(nid, label))
        print(
            f"[OSPF] Route dumps ({label}): "
            + ", ".join(os.path.basename(p) for p in paths))
        return paths


def attach_ospf_metrics(
    sn,
    *,
    route_dump_nodes: Tuple[int, ...] = DEFAULT_ROUTE_DUMP_NODES,
) -> OspfMetricsCollector:
    collector = OspfMetricsCollector(sn, route_dump_nodes=route_dump_nodes)
    sn.ospf_metrics = collector
    return collector


def sync_damaged_nodes(sn) -> Set[int]:
    damaged: Set[int] = {int(i) + 1 for i in sn.damage_list}
    return damaged


def ospf_after_delay_update(sn, timeptr: int) -> None:
    """
    Steady delay ticks only adjust link delay (tc netem); OSPF routes unchanged.

    Skip metrics collection here so control-plane summaries compare routing events
    only (symmetric with SDN fib_unchanged delay ticks).
    """
    return


def ospf_after_topology_change(sn, time_index: int) -> None:
    collector = getattr(sn, "ospf_metrics", None)
    if collector is None:
        return
    collector.record_snapshot(
        int(time_index),
        "topology_change",
        damaged_nodes=sync_damaged_nodes(sn),
    )


def ospf_after_damage_or_recovery(sn, timeptr: int) -> None:
    collector = getattr(sn, "ospf_metrics", None)
    if collector is None:
        return
    collector.record_snapshot(
        timeptr, "damage_recovery", damaged_nodes=sync_damaged_nodes(sn))


def dump_ospf_routes(
    sn,
    label: str = "pre_teardown",
    node_ids: Optional[List[int]] = None,
) -> None:
    """Dump kernel route tables from selected nodes (verification)."""
    collector = getattr(sn, "ospf_metrics", None)
    if collector is None:
        attach_ospf_metrics(sn)
        collector = sn.ospf_metrics
    collector.dump_routes(label, node_ids=node_ids)


def run_ospf_post_init_checks(
    sn,
    *,
    route_dump_nodes: Tuple[int, ...] = DEFAULT_ROUTE_DUMP_NODES,
    wait_s: float = 5.0,
) -> None:
    """Wait for OSPF convergence, record init snapshot, dump post_init tables."""
    if wait_s > 0:
        print(f"[OSPF] Waiting {wait_s:.0f}s for OSPF convergence...")
        time.sleep(wait_s)
    attach_ospf_metrics(sn, route_dump_nodes=route_dump_nodes)
    sn.ospf_metrics.record_snapshot(1, "init", label="post_init")
