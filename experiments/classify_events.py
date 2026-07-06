#!/usr/bin/env python3
"""
Classify each handover event as a coverage-preserving GSL handover or a
coverage-gap event, from committed run artifacts.

Motivation: the outage metric aligns black-holes to control snapshots, but not
every "topology change" is the same physical event. A normal GSL handover swaps
a ground station's serving satellite while a replacement is in view, so a
controller can install the new path before the old link is removed. When the
constellation is sparse, a fixed ground station can instead fall into a
*coverage gap*: no satellite is in view for a period. During a coverage gap the
ground-to-ground path is physically severed and no routing protocol (OSPF or
SDN) can restore it, because there is no replacement link to program.

This classifier reads the per-second one-way delay matrices produced by the
emulator (``delay/<t>.txt``) for one representative run per grid and labels each
handover tick present in ``outage_raw.csv``:

  coverage_gap  -- a probe-endpoint ground station has zero satellite links in
                   view at the event tick (or the tick immediately after);
  handover      -- both probe-endpoint ground stations keep a satellite link
                   throughout the event.

The orbital geometry is deterministic, so a single representative run per grid
determines the class of every repetition at that grid. Output is a small sidecar
CSV consumed by the plotter.

Usage:
  python experiments/classify_events.py --in-dir ./scale_results_paper \
      --artifacts-root . --out event_class.csv
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import sys
from typing import Dict, List, Optional, Set, Tuple

HANDOVER_REASONS = {"topology_change", "proactive_handover"}
LINK_THRESHOLD_MS = 0.01  # same edge threshold the SDN topology builder uses


def _int(val: object) -> Optional[int]:
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return None


def handover_ticks_by_nodes(outage_raw_path: str) -> Dict[int, Set[int]]:
    """Collect the set of handover-event ticks present per node count."""
    ticks: Dict[int, Set[int]] = {}
    with open(outage_raw_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("reason") not in HANDOVER_REASONS:
                continue
            n = _int(row.get("nodes"))
            t = _int(row.get("time_index"))
            if n is None or t is None:
                continue
            ticks.setdefault(n, set()).add(t)
    return ticks


def grid_side(nodes: int) -> int:
    """Recover the O in an O x O Walker grid from node count (O*O + 2 GS)."""
    return int(round((nodes - 2) ** 0.5))


def find_artifact_dir(artifacts_root: str, side: int) -> Optional[str]:
    """Locate a representative committed run directory for one grid."""
    patterns = [
        f"starlink-{side}-{side}-*-{side}x{side}-r1",
        f"starlink-{side}-{side}-*-{side}x{side}-r*",
        f"starlink-{side}-{side}-*",
    ]
    for pat in patterns:
        matches = sorted(glob.glob(os.path.join(artifacts_root, pat)))
        matches = [m for m in matches if os.path.isdir(os.path.join(m, "delay"))]
        if matches:
            return matches[0]
    return None


def probe_endpoints(run_dir: str, nodes: int) -> Tuple[int, int]:
    """Read the probed ground-station pair from the outage-<a>-<b>.txt filename."""
    for path in glob.glob(os.path.join(run_dir, "outage-*.txt")):
        m = re.search(r"outage-(\d+)-(\d+)\.txt$", os.path.basename(path))
        if m:
            return int(m.group(1)), int(m.group(2))
    # Fall back to the last two node ids (ground stations follow the satellites).
    return nodes - 1, nodes


def satellite_links(run_dir: str, tick: int, node: int) -> Optional[int]:
    """Count in-view satellite links for one node at a tick from the delay matrix."""
    path = os.path.join(run_dir, "delay", f"{tick}.txt")
    if not os.path.isfile(path):
        return None
    rows = [line.strip().split(",") for line in open(path) if line.strip()]
    if node - 1 >= len(rows):
        return None
    return sum(1 for cell in rows[node - 1] if _to_float(cell) > LINK_THRESHOLD_MS)


def _to_float(cell: str) -> float:
    try:
        return float(cell)
    except ValueError:
        return 0.0


def classify_tick(run_dir: str, tick: int, gs1: int, gs2: int) -> str:
    """Coverage gap if either endpoint loses all satellites at/around the tick."""
    for offset in (-1, 0, 1):
        for gs in (gs1, gs2):
            links = satellite_links(run_dir, tick + offset, gs)
            if links == 0:
                return "coverage_gap"
    return "handover"


def classify(in_dir: str, artifacts_root: str) -> List[dict]:
    outage_raw = os.path.join(in_dir, "outage_raw.csv")
    if not os.path.isfile(outage_raw):
        raise FileNotFoundError(outage_raw)

    ticks_by_nodes = handover_ticks_by_nodes(outage_raw)
    records: List[dict] = []
    for nodes in sorted(ticks_by_nodes):
        side = grid_side(nodes)
        run_dir = find_artifact_dir(artifacts_root, side)
        if run_dir is None:
            print(
                f"  warn: no artifact dir with delay matrices for {side}x{side} "
                f"({nodes} nodes); skipping classification",
                file=sys.stderr,
            )
            continue
        gs1, gs2 = probe_endpoints(run_dir, nodes)
        for tick in sorted(ticks_by_nodes[nodes]):
            event_type = classify_tick(run_dir, tick, gs1, gs2)
            records.append(
                {"nodes": nodes, "time_index": tick, "event_type": event_type}
            )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify handover events as coverage-preserving or coverage gaps")
    parser.add_argument("--in-dir", required=True,
                        help="Results dir containing outage_raw.csv")
    parser.add_argument("--artifacts-root", default=".",
                        help="Directory holding the starlink-* run artifacts")
    parser.add_argument("--out", default="event_class.csv",
                        help="Output filename (written inside --in-dir)")
    args = parser.parse_args()

    records = classify(args.in_dir, args.artifacts_root)
    out_path = os.path.join(args.in_dir, args.out)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["nodes", "time_index", "event_type"])
        writer.writeheader()
        writer.writerows(records)

    gaps = sum(1 for r in records if r["event_type"] == "coverage_gap")
    print(f"Classified {len(records)} handover events "
          f"({gaps} coverage-gap, {len(records) - gaps} handover)")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
