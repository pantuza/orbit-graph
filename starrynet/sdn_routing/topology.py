"""Topology graph built from StarryNet delay matrices."""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Set

import numpy as np

from starrynet.sn_orchestrater import sn_get_param

Graph = Dict[int, List[tuple[int, float]]]


def load_delay_matrix(delay_file: str) -> np.ndarray:
    """Load a comma-separated delay matrix written by Observer."""
    rows = sn_get_param(delay_file)
    return np.array([[float(x) for x in row] for row in rows])


def delay_matrix_path(delay_dir: str, time_index: int) -> str:
    return os.path.join(delay_dir, f"{time_index}.txt")


def graph_from_matrix(
    matrix: np.ndarray,
    *,
    link_threshold: float = 0.01,
    damaged_nodes: Optional[Set[int]] = None,
) -> Graph:
    """
    Build an undirected weighted graph. Node ids are 1-indexed (StarryNet convention).

    Damaged nodes are removed from the graph (all incident edges dropped).
    """
    n = matrix.shape[0]
    damaged = damaged_nodes or set()
    graph: Graph = {i: [] for i in range(1, n + 1)}

    for i in range(n):
        for j in range(i + 1, n):
            w = float(matrix[i][j])
            if w <= link_threshold:
                continue
            a, b = i + 1, j + 1
            if a in damaged or b in damaged:
                continue
            graph[a].append((b, w))
            graph[b].append((a, w))

    return graph


def load_graph(
    delay_dir: str,
    time_index: int,
    *,
    link_threshold: float = 0.01,
    damaged_nodes: Optional[Set[int]] = None,
) -> Graph:
    path = delay_matrix_path(delay_dir, time_index)
    matrix = load_delay_matrix(path)
    return graph_from_matrix(
        matrix,
        link_threshold=link_threshold,
        damaged_nodes=damaged_nodes,
    )
