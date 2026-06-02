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
  python experiments/compare_batch.py --reps 3 --out-dir ./batch_results

Outputs (under --out-dir):
  ping_raw.csv             one row per (mode, rep, ping time tag), incl. phase
  ping_summary.csv         mean/stddev of loss/RTT per (nodes, mode, time tag)
  ping_summary_by_phase.csv mean/stddev per (nodes, mode, handover-relative phase)
  control_raw.csv          one row per (mode, rep, control-plane snapshot)
  control_summary.csv      mean/stddev of control-plane time per (nodes, mode, event)
  control_summary_by_reason.csv pooled per (nodes, mode, reason)
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
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from compare_summarize import (  # noqa: E402  (path set above)
    _is_routing_event,
    _load_snapshots,
    _parse_ping,
)

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


def _run_one(
    mode: str,
    profile: str,
    rep: int,
    seed: int,
    orbits: Optional[int],
    sats: Optional[int],
) -> Optional[dict]:
    """Run a single experiment in a subprocess; return run metadata dict."""
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
    print(f"\n{'=' * 70}")
    print(f">>> {mode.upper()} grid={grid} rep {rep} (seed={seed}) suffix={suffix}")
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

    if proc.returncode != 0:
        print(f"!!! {mode} grid={grid} rep {rep} exited with code {proc.returncode}")
        return None
    if artifact_dir is None or not os.path.isdir(artifact_dir):
        print(f"!!! {mode} grid={grid} rep {rep}: artifact dir not found")
        return None
    return {
        "artifact_dir": artifact_dir,
        "nodes": nodes,
        "pair": pair,
        "handover_t": handover_t,
        "steady_t": steady_t,
    }


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
            "routing_event": _is_routing_event(snap),
            "installed": snap.get("installed"),
            "fib_unchanged": snap.get("fib_unchanged"),
            "bird_route_ok": snap.get("bird_route_ok"),
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
        out.append({
            "nodes": nodes,
            "mode": mode,
            "reason": reason,
            "n": len(items),
            "reps": len({i["rep"] for i in items}),
            "time_ms_mean": mean,
            "time_ms_std": std,
            "installed_mean": inst_mean,
            "installed_std": inst_std,
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
    print("=" * 78)
    print(f"{'nodes':>5} {'mode':6} {'reason':18} {'n':>3} {'reps':>4} "
          f"{'time ms (mean+/-sd)':>22} {'installed (mean+/-sd)':>22}")
    for s in summary:
        t = f"{_fmt(s['time_ms_mean'])}+/-{_fmt(s['time_ms_std'])}"
        inst = f"{_fmt(s['installed_mean'])}+/-{_fmt(s['installed_std'])}"
        print(f"{str(s['nodes']):>5} {s['mode']:6} {s['reason']:18} "
              f"{s['n']:>3} {s['reps']:>4} {t:>22} {inst:>22}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repeated seeded OSPF vs SDN runs with aggregated statistics")
    parser.add_argument("--reps", type=int, default=5,
                        help="Repetitions per mode (default 5)")
    parser.add_argument("--modes", default="ospf,sdn",
                        help="Comma-separated modes (default ospf,sdn)")
    parser.add_argument("--profile", choices=("basic", "full"), default="full",
                        help="Experiment profile (default full)")
    parser.add_argument("--base-seed", type=int, default=1000,
                        help="Seed for rep i is base-seed + i (default 1000)")
    parser.add_argument("--out-dir", default=os.path.join(_ROOT, "batch_results"),
                        help="Directory for CSV outputs (default ./batch_results)")
    parser.add_argument(
        "--sizes", default="",
        help="Comma-separated grid sizes 'OxS' (e.g. '5x5,6x6,10x10'). "
        "Empty uses the config's default grid.")
    args = parser.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    sizes = _parse_sizes(args.sizes)
    os.makedirs(args.out_dir, exist_ok=True)

    ping_rows: List[dict] = []
    control_rows: List[dict] = []
    failures: List[str] = []
    missing: List[str] = []

    for orbits, sats in sizes:
        for mode in modes:
            for rep in range(1, args.reps + 1):
                seed = args.base_seed + rep
                meta = _run_one(mode, args.profile, rep, seed, orbits, sats)
                grid = f"{orbits}x{sats}" if orbits else "default"
                if meta is None:
                    failures.append(f"{mode}-{grid}-r{rep}")
                    continue
                nodes = meta["nodes"]
                pr = _collect_ping(
                    meta["artifact_dir"], mode, args.profile, rep, seed, nodes,
                    meta.get("handover_t"), meta.get("steady_t"))
                cr = _collect_control(
                    meta["artifact_dir"], mode, args.profile, rep, seed, nodes)
                missing.extend(
                    _check_missing(mode, nodes, rep, args.profile, pr, cr))
                ping_rows.extend(pr)
                control_rows.extend(cr)

    ping_summary = _summarize_ping(ping_rows)
    ping_by_phase = _summarize_ping_by_phase(ping_rows)
    control_summary = _summarize_control(control_rows)
    control_by_reason = _summarize_control_by_reason(control_rows)

    _write_csv(
        os.path.join(args.out_dir, "ping_raw.csv"),
        ping_rows,
        ["mode", "profile", "nodes", "rep", "seed", "time_tag", "phase",
         "loss_pct", "avg_rtt_ms"],
    )
    _write_csv(
        os.path.join(args.out_dir, "ping_summary.csv"),
        ping_summary,
        ["nodes", "mode", "time_tag", "n", "loss_mean_pct", "loss_std_pct",
         "rtt_mean_ms", "rtt_std_ms", "n_rtt_samples"],
    )
    _write_csv(
        os.path.join(args.out_dir, "ping_summary_by_phase.csv"),
        ping_by_phase,
        ["nodes", "mode", "phase", "n", "loss_mean_pct", "loss_std_pct",
         "rtt_mean_ms", "rtt_std_ms", "n_rtt_samples"],
    )
    _write_csv(
        os.path.join(args.out_dir, "control_raw.csv"),
        control_rows,
        ["mode", "profile", "nodes", "rep", "seed", "time_index", "reason",
         "event", "time_ms", "routing_event", "installed", "fib_unchanged",
         "bird_route_ok"],
    )
    _write_csv(
        os.path.join(args.out_dir, "control_summary.csv"),
        control_summary,
        ["nodes", "mode", "event", "n", "reps", "time_ms_mean", "time_ms_std"],
    )
    _write_csv(
        os.path.join(args.out_dir, "control_summary_by_reason.csv"),
        control_by_reason,
        ["nodes", "mode", "reason", "n", "reps", "time_ms_mean", "time_ms_std",
         "installed_mean", "installed_std"],
    )

    _print_ping_table(ping_summary)
    _print_ping_by_phase_table(ping_by_phase)
    _print_control_table(control_summary)
    _print_control_by_reason_table(control_by_reason)

    print("\n" + "=" * 78)
    print(f"CSV outputs written to: {args.out_dir}")
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
