#!/usr/bin/env python3
"""
Run repeated, seeded OSPF and SDN experiments and aggregate statistics.

Each repetition runs `compare_single_run.py` in a subprocess with a unique
artifact suffix and RNG seed, then this script parses the resulting artifact
directory. Results are aggregated into mean / stddev and written as CSV for
plotting, plus a printed summary table.

Usage:
  python experiments/compare_batch.py --reps 5
  python experiments/compare_batch.py --reps 10 --modes ospf,sdn --profile full
  python experiments/compare_batch.py --simulation simulation.json
  make scale
  make scale SCALE_REPS=10 SIMULATION=simulation.json

Outputs (under --out-dir):
  ping_raw.csv             one row per (mode, rep, ping time tag), incl. phase
  ping_summary.csv         mean/stddev of loss/RTT per (nodes, mode, time tag)
  ping_summary_by_phase.csv mean/stddev per (nodes, mode, handover-relative phase)
  control_raw.csv          one row per (mode, rep, control-plane snapshot),
                           incl. compute_ms/install_ms (SDN)
  control_summary.csv      mean/stddev of control-plane time per (nodes, mode, event)
  control_summary_by_reason.csv pooled per (nodes, mode, reason) incl. compute/install
  outage_raw.csv           one row per (mode, rep, event): data-plane outage_ms
  outage_summary.csv       mean/stddev data-plane outage per (nodes, mode, reason)
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from starrynet.sn_utils import sn_ensure_ovs_containers_gone  # noqa: E402

from compare_summarize import (  # noqa: E402  (path set above)
    _is_routing_event,
    _load_snapshots,
    _parse_ping,
)
from outage import run_outages  # noqa: E402  (path set above)
from simulation_config import load_simulation_plan, print_plan  # noqa: E402

_SINGLE_RUN = os.path.join(_HERE, "compare_single_run.py")
_ARTIFACT_RE = re.compile(r"^BATCH_ARTIFACT_DIR=(.+)$")
_NODES_RE = re.compile(r"^BATCH_NODES=(\d+)$")
_PAIR_RE = re.compile(r"^BATCH_PING_PAIR=(.+)$")
_HANDOVER_RE = re.compile(r"^BATCH_HANDOVER_TIME=(-?\d+)$")
_STEADY_RE = re.compile(r"^BATCH_STEADY_TIME=(-?\d+)$")

# Control-plane reasons expected in every full-profile rep. We match by reason
# (not reason@time) because the topology_change tick is geometry-dependent
# (t=53 for 5x5, t=23 for 6x6, ...), so a fixed @t would falsely flag larger
# grids. init/damage_recovery come from our own fixed damage(5)/recovery(10).
_EXPECTED_FULL_REASONS = ("init", "damage_recovery", "topology_change")
# Ping phases expected in every full-profile rep (handover-relative; see
# compare_single_run._full_ping_schedule).
_EXPECTED_FULL_PHASES = ("post_init", "handover", "post_handover", "steady")


def _agg(values: List[float]) -> Tuple[int, Optional[float], Optional[float]]:
    """Return (n, mean, sample_stddev) ignoring None; stddev is None when n<2."""
    nums = [v for v in values if v is not None]
    if not nums:
        return 0, None, None
    mean = statistics.fmean(nums)
    std = statistics.stdev(nums) if len(nums) >= 2 else 0.0
    return len(nums), mean, std


def _fmt(x: Optional[float]) -> str:
    return "NA" if x is None else f"{x:.2f}"


def _format_elapsed(seconds: float) -> str:
    """Human-readable duration, similar to shell ``time`` output."""
    if seconds >= 3600:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours}h {minutes}m {secs:.2f}s"
    if seconds >= 60:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.2f}s"
    return f"{seconds:.2f}s"


def _parse_sizes(spec: str) -> List[Tuple[Optional[int], Optional[int]]]:
    """Parse '5x5,6x6' into [(5,5),(6,6)]; empty -> [(None, None)] (config default)."""
    spec = spec.strip()
    if not spec:
        return [(None, None)]
    out: List[Tuple[Optional[int], Optional[int]]] = []
    for token in spec.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if "x" not in token:
            raise SystemExit(f"Invalid --sizes token '{token}' (expected OxS)")
        o, s = token.split("x", 1)
        out.append((int(o), int(s)))
    return out


def _parse_durations(spec: str) -> Dict[Tuple[int, int], int]:
    """
    Parse '6x6=120,8x8=300' into {(6,6): 120, (8,8): 300}.

    Sizes omitted from the map use the Duration (s) value in config.json /
    config_sdn.json when the batch run starts.
    """
    spec = spec.strip()
    if not spec:
        return {}
    out: Dict[Tuple[int, int], int] = {}
    for token in spec.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if "=" not in token:
            raise SystemExit(
                f"Invalid --durations token '{token}' (expected OxS=seconds)")
        grid, secs = token.split("=", 1)
        if "x" not in grid:
            raise SystemExit(
                f"Invalid --durations grid '{grid}' (expected OxS=seconds)")
        o, s = grid.split("x", 1)
        try:
            duration = int(secs.strip())
        except ValueError as exc:
            raise SystemExit(
                f"Invalid --durations seconds in '{token}' (expected integer)"
            ) from exc
        if duration < 10:
            raise SystemExit(
                f"Duration for {grid} must be >= 10s (got {duration})")
        key = (int(o), int(s))
        if key in out:
            raise SystemExit(f"Duplicate --durations entry for {grid}")
        out[key] = duration
    return out


def _run_one(
    mode: str,
    profile: str,
    rep: int,
    seed: int,
    orbits: Optional[int],
    sats: Optional[int],
    duration: Optional[int] = None,
) -> Tuple[Optional[dict], float]:
    """Run a single experiment in a subprocess; return (metadata, elapsed_s)."""
    started = time.perf_counter()
    grid = f"{orbits}x{sats}" if (orbits and sats) else "default"
    suffix = (
        f"{mode}-{profile}-{orbits}x{sats}-r{rep}"
        if (orbits and sats)
        else f"{mode}-{profile}-r{rep}"
    )
    cmd = [
        sys.executable,
        _SINGLE_RUN,
        "--mode", mode,
        "--profile", profile,
        "--suffix", suffix,
        "--seed", str(seed),
    ]
    if orbits and sats:
        cmd += ["--orbits", str(orbits), "--sats", str(sats)]
    if duration is not None:
        cmd += ["--duration", str(duration)]
    dur_note = f" duration={duration}s" if duration is not None else ""
    print(f"\n{'=' * 70}")
    print(f">>> {mode.upper()} grid={grid} rep {rep} (seed={seed}){dur_note} suffix={suffix}")
    print(f"{'=' * 70}", flush=True)

    artifact_dir: Optional[str] = None
    nodes: Optional[int] = None
    pair: Optional[str] = None
    handover_t: Optional[int] = None
    steady_t: Optional[int] = None
    proc = subprocess.Popen(
        cmd,
        cwd=_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        stripped = line.strip()
        m = _ARTIFACT_RE.match(stripped)
        if m:
            artifact_dir = m.group(1).strip()
        m = _NODES_RE.match(stripped)
        if m:
            nodes = int(m.group(1))
        m = _PAIR_RE.match(stripped)
        if m:
            pair = m.group(1).strip()
        m = _HANDOVER_RE.match(stripped)
        if m:
            handover_t = int(m.group(1))
        m = _STEADY_RE.match(stripped)
        if m:
            steady_t = int(m.group(1))
    proc.wait()

    try:
        sn_ensure_ovs_containers_gone(None)
    except RuntimeError as exc:
        elapsed = time.perf_counter() - started
        print(
            f"!!! Docker cleanup after {mode} grid={grid} rep {rep}: {exc} "
            f"({_format_elapsed(elapsed)})"
        )
        return None, elapsed

    elapsed = time.perf_counter() - started

    if proc.returncode != 0:
        print(
            f"!!! {mode} grid={grid} rep {rep} exited with code "
            f"{proc.returncode} ({_format_elapsed(elapsed)})"
        )
        return None, elapsed
    if artifact_dir is None or not os.path.isdir(artifact_dir):
        print(
            f"!!! {mode} grid={grid} rep {rep}: artifact dir not found "
            f"({_format_elapsed(elapsed)})"
        )
        return None, elapsed
    print(f"Finished {mode.upper()} grid={grid} rep {rep} in "
          f"{_format_elapsed(elapsed)} ({elapsed:.2f} s)")
    return {
        "artifact_dir": artifact_dir,
        "nodes": nodes,
        "pair": pair,
        "handover_t": handover_t,
        "steady_t": steady_t,
    }, elapsed


def _ping_phase(
    tag: str,
    handover_t: Optional[int],
    steady_t: Optional[int],
) -> str:
    """Map a ping time tag to a size-agnostic phase for cross-size comparison.

    Handover ticks are geometry-dependent, so a fixed tag won't align across
    grids; we use the per-run handover/steady markers to label pings instead.
    """
    if tag == "post_init":
        return "post_init"
    try:
        t = int(tag)
    except ValueError:
        return tag
    if handover_t is not None and handover_t >= 0:
        if t == handover_t:
            return "handover"
        if t == handover_t + 2:
            return "post_handover"
    if steady_t is not None and steady_t >= 0 and t == steady_t:
        return "steady"
    return f"t{tag}"


def _collect_ping(
    artifact_dir: str,
    mode: str,
    profile: str,
    rep: int,
    seed: int,
    nodes: Optional[int],
    handover_t: Optional[int] = None,
    steady_t: Optional[int] = None,
) -> List[dict]:
    rows = []
    for ping_file in sorted(glob.glob(os.path.join(artifact_dir, "ping-*_*.txt"))):
        name = os.path.basename(ping_file)[len("ping-"):-len(".txt")]
        tag = name.split("_", 1)[1] if "_" in name else name
        stats = _parse_ping(ping_file)
        rows.append({
            "mode": mode,
            "profile": profile,
            "nodes": nodes,
            "rep": rep,
            "seed": seed,
            "time_tag": tag,
            "phase": _ping_phase(tag, handover_t, steady_t),
            "loss_pct": stats["loss"],
            "avg_rtt_ms": stats["avg_ms"],
        })
    return rows


def _collect_control(
    artifact_dir: str,
    mode: str,
    profile: str,
    rep: int,
    seed: int,
    nodes: Optional[int],
) -> List[dict]:
    sub = "sdn_metrics" if mode == "sdn" else "ospf_metrics"
    time_key = "recompute_ms" if mode == "sdn" else "collection_ms"
    rows = []
    for snap in _load_snapshots(os.path.join(artifact_dir, sub)):
        rows.append({
            "mode": mode,
            "profile": profile,
            "nodes": nodes,
            "rep": rep,
            "seed": seed,
            "time_index": snap.get("time_index"),
            "reason": snap.get("reason"),
            "event": f"{snap.get('reason')}@t{snap.get('time_index')}",
            "time_ms": snap.get(time_key),
            # SDN only: separate algorithmic compute from dataplane install cost
            # (install via docker-exec is a harness artifact, not inherent SDN).
            "compute_ms": snap.get("compute_ms"),
            "install_ms": snap.get("install_ms"),
            "routing_event": _is_routing_event(snap),
            "installed": snap.get("installed"),
            "fib_unchanged": snap.get("fib_unchanged"),
            "bird_route_ok": snap.get("bird_route_ok"),
        })
    return rows


def _collect_outage(
    artifact_dir: str,
    mode: str,
    profile: str,
    rep: int,
    seed: int,
    nodes: Optional[int],
    pair: Optional[str],
) -> List[dict]:
    rows = []
    for r in run_outages(artifact_dir, mode, pair):
        rows.append({
            "mode": mode,
            "profile": profile,
            "nodes": nodes,
            "rep": rep,
            "seed": seed,
            "reason": r["reason"],
            "time_index": r["time_index"],
            "event": r["event"],
            "outage_ms": r["outage_ms"],
            "still_down": r["still_down"],
        })
    return rows


def _check_missing(
    mode: str,
    nodes: Optional[int],
    rep: int,
    profile: str,
    ping_rows: List[dict],
    control_rows: List[dict],
) -> List[str]:
    """Warn when a rep is missing expected ping phases or routing reasons.

    Checks are size-agnostic: ping phases (post_init/handover/post_handover/
    steady) and control-plane reasons (init/damage_recovery/topology_change),
    not fixed @t ticks, since handover timing depends on constellation geometry.
    """
    if profile != "full":
        return []
    warnings = []
    have_phases = {r.get("phase") for r in ping_rows}
    for phase in _EXPECTED_FULL_PHASES:
        if phase not in have_phases:
            warnings.append(
                f"{mode} nodes={nodes} rep={rep}: missing ping phase {phase}")
    have_reasons = {r["reason"] for r in control_rows if r.get("routing_event")}
    for reason in _EXPECTED_FULL_REASONS:
        if reason not in have_reasons:
            warnings.append(
                f"{mode} nodes={nodes} rep={rep}: missing routing event {reason}")
    return warnings


PING_RAW_FIELDS = [
    "mode", "profile", "nodes", "rep", "seed", "time_tag", "phase",
    "loss_pct", "avg_rtt_ms",
]
CONTROL_RAW_FIELDS = [
    "mode", "profile", "nodes", "rep", "seed", "time_index", "reason",
    "event", "time_ms", "compute_ms", "install_ms", "routing_event",
    "installed", "fib_unchanged", "bird_route_ok",
]
OUTAGE_RAW_FIELDS = [
    "mode", "profile", "nodes", "rep", "seed", "reason", "time_index",
    "event", "outage_ms", "still_down",
]


def _read_csv(path: str, fields: List[str]) -> List[dict]:
    """Load a raw CSV written by this script; return [] when missing."""
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return []
        rows: List[dict] = []
        for row in reader:
            parsed: dict = {}
            for key in fields:
                val = row.get(key)
                if key in ("nodes", "rep", "seed", "time_index", "installed"):
                    parsed[key] = int(val) if val not in (None, "") else None
                elif key in ("loss_pct", "avg_rtt_ms", "time_ms", "compute_ms",
                             "install_ms", "outage_ms"):
                    parsed[key] = (
                        float(val) if val not in (None, "") else None
                    )
                elif key == "still_down":
                    parsed[key] = str(val).strip().lower() in (
                        "1", "true", "yes")
                else:
                    parsed[key] = val
            rows.append(parsed)
        return rows


def _nodes_in_rows(*row_groups: List[dict]) -> set[int]:
    nodes: set[int] = set()
    for group in row_groups:
        for row in group:
            n = row.get("nodes")
            if n is not None:
                nodes.add(int(n))
    return nodes


def _merge_appended_raw(
    out_dir: str,
    ping_rows: List[dict],
    control_rows: List[dict],
    outage_rows: List[dict],
) -> Tuple[List[dict], List[dict], List[dict]]:
    """
    Merge this run into existing raw CSVs, replacing any prior rows for the
    same ``nodes`` value (re-run of one grid size).
    """
    replace_nodes = _nodes_in_rows(ping_rows, control_rows, outage_rows)
    if not replace_nodes:
        return ping_rows, control_rows, outage_rows

    def _merge(path: str, fields: List[str], new_rows: List[dict]) -> List[dict]:
        kept = [
            r for r in _read_csv(path, fields)
            if r.get("nodes") not in replace_nodes
        ]
        return kept + new_rows

    merged_ping = _merge(
        os.path.join(out_dir, "ping_raw.csv"), PING_RAW_FIELDS, ping_rows)
    merged_control = _merge(
        os.path.join(out_dir, "control_raw.csv"), CONTROL_RAW_FIELDS, control_rows)
    merged_outage = _merge(
        os.path.join(out_dir, "outage_raw.csv"), OUTAGE_RAW_FIELDS, outage_rows)
    print(
        f"Appended results for nodes {sorted(replace_nodes)} into {out_dir} "
        f"(replaced prior rows for those sizes if present)."
    )
    return merged_ping, merged_control, merged_outage


def _write_csv(path: str, rows: List[dict], fields: List[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})


def _sort_key(item: dict) -> tuple:
    return (item.get("nodes") or 0, item["mode"])


def _summarize_ping(rows: List[dict]) -> List[dict]:
    grouped: Dict[Tuple[Optional[int], str, str], List[dict]] = defaultdict(list)
    for r in rows:
        grouped[(r["nodes"], r["mode"], r["time_tag"])].append(r)
    out = []
    for (nodes, mode, tag), items in grouped.items():
        n_loss, loss_mean, loss_std = _agg([i["loss_pct"] for i in items])
        n_rtt, rtt_mean, rtt_std = _agg([i["avg_rtt_ms"] for i in items])
        out.append({
            "nodes": nodes,
            "mode": mode,
            "time_tag": tag,
            "n": len(items),
            "loss_mean_pct": loss_mean,
            "loss_std_pct": loss_std,
            "rtt_mean_ms": rtt_mean,
            "rtt_std_ms": rtt_std,
            "n_rtt_samples": n_rtt,
        })
    out.sort(key=lambda s: (_sort_key(s), s["time_tag"]))
    return out


_PHASE_ORDER = {"post_init": 0, "handover": 1, "post_handover": 2, "steady": 3}


def _summarize_ping_by_phase(rows: List[dict]) -> List[dict]:
    """Per (nodes, mode, phase) ping stats.

    Phases are size-agnostic (handover-relative), so this view lines up across
    constellation sizes for charts like 'post-handover RTT vs node count'.
    """
    grouped: Dict[Tuple[Optional[int], str, str], List[dict]] = defaultdict(list)
    for r in rows:
        grouped[(r["nodes"], r["mode"], r.get("phase"))].append(r)
    out = []
    for (nodes, mode, phase), items in grouped.items():
        _n_loss, loss_mean, loss_std = _agg([i["loss_pct"] for i in items])
        n_rtt, rtt_mean, rtt_std = _agg([i["avg_rtt_ms"] for i in items])
        out.append({
            "nodes": nodes,
            "mode": mode,
            "phase": phase,
            "n": len(items),
            "loss_mean_pct": loss_mean,
            "loss_std_pct": loss_std,
            "rtt_mean_ms": rtt_mean,
            "rtt_std_ms": rtt_std,
            "n_rtt_samples": n_rtt,
        })
    out.sort(key=lambda s: (_sort_key(s), _PHASE_ORDER.get(s["phase"], 99),
                            str(s["phase"])))
    return out


def _summarize_control(rows: List[dict]) -> List[dict]:
    """Per (mode, reason@time) routing-event stats.

    Deterministic events (init@t1, damage_recovery@t5/t10, topology_change@t53)
    appear in every rep. Seed-dependent delay-tick FIB changes may appear in
    only some reps (watch the `n` column when comparing).
    """
    grouped: Dict[Tuple[Optional[int], str, str], List[dict]] = defaultdict(list)
    for r in rows:
        if not r["routing_event"]:
            continue
        grouped[(r["nodes"], r["mode"], r["event"])].append(r)
    out = []
    for (nodes, mode, event), items in grouped.items():
        n, mean, std = _agg([i["time_ms"] for i in items])
        out.append({
            "nodes": nodes,
            "mode": mode,
            "event": event,
            "n": len(items),
            "reps": len({i["rep"] for i in items}),
            "time_ms_mean": mean,
            "time_ms_std": std,
        })
    out.sort(key=lambda s: (_sort_key(s), s["event"]))
    return out


def _summarize_control_by_reason(rows: List[dict]) -> List[dict]:
    """Per (mode, reason) routing-event stats.

    Aggregates across time indices so seed-dependent events (e.g. all
    delay_update FIB changes) pool into one stable bucket. Robust to the
    exact tick at which a path change happens to land.
    """
    grouped: Dict[Tuple[Optional[int], str, str], List[dict]] = defaultdict(list)
    for r in rows:
        if not r["routing_event"]:
            continue
        grouped[(r["nodes"], r["mode"], r["reason"])].append(r)
    out = []
    for (nodes, mode, reason), items in grouped.items():
        n, mean, std = _agg([i["time_ms"] for i in items])
        _n_inst, inst_mean, inst_std = _agg(
            [i["installed"] for i in items if i["installed"] is not None])
        _nc, compute_mean, _cs = _agg(
            [i.get("compute_ms") for i in items if i.get("compute_ms") is not None])
        _ni, install_mean, _is = _agg(
            [i.get("install_ms") for i in items if i.get("install_ms") is not None])
        out.append({
            "nodes": nodes,
            "mode": mode,
            "reason": reason,
            "n": len(items),
            "reps": len({i["rep"] for i in items}),
            "time_ms_mean": mean,
            "time_ms_std": std,
            "compute_ms_mean": compute_mean,
            "install_ms_mean": install_mean,
            "installed_mean": inst_mean,
            "installed_std": inst_std,
        })
    out.sort(key=lambda s: (_sort_key(s), s["reason"]))
    return out


def _summarize_outage(rows: List[dict]) -> List[dict]:
    """Per (nodes, mode, reason) data-plane outage stats (pooled across time)."""
    grouped: Dict[Tuple[Optional[int], str, str], List[dict]] = defaultdict(list)
    for r in rows:
        grouped[(r["nodes"], r["mode"], r["reason"])].append(r)
    out = []
    for (nodes, mode, reason), items in grouped.items():
        n, mean, std = _agg([i["outage_ms"] for i in items])
        out.append({
            "nodes": nodes,
            "mode": mode,
            "reason": reason,
            "n": len(items),
            "reps": len({i["rep"] for i in items}),
            "outage_ms_mean": mean,
            "outage_ms_std": std,
            "still_down": sum(1 for i in items if i["still_down"]),
        })
    out.sort(key=lambda s: (_sort_key(s), s["reason"]))
    return out


def _print_ping_table(summary: List[dict]) -> None:
    print("\n" + "=" * 78)
    print("PING SUMMARY (mean +/- stddev over reps)")
    print("=" * 78)
    print(f"{'nodes':>5} {'mode':6} {'time':10} {'n':>3} "
          f"{'loss% (mean+/-sd)':>20} {'rtt ms (mean+/-sd)':>22}")
    for s in summary:
        loss = f"{_fmt(s['loss_mean_pct'])}+/-{_fmt(s['loss_std_pct'])}"
        rtt = f"{_fmt(s['rtt_mean_ms'])}+/-{_fmt(s['rtt_std_ms'])}"
        print(f"{str(s['nodes']):>5} {s['mode']:6} {s['time_tag']:10} {s['n']:>3} "
              f"{loss:>20} {rtt:>22}")


def _print_ping_by_phase_table(summary: List[dict]) -> None:
    print("\n" + "=" * 78)
    print("PING SUMMARY by phase (handover-relative; mean +/- stddev)")
    print("=" * 78)
    print(f"{'nodes':>5} {'mode':6} {'phase':14} {'n':>3} "
          f"{'loss% (mean+/-sd)':>20} {'rtt ms (mean+/-sd)':>22}")
    for s in summary:
        loss = f"{_fmt(s['loss_mean_pct'])}+/-{_fmt(s['loss_std_pct'])}"
        rtt = f"{_fmt(s['rtt_mean_ms'])}+/-{_fmt(s['rtt_std_ms'])}"
        print(f"{str(s['nodes']):>5} {s['mode']:6} {str(s['phase']):14} {s['n']:>3} "
              f"{loss:>20} {rtt:>22}")


def _print_control_table(summary: List[dict]) -> None:
    print("\n" + "=" * 78)
    print("CONTROL-PLANE SUMMARY by event (reason@time; mean +/- stddev)")
    print("=" * 78)
    print(f"{'nodes':>5} {'mode':6} {'event':24} {'n':>3} {'reps':>4} "
          f"{'time ms (mean+/-sd)':>24}")
    for s in summary:
        t = f"{_fmt(s['time_ms_mean'])}+/-{_fmt(s['time_ms_std'])}"
        print(f"{str(s['nodes']):>5} {s['mode']:6} {s['event']:24} "
              f"{s['n']:>3} {s['reps']:>4} {t:>24}")


def _print_control_by_reason_table(summary: List[dict]) -> None:
    print("\n" + "=" * 78)
    print("CONTROL-PLANE SUMMARY by reason (pooled across time; mean +/- stddev)")
    print("SDN time = compute (Dijkstra) + install (docker-exec push); "
          "OSPF time = dump cost")
    print("=" * 78)
    print(f"{'nodes':>5} {'mode':6} {'reason':16} {'n':>3} "
          f"{'time ms':>12} {'compute ms':>11} {'install ms':>11} {'installed':>10}")
    for s in summary:
        print(f"{str(s['nodes']):>5} {s['mode']:6} {s['reason']:16} {s['n']:>3} "
              f"{_fmt(s['time_ms_mean']):>12} {_fmt(s['compute_ms_mean']):>11} "
              f"{_fmt(s['install_ms_mean']):>11} {_fmt(s['installed_mean']):>10}")


def _print_outage_table(summary: List[dict]) -> None:
    print("\n" + "=" * 78)
    print("DATA-PLANE OUTAGE by reason (event -> recovery; mean +/- stddev)")
    print("Same probe for OSPF and SDN: time from event until first reply gets through")
    print("=" * 78)
    print(f"{'nodes':>5} {'mode':6} {'reason':18} {'n':>3} {'reps':>4} "
          f"{'outage ms (mean+/-sd)':>24} {'still_down':>10}")
    for s in summary:
        o = f"{_fmt(s['outage_ms_mean'])}+/-{_fmt(s['outage_ms_std'])}"
        print(f"{str(s['nodes']):>5} {s['mode']:6} {s['reason']:18} "
              f"{s['n']:>3} {s['reps']:>4} {o:>24} {s['still_down']:>10}")


def _print_timing_summary(
    run_times: List[dict],
    *,
    total_elapsed: float,
    batch_elapsed: float,
) -> None:
    """Print per-run and per-size wall-clock timing (bash ``time``-style)."""
    if not run_times:
        return

    print("\n" + "=" * 78)
    print("TIMING (wall clock)")
    print("=" * 78)
    print(f"{'grid':>8} {'mode':>6} {'rep':>4} {'elapsed':>12} {'seconds':>10}")
    size_totals: Dict[str, float] = defaultdict(float)
    for row in run_times:
        elapsed = row["elapsed_s"]
        grid = row["grid"]
        size_totals[grid] += elapsed
        print(
            f"{grid:>8} {row['mode']:>6} {row['rep']:>4} "
            f"{_format_elapsed(elapsed):>12} {elapsed:>10.2f}"
        )

    if len(size_totals) > 1 or len(run_times) > 1:
        print("-" * 78)
        print(f"{'grid':>8} {'':>6} {'':>4} {'total':>12} {'seconds':>10}")
        for grid in sorted(size_totals):
            total = size_totals[grid]
            print(
                f"{grid:>8} {'':>6} {'':>4} "
                f"{_format_elapsed(total):>12} {total:>10.2f}"
            )

    print("\n" + "_" * 56)
    print(
        f"Executed in {batch_elapsed:>9.2f} secs    experiment runs (wall clock)"
    )
    print(
        f"Executed in {total_elapsed:>9.2f} secs    total including aggregation"
    )
    print("_" * 56)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repeated seeded OSPF vs SDN runs with aggregated statistics")
    parser.add_argument(
        "--simulation",
        default="",
        help="Experiment plan JSON (simulation.json). When set, loads sizes, "
        "per-size durations, reps, out_dir, profile, modes, and base_seed "
        "unless overridden by explicit CLI flags below.",
    )
    parser.add_argument("--reps", type=int, default=None,
                        help="Repetitions per mode (default: simulation plan "
                        "or 5)")
    parser.add_argument("--modes", default=None,
                        help="Comma-separated modes (default: simulation plan "
                        "or ospf,sdn)")
    parser.add_argument("--profile", choices=("basic", "full"), default=None,
                        help="Experiment profile (default: simulation plan "
                        "or full)")
    parser.add_argument("--base-seed", type=int, default=None,
                        help="Seed for rep i is base-seed + i (default: "
                        "simulation plan or 1000)")
    parser.add_argument("--out-dir", default=None,
                        help="Directory for CSV outputs (default: "
                        "simulation plan or ./batch_results)")
    parser.add_argument(
        "--sizes", default="",
        help="Comma-separated grid sizes 'OxS' (e.g. '5x5,6x6,10x10'). "
        "Empty uses simulation plan constellations or the config default grid.")
    parser.add_argument(
        "--durations", default="",
        help="Per-size Duration (s), e.g. '6x6=120,8x8=300,12x12=600'. "
        "Overrides simulation plan when both are set.")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Merge raw CSVs into out-dir (replace rows for the same node count). "
        "Use when running one grid size at a time into a shared results folder.",
    )
    args = parser.parse_args()

    plan = None
    if args.simulation:
        try:
            plan = load_simulation_plan(args.simulation, _ROOT)
        except (FileNotFoundError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        print_plan(plan)

    reps = args.reps if args.reps is not None else (plan.reps if plan else 5)
    profile = (
        args.profile if args.profile is not None
        else (plan.profile if plan else "full")
    )
    base_seed = (
        args.base_seed if args.base_seed is not None
        else (plan.base_seed if plan else 1000)
    )
    if args.out_dir is not None:
        out_dir = args.out_dir
    elif plan is not None:
        out_dir = plan.out_dir
    else:
        out_dir = os.path.join(_ROOT, "batch_results")

    if args.modes is not None:
        modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    elif plan is not None:
        modes = list(plan.modes)
    else:
        modes = ["ospf", "sdn"]

    if args.sizes.strip():
        sizes = _parse_sizes(args.sizes)
    elif plan is not None:
        sizes = plan.size_list()
    else:
        sizes = _parse_sizes("")

    cli_durations = _parse_durations(args.durations)
    if cli_durations:
        duration_by_size = cli_durations
    elif plan is not None:
        duration_by_size = plan.duration_by_size()
    else:
        duration_by_size = {}

    os.makedirs(out_dir, exist_ok=True)

    main_started = time.perf_counter()

    if duration_by_size:
        print("Per-size durations (s):")
        for (o, s), d in sorted(duration_by_size.items()):
            print(f"  {o}x{s}: {d}")
        if plan and not cli_durations:
            missing = [
                f"{o}x{s}" for o, s in sizes
                if (o, s) not in duration_by_size
            ]
            if missing:
                print(f"  (no duration in plan: {', '.join(missing)} — "
                      "config Duration (s))")
        elif not plan and not cli_durations:
            print("  (other sizes: config Duration (s))")

    ping_rows: List[dict] = []
    control_rows: List[dict] = []
    outage_rows: List[dict] = []
    failures: List[str] = []
    missing: List[str] = []
    run_times: List[dict] = []

    batch_started = time.perf_counter()

    for orbits, sats in sizes:
        grid_key = (orbits, sats) if (orbits and sats) else None
        run_duration = (
            duration_by_size.get(grid_key) if grid_key is not None else None
        )
        for mode in modes:
            for rep in range(1, reps + 1):
                seed = base_seed + rep
                meta, elapsed = _run_one(
                    mode, profile, rep, seed, orbits, sats, run_duration)
                grid = f"{orbits}x{sats}" if orbits else "default"
                run_times.append({
                    "grid": grid,
                    "mode": mode,
                    "rep": rep,
                    "elapsed_s": elapsed,
                    "ok": meta is not None,
                })
                if meta is None:
                    failures.append(f"{mode}-{grid}-r{rep}")
                    continue
                nodes = meta["nodes"]
                pr = _collect_ping(
                    meta["artifact_dir"], mode, profile, rep, seed, nodes,
                    meta.get("handover_t"), meta.get("steady_t"))
                cr = _collect_control(
                    meta["artifact_dir"], mode, profile, rep, seed, nodes)
                outr = _collect_outage(
                    meta["artifact_dir"], mode, profile, rep, seed, nodes,
                    meta.get("pair"))
                missing.extend(
                    _check_missing(mode, nodes, rep, profile, pr, cr))
                ping_rows.extend(pr)
                control_rows.extend(cr)
                outage_rows.extend(outr)

    batch_elapsed = time.perf_counter() - batch_started

    if args.append:
        ping_rows, control_rows, outage_rows = _merge_appended_raw(
            out_dir, ping_rows, control_rows, outage_rows)

    ping_summary = _summarize_ping(ping_rows)
    ping_by_phase = _summarize_ping_by_phase(ping_rows)
    control_summary = _summarize_control(control_rows)
    control_by_reason = _summarize_control_by_reason(control_rows)
    outage_summary = _summarize_outage(outage_rows)

    _write_csv(
        os.path.join(out_dir, "ping_raw.csv"),
        ping_rows,
        PING_RAW_FIELDS,
    )
    _write_csv(
        os.path.join(out_dir, "ping_summary.csv"),
        ping_summary,
        ["nodes", "mode", "time_tag", "n", "loss_mean_pct", "loss_std_pct",
         "rtt_mean_ms", "rtt_std_ms", "n_rtt_samples"],
    )
    _write_csv(
        os.path.join(out_dir, "ping_summary_by_phase.csv"),
        ping_by_phase,
        ["nodes", "mode", "phase", "n", "loss_mean_pct", "loss_std_pct",
         "rtt_mean_ms", "rtt_std_ms", "n_rtt_samples"],
    )
    _write_csv(
        os.path.join(out_dir, "control_raw.csv"),
        control_rows,
        CONTROL_RAW_FIELDS,
    )
    _write_csv(
        os.path.join(out_dir, "control_summary.csv"),
        control_summary,
        ["nodes", "mode", "event", "n", "reps", "time_ms_mean", "time_ms_std"],
    )
    _write_csv(
        os.path.join(out_dir, "control_summary_by_reason.csv"),
        control_by_reason,
        ["nodes", "mode", "reason", "n", "reps", "time_ms_mean", "time_ms_std",
         "compute_ms_mean", "install_ms_mean", "installed_mean", "installed_std"],
    )
    _write_csv(
        os.path.join(out_dir, "outage_raw.csv"),
        outage_rows,
        OUTAGE_RAW_FIELDS,
    )
    _write_csv(
        os.path.join(out_dir, "outage_summary.csv"),
        outage_summary,
        ["nodes", "mode", "reason", "n", "reps", "outage_ms_mean",
         "outage_ms_std", "still_down"],
    )

    _print_ping_table(ping_summary)
    _print_ping_by_phase_table(ping_by_phase)
    _print_control_table(control_summary)
    _print_control_by_reason_table(control_by_reason)
    _print_outage_table(outage_summary)

    total_elapsed = time.perf_counter() - main_started
    _print_timing_summary(
        run_times,
        total_elapsed=total_elapsed,
        batch_elapsed=batch_elapsed,
    )

    print("\n" + "=" * 78)
    print(f"CSV outputs written to: {out_dir}")
    if missing:
        print(f"\nDATA-QUALITY WARNINGS ({len(missing)} missing sample(s)):")
        for w in missing:
            print(f"  - {w}")
    if failures:
        print(f"\nFAILED RUNS ({len(failures)}): {', '.join(failures)}")
    if not missing and not failures:
        print("All runs complete; no missing samples.")
    print("=" * 78)


if __name__ == "__main__":
    main()
