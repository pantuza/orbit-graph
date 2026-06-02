#!/usr/bin/env python3
"""
Summarize ping and control-plane metrics from two compare_single_run artifact dirs.

SDN full runs recompute the FIB on each delay tick but skip dataplane pushes when
next-hops are unchanged (fib_unchanged). Summaries separate routing events from
steady delay ticks for fair OSPF vs SDN comparison.

Usage:
  python experiments/compare_summarize.py \\
    starlink-5-5-550-53-grid-LeastDelay-ospf-full \\
    starlink-5-5-550-53-grid-LeastDelay-sdn-full
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys


def _parse_ping(path: str) -> dict:
    if not os.path.isfile(path):
        return {"loss": None, "avg_ms": None}
    text = open(path, encoding="utf-8").read()
    loss = None
    avg_ms = None
    for line in text.splitlines():
        if "packet loss" in line:
            m = re.search(r"(\d+)% packet loss", line)
            if m:
                loss = int(m.group(1))
        if line.startswith("rtt ") and "avg" in line:
            m = re.search(r"/([\d.]+)/", line)
            if m:
                avg_ms = float(m.group(1))
    return {"loss": loss, "avg_ms": avg_ms}


def _load_snapshots(metrics_dir: str) -> list[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(metrics_dir, "snapshot_*.json"))):
        with open(path, encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def _is_routing_event(snap: dict) -> bool:
    """Control-plane work that may change forwarding (paper-relevant)."""
    reason = snap.get("reason")
    if reason in ("init", "topology_change", "damage_recovery"):
        return True
    if reason == "delay_update":
        if snap.get("fib_unchanged"):
            return False
        if snap.get("installed", 0) == 0 and snap.get("failed", 0) == 0:
            return False
        return True
    return True


def _summarize_control_plane(path: str, mode: str) -> None:
    sub = "sdn_metrics" if mode == "sdn" else "ospf_metrics"
    mdir = os.path.join(path, sub)
    if not os.path.isdir(mdir):
        print(f"  {sub}: (missing)")
        return

    snaps = _load_snapshots(mdir)
    if not snaps:
        print(f"  {sub}: (no snapshots)")
        return

    time_key = "recompute_ms" if mode == "sdn" else "collection_ms"
    events = [s for s in snaps if _is_routing_event(s)]
    steady = [s for s in snaps if not _is_routing_event(s)]

    print(f"  {sub}: {len(snaps)} snapshots ({len(events)} routing events, {len(steady)} steady delay ticks)")
    print(f"  Control-plane events ({time_key}):")
    for s in events:
        if mode == "sdn":
            extra = (
                f"installed={s.get('installed')} failed={s.get('failed')}"
            )
            if s.get("fib_unchanged") is False and s.get("reason") == "delay_update":
                extra += " (FIB changed)"
        else:
            extra = f"bird_ok={s.get('bird_route_ok')}/{s.get('nodes_dumped')}"
        print(
            f"    t={s.get('time_index')} {s.get('reason')}: "
            f"{time_key}={s.get(time_key)} {extra}"
        )

    if steady and mode == "sdn":
        ms = [s.get(time_key) for s in steady if s.get(time_key) is not None]
        avg = sum(ms) / len(ms) if ms else 0.0
        print(
            f"  Steady delay ticks (FIB unchanged, no route push): "
            f"n={len(steady)} avg_{time_key}={avg:.1f}ms "
            f"(FIB recompute only; compare to OSPF tc-only updates)"
        )
    elif steady:
        print(
            f"  Steady delay ticks: n={len(steady)} "
            f"(OSPF: no collection on delay ticks; SDN: FIB recompute only)"
        )


def _ping_tag(ping_file: str) -> str:
    """Extract the time tag from ping-<src>-<des>_<tag>.txt (size-agnostic)."""
    name = os.path.basename(ping_file)[len("ping-"):-len(".txt")]
    # name == "<src>-<des>_<tag>"; the src-des pair has no underscore.
    return name.split("_", 1)[1] if "_" in name else name


def _summarize_dir(path: str, label: str, mode: str) -> None:
    print(f"\n=== {label}: {path} ===")
    for ping_file in sorted(glob.glob(os.path.join(path, "ping-*_*.txt"))):
        tag = _ping_tag(ping_file)
        stats = _parse_ping(ping_file)
        print(f"  ping @ {tag}: loss={stats['loss']}% avg_rtt={stats['avg_ms']} ms")

    _summarize_control_plane(path, mode)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare OSPF vs SDN artifact folders")
    parser.add_argument("ospf_dir", help="Artifact dir from --mode ospf run")
    parser.add_argument("sdn_dir", help="Artifact dir from --mode sdn run")
    args = parser.parse_args()
    _summarize_dir(args.ospf_dir, "OSPF", "ospf")
    _summarize_dir(args.sdn_dir, "SDN", "sdn")
    print()


if __name__ == "__main__":
    main()
