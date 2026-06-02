"""Shortest-path routing for SDN snapshots."""

from __future__ import annotations

import heapq
from typing import Dict, List, Optional, Tuple

from starrynet.sdn_routing.topology import Graph

Fib = Dict[int, Dict[int, int]]
"""Forwarding table: src_node -> {dest_node -> next_hop_node}."""


def dijkstra(graph: Graph, source: int) -> Tuple[Dict[int, float], Dict[int, Optional[int]]]:
    """Return (distance, parent) for all nodes reachable from source."""
    dist: Dict[int, float] = {source: 0.0}
    parent: Dict[int, Optional[int]] = {source: None}
    heap: List[tuple[float, int]] = [(0.0, source)]

    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, float("inf")):
            continue
        for v, w in graph.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                parent[v] = u
                heapq.heappush(heap, (nd, v))

    return dist, parent


def _path_from_parent(parent: Dict[int, Optional[int]], dest: int) -> List[int]:
    if dest not in parent:
        return []
    path: List[int] = []
    cur: Optional[int] = dest
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    path.reverse()
    return path


def fib_equal(a: Fib, b: Fib) -> bool:
    """True when every (src, dest) next-hop matches (ignores link weights)."""
    if set(a.keys()) != set(b.keys()):
        return False
    for src, entries in a.items():
        if entries != b.get(src):
            return False
    return True


def compute_fib(graph: Graph, node_count: int) -> Fib:
    """All-pairs next-hop FIB using delay-weighted shortest paths."""
    fib: Fib = {}
    for src in range(1, node_count + 1):
        if src not in graph:
            continue
        _, parent = dijkstra(graph, src)
        fib[src] = {}
        for dest in range(1, node_count + 1):
            if dest == src:
                continue
            path = _path_from_parent(parent, dest)
            if len(path) < 2:
                continue
            fib[src][dest] = path[1]
    return fib
