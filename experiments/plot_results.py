#!/usr/bin/env python3
"""
Generate journal figures from compare_batch / compare_scale CSV outputs.

The plotter only cares about the **standard summary CSV filenames** written by
``compare_batch.py`` into a single results directory (e.g. ``./scale_results``).
It does **not** read per-run artifact trees (``starlink-*-ospf-full-6x6-r3``,
etc.) — those names change every run; the aggregated CSVs are stable.

Usage:
  python experiments/plot_results.py --in-dir ./scale_results --out-dir ./figures
  make plots RESULTS=./scale_results FIGDIR=./figures
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

# Summary CSVs produced by compare_batch.py (stable names).
OUTAGE_SUMMARY = "outage_summary.csv"
OUTAGE_RAW = "outage_raw.csv"
PING_BY_PHASE = "ping_summary_by_phase.csv"
CONTROL_BY_REASON = "control_summary_by_reason.csv"

PHASE_ORDER = ("post_init", "handover", "post_handover", "steady")
MODE_COLORS = {"ospf": "#d62728", "sdn": "#1f77b4"}
MODE_LABELS = {"ospf": "OSPF", "sdn": "SDN"}

# Handover comparison yardsticks (see METRICS.md Phase 3).
OSPF_HANDOVER_OUTAGE = "topology_change"
SDN_HANDOVER_OUTAGE_PREF = "proactive_handover"
SDN_HANDOVER_OUTAGE_FALLBACK = "topology_change"
SDN_HANDOVER_INSTALL = "proactive_handover"


def _import_matplotlib():
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        print(
            "matplotlib is required for plotting. Install with:\n"
            "  pip install matplotlib\n"
            "or: make install-deps (after adding matplotlib to requirements)",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    return plt


def _float(val: object) -> Optional[float]:
    if val is None:
        return None
    text = str(val).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(val: object) -> Optional[int]:
    f = _float(val)
    return int(f) if f is not None else None


def read_table(path: str, required: Sequence[str]) -> List[dict]:
    """Load a CSV; raise FileNotFoundError or ValueError with a clear message."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: empty or headerless CSV")
        missing = [c for c in required if c not in reader.fieldnames]
        if missing:
            raise ValueError(
                f"{path}: missing columns {missing} "
                f"(have {reader.fieldnames})"
            )
        return list(reader)


def discover_results_dir(path: str) -> str:
    """Resolve and validate a results directory."""
    resolved = os.path.abspath(path)
    if not os.path.isdir(resolved):
        raise FileNotFoundError(f"Results directory not found: {resolved}")
    return resolved


def available_summaries(results_dir: str) -> Dict[str, str]:
    """Map logical name -> path for summary CSVs that exist."""
    mapping = {
        "outage": os.path.join(results_dir, OUTAGE_SUMMARY),
        "ping_phase": os.path.join(results_dir, PING_BY_PHASE),
        "control_reason": os.path.join(results_dir, CONTROL_BY_REASON),
    }
    return {k: v for k, v in mapping.items() if os.path.isfile(v)}


def _sdn_handover_outage_reason(rows: List[dict]) -> str:
    reasons = {
        r.get("reason") for r in rows
        if r.get("mode") == "sdn"
    }
    if SDN_HANDOVER_OUTAGE_PREF in reasons:
        return SDN_HANDOVER_OUTAGE_PREF
    return SDN_HANDOVER_OUTAGE_FALLBACK


def _filter(rows: List[dict], **kwargs) -> List[dict]:
    out = []
    for row in rows:
        if all(row.get(k) == v for k, v in kwargs.items()):
            out.append(row)
    return out


def _sorted_nodes(rows: List[dict]) -> List[int]:
    nodes = {_int(r["nodes"]) for r in rows if _int(r.get("nodes")) is not None}
    return sorted(n for n in nodes if n is not None)


def _save_figure(fig, out_dir: str, stem: str, dpi: int) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    written = []
    pdf = os.path.join(out_dir, f"{stem}.pdf")
    png = os.path.join(out_dir, f"{stem}.png")
    fig.savefig(pdf, bbox_inches="tight")
    fig.savefig(png, dpi=dpi, bbox_inches="tight")
    written.extend([pdf, png])
    return written


def _clip_lower_yerr(mean: float, std: float) -> float:
    """Keep error bars above zero when mean − std would be negative."""
    return min(std, max(0.0, mean))


def plot_outage_vs_nodes(
    rows: List[dict],
    out_dir: str,
    dpi: int,
) -> Optional[List[str]]:
    """Data-plane outage at handover: OSPF vs SDN (mean +/- stddev vs nodes)."""
    plt = _import_matplotlib()
    nodes = _sorted_nodes(rows)
    if not nodes:
        print("  skip outage_vs_nodes: no node data")
        return None

    sdn_reason = _sdn_handover_outage_reason(rows)
    series = [
        ("ospf", OSPF_HANDOVER_OUTAGE, "OSPF (topology change)"),
        ("sdn", sdn_reason, f"SDN ({sdn_reason.replace('_', ' ')})"),
    ]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    any_data = False
    for mode, reason, label in series:
        xs, ys, yerr = [], [], []
        for n in nodes:
            match = _filter(rows, nodes=str(n), mode=mode, reason=reason)
            if not match:
                continue
            row = match[0]
            mean = _float(row.get("outage_ms_mean"))
            if mean is None:
                continue
            std = _float(row.get("outage_ms_std")) or 0.0
            mean_s = mean / 1000.0
            std_s = std / 1000.0
            xs.append(n)
            ys.append(mean_s)
            yerr.append(_clip_lower_yerr(mean_s, std_s))
        if not xs:
            continue
        any_data = True
        ax.errorbar(
            xs, ys, yerr=yerr, marker="o", capsize=4, linewidth=2,
            label=label, color=MODE_COLORS.get(mode, None),
        )

    if not any_data:
        plt.close(fig)
        print("  skip outage_vs_nodes: no handover outage rows")
        return None

    ax.set_xlabel("Constellation size (nodes)")
    ax.set_ylabel("Data-plane outage at handover (s)")
    ax.set_title("Handover black-hole duration (continuous probe)")
    ax.set_ylim(bottom=0)
    ax.legend()
    ax.grid(True, alpha=0.3)
    if len(nodes) > 1:
        ax.set_xticks(nodes)
    return _save_figure(fig, out_dir, "outage_vs_nodes", dpi)


DAMAGE_RECOVERY = "damage_recovery"
DAMAGE_TIME = 5
RECOVERY_TIME = 10


def _aggregate_outage_raw(
    rows: List[dict],
    time_index: int,
) -> Dict[Tuple[str, str], Tuple[float, float]]:
    """Return {(nodes, mode): (mean_ms, std_ms)} for one damage/recovery tick."""
    grouped: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for row in rows:
        if row.get("reason") != DAMAGE_RECOVERY:
            continue
        if _int(row.get("time_index")) != time_index:
            continue
        val = _float(row.get("outage_ms"))
        if val is None:
            continue
        grouped[(row["nodes"], row["mode"])].append(val)

    out: Dict[Tuple[str, str], Tuple[float, float]] = {}
    for key, values in grouped.items():
        mean = statistics.fmean(values)
        std = statistics.stdev(values) if len(values) >= 2 else 0.0
        out[key] = (mean, std)
    return out


def _plot_outage_panel(
    ax,
    agg: Dict[Tuple[str, str], Tuple[float, float]],
    nodes: List[int],
    *,
    title: str,
) -> bool:
    """One panel: OSPF vs SDN outage at a fixed sim tick."""
    any_data = False
    for mode, label in (("ospf", "OSPF"), ("sdn", "SDN")):
        xs, ys, yerr = [], [], []
        for n in nodes:
            key = (str(n), mode)
            if key not in agg:
                continue
            mean_ms, std_ms = agg[key]
            mean_s = mean_ms / 1000.0
            std_s = std_ms / 1000.0
            xs.append(n)
            ys.append(mean_s)
            yerr.append(_clip_lower_yerr(mean_s, std_s))
        if not xs:
            continue
        any_data = True
        ax.errorbar(
            xs, ys, yerr=yerr, marker="o", capsize=4, linewidth=2,
            label=label, color=MODE_COLORS.get(mode, None),
        )
    ax.set_title(title)
    ax.set_xlabel("Constellation size (nodes)")
    ax.grid(True, alpha=0.3)
    ax.set_ylim(bottom=0)
    if len(nodes) > 1:
        ax.set_xticks(nodes)
    return any_data


def plot_outage_damage_recovery(
    raw_rows: List[dict],
    out_dir: str,
    dpi: int,
) -> Optional[List[str]]:
    """
    Side-by-side damage (t=5) vs link recovery (t=10) data-plane outage.

    The pooled ``damage_recovery`` summary mixed two different events; this
    figure keeps them separate.
    """
    plt = _import_matplotlib()
    nodes = sorted({
        _int(r["nodes"]) for r in raw_rows
        if r.get("reason") == DAMAGE_RECOVERY and _int(r.get("nodes")) is not None
    })
    if not nodes:
        print("  skip outage_damage_recovery: no raw damage_recovery rows")
        return None

    damage_agg = _aggregate_outage_raw(raw_rows, DAMAGE_TIME)
    recovery_agg = _aggregate_outage_raw(raw_rows, RECOVERY_TIME)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    left_ok = _plot_outage_panel(
        axes[0], damage_agg, nodes,
        title=f"Link damage (t={DAMAGE_TIME})",
    )
    right_ok = _plot_outage_panel(
        axes[1], recovery_agg, nodes,
        title=f"Link recovery (t={RECOVERY_TIME})",
    )

    if not left_ok and not right_ok:
        plt.close(fig)
        print("  skip outage_damage_recovery: no plottable values")
        return None

    axes[0].set_ylabel("Data-plane outage (s)")
    axes[1].legend(loc="best")
    fig.suptitle(
        "Damage vs recovery: data-plane black-hole duration (continuous probe)",
        y=1.02,
    )
    fig.tight_layout()
    return _save_figure(fig, out_dir, "outage_damage_recovery", dpi)


def _plot_by_phase(
    rows: List[dict],
    out_dir: str,
    dpi: int,
    *,
    mean_key: str,
    std_key: str,
    ylabel: str,
    title: str,
    stem: str,
    ylim: Optional[Tuple[float, float]] = None,
) -> Optional[List[str]]:
    """Shared layout for phase × constellation size (RTT, loss, …)."""
    plt = _import_matplotlib()
    nodes = _sorted_nodes(rows)
    if not nodes:
        print(f"  skip {stem}: no node data")
        return None

    phases = [p for p in PHASE_ORDER if _filter(rows, phase=p)]
    if not phases:
        print(f"  skip {stem}: no phase rows")
        return None

    fig, axes = plt.subplots(
        1, len(phases), figsize=(3.2 * len(phases), 4.2),
        sharey=True, squeeze=False,
    )
    any_data = False
    for ax, phase in zip(axes[0], phases):
        for mode in ("ospf", "sdn"):
            xs, ys, yerr = [], [], []
            for n in nodes:
                match = _filter(rows, nodes=str(n), mode=mode, phase=phase)
                if not match:
                    continue
                row = match[0]
                mean = _float(row.get(mean_key))
                if mean is None:
                    continue
                std = _float(row.get(std_key)) or 0.0
                xs.append(n)
                ys.append(mean)
                yerr.append(std)
            if not xs:
                continue
            any_data = True
            ax.errorbar(
                xs, ys, yerr=yerr, marker="o", capsize=3, linewidth=1.5,
                label=MODE_LABELS[mode], color=MODE_COLORS[mode],
            )
        ax.set_title(phase.replace("_", " "))
        ax.set_xlabel("nodes")
        ax.grid(True, alpha=0.3)
        if len(nodes) > 1:
            ax.set_xticks(nodes)

    if not any_data:
        plt.close(fig)
        print(f"  skip {stem}: no plottable values")
        return None

    axes[0, 0].set_ylabel(ylabel)
    if ylim is not None:
        axes[0, 0].set_ylim(*ylim)
    axes[0, -1].legend(loc="best")
    fig.suptitle(title, y=1.02)
    fig.tight_layout()
    return _save_figure(fig, out_dir, stem, dpi)


def plot_rtt_by_phase(
    rows: List[dict],
    out_dir: str,
    dpi: int,
) -> Optional[List[str]]:
    """RTT by handover-relative phase vs constellation size."""
    return _plot_by_phase(
        rows, out_dir, dpi,
        mean_key="rtt_mean_ms",
        std_key="rtt_std_ms",
        ylabel="RTT (ms)",
        title="Ping RTT by phase (mean ± stddev)",
        stem="rtt_by_phase",
    )


def plot_loss_by_phase(
    rows: List[dict],
    out_dir: str,
    dpi: int,
) -> Optional[List[str]]:
    """Packet loss by handover-relative phase vs constellation size."""
    return _plot_by_phase(
        rows, out_dir, dpi,
        mean_key="loss_mean_pct",
        std_key="loss_std_pct",
        ylabel="Packet loss (%)",
        title="Ping loss by phase (mean ± stddev)",
        stem="loss_by_phase",
        ylim=(0, 105),
    )


def plot_control_handover(
    rows: List[dict],
    out_dir: str,
    dpi: int,
) -> Optional[List[str]]:
    """
    Handover control-plane cost: grouped bars (log scale).

    OSPF ``time_ms`` is route-dump collection time (~10² ms). SDN splits into
    Dijkstra compute (~10⁰ ms) and docker-exec install (~10³ ms). A stacked bar
    hid compute entirely; log-scale grouped bars keep all three visible.
    """
    plt = _import_matplotlib()
    import numpy as np

    nodes = _sorted_nodes(rows)
    if not nodes:
        print("  skip control_handover: no node data")
        return None

    sdn_reasons = {r.get("reason") for r in rows if r.get("mode") == "sdn"}
    sdn_reason = (
        SDN_HANDOVER_INSTALL if SDN_HANDOVER_INSTALL in sdn_reasons
        else SDN_HANDOVER_OUTAGE_FALLBACK
    )

    ospf_y, ospf_err = [], []
    compute_y, install_y = [], []
    plot_nodes: List[int] = []

    for n in nodes:
        ospf_row = _filter(rows, nodes=str(n), mode="ospf", reason=OSPF_HANDOVER_OUTAGE)
        sdn_row = _filter(rows, nodes=str(n), mode="sdn", reason=sdn_reason)
        if not ospf_row and not sdn_row:
            continue
        plot_nodes.append(n)
        if ospf_row:
            ospf_y.append(_float(ospf_row[0].get("time_ms_mean")) or np.nan)
            ospf_err.append(_float(ospf_row[0].get("time_ms_std")) or 0.0)
        else:
            ospf_y.append(np.nan)
            ospf_err.append(0.0)
        if sdn_row:
            compute_y.append(_float(sdn_row[0].get("compute_ms_mean")) or np.nan)
            install_y.append(_float(sdn_row[0].get("install_ms_mean")) or np.nan)
        else:
            compute_y.append(np.nan)
            install_y.append(np.nan)

    if not plot_nodes:
        print("  skip control_handover: no control rows")
        return None

    x = np.arange(len(plot_nodes), dtype=float)
    width = 0.25
    fig, ax = plt.subplots(figsize=(8, 4.8))

    ospf_arr = np.array(ospf_y, dtype=float)
    comp_arr = np.array(compute_y, dtype=float)
    inst_arr = np.array(install_y, dtype=float)

    bars_ospf = ax.bar(
        x - width, ospf_arr, width, yerr=ospf_err, capsize=3,
        label="OSPF route dump", color=MODE_COLORS["ospf"],
        edgecolor="white", linewidth=0.5,
    )
    bars_comp = ax.bar(
        x, comp_arr, width, label="SDN compute (Dijkstra)",
        color="#9ecae1", edgecolor="white", linewidth=0.5,
    )
    bars_inst = ax.bar(
        x + width, inst_arr, width, label="SDN install (docker-exec)",
        color=MODE_COLORS["sdn"], edgecolor="white", linewidth=0.5,
    )

    ax.set_yscale("log")
    ax.set_ylabel("Control-plane time at handover (ms, log scale)")
    ax.set_xlabel("Constellation size (nodes)")
    ax.set_title("Handover control-plane cost")
    ax.set_xticks(x)
    ax.set_xticklabels([str(n) for n in plot_nodes])
    ax.legend(loc="upper left")
    ax.grid(True, axis="y", which="both", alpha=0.3)

    # Label bars when tall enough to read; always label tiny compute bars.
    for bars, values in (
        (bars_ospf, ospf_arr),
        (bars_comp, comp_arr),
        (bars_inst, inst_arr),
    ):
        for bar, val in zip(bars, values):
            if np.isnan(val) or val <= 0:
                continue
            height = bar.get_height()
            if bars is bars_comp or height >= 500:
                ax.annotate(
                    f"{val:.0f}" if val >= 10 else f"{val:.1f}",
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", fontsize=7,
                )

    fig.text(
        0.5, -0.02,
        "OSPF has no explicit install step; data-plane impact is in outage_vs_nodes.",
        ha="center", fontsize=8, style="italic",
    )
    fig.tight_layout()
    return _save_figure(fig, out_dir, "control_handover", dpi)


def plot_routes_installed_handover(
    rows: List[dict],
    out_dir: str,
    dpi: int,
) -> Optional[List[str]]:
    """Routes pushed at handover (SDN installed_mean vs FIB scale)."""
    plt = _import_matplotlib()
    nodes = _sorted_nodes(rows)
    sdn_rows = [
        r for r in rows
        if r.get("mode") == "sdn" and r.get("reason") == SDN_HANDOVER_INSTALL
    ]
    if not sdn_rows:
        sdn_rows = _filter(rows, mode="sdn", reason=SDN_HANDOVER_OUTAGE_FALLBACK)
    if not sdn_rows or not nodes:
        print("  skip routes_installed_handover: no SDN handover install rows")
        return None

    fig, ax = plt.subplots(figsize=(7, 4.5))
    xs, ys, yerr = [], [], []
    for n in nodes:
        match = [r for r in sdn_rows if _int(r.get("nodes")) == n]
        if not match:
            continue
        mean = _float(match[0].get("installed_mean"))
        if mean is None:
            continue
        xs.append(n)
        ys.append(mean)
        yerr.append(_float(match[0].get("installed_std")) or 0.0)

    if not xs:
        plt.close(fig)
        print("  skip routes_installed_handover: no installed_mean values")
        return None

    ax.errorbar(
        xs, ys, yerr=yerr, marker="o", capsize=4, linewidth=2,
        color=MODE_COLORS["sdn"], label="SDN routes installed (handover)",
    )
    ax.set_xlabel("Constellation size (nodes)")
    ax.set_ylabel("Kernel routes pushed")
    ax.set_title("Incremental install size at handover")
    ax.legend()
    ax.grid(True, alpha=0.3)
    if len(nodes) > 1:
        ax.set_xticks(nodes)
    return _save_figure(fig, out_dir, "routes_installed_handover", dpi)


def generate_figures(
    results_dir: str,
    out_dir: str,
    *,
    dpi: int = 200,
) -> List[str]:
    """Plot every figure supported by the summary CSVs present in results_dir."""
    summaries = available_summaries(results_dir)
    if not summaries:
        raise FileNotFoundError(
            f"No summary CSVs found in {results_dir}. "
            f"Expected at least one of: {OUTAGE_SUMMARY}, {PING_BY_PHASE}, "
            f"{CONTROL_BY_REASON}. Run 'make scale' or 'make batch' first."
        )

    print(f"Results: {results_dir}")
    print(f"Found: {', '.join(sorted(summaries))}")
    print(f"Output:  {out_dir}")

    written: List[str] = []
    if "outage" in summaries:
        rows = read_table(
            summaries["outage"],
            ["nodes", "mode", "reason", "outage_ms_mean", "outage_ms_std"],
        )
        paths = plot_outage_vs_nodes(rows, out_dir, dpi)
        if paths:
            written.extend(paths)

    raw_path = os.path.join(results_dir, OUTAGE_RAW)
    if os.path.isfile(raw_path):
        raw_rows = read_table(
            raw_path,
            ["nodes", "mode", "reason", "time_index", "outage_ms"],
        )
        paths = plot_outage_damage_recovery(raw_rows, out_dir, dpi)
        if paths:
            written.extend(paths)

    if "ping_phase" in summaries:
        rows = read_table(
            summaries["ping_phase"],
            ["nodes", "mode", "phase", "rtt_mean_ms", "rtt_std_ms",
             "loss_mean_pct", "loss_std_pct"],
        )
        paths = plot_rtt_by_phase(rows, out_dir, dpi)
        if paths:
            written.extend(paths)
        paths = plot_loss_by_phase(rows, out_dir, dpi)
        if paths:
            written.extend(paths)

    if "control_reason" in summaries:
        rows = read_table(
            summaries["control_reason"],
            ["nodes", "mode", "reason", "time_ms_mean", "time_ms_std",
             "compute_ms_mean", "install_ms_mean", "installed_mean",
             "installed_std"],
        )
        for plot_fn in (plot_control_handover, plot_routes_installed_handover):
            paths = plot_fn(rows, out_dir, dpi)
            if paths:
                written.extend(paths)

    if not written:
        raise RuntimeError(
            "No figures produced — CSVs exist but contained no plottable rows."
        )
    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate figures from compare_batch summary CSVs")
    parser.add_argument(
        "--in-dir", required=True,
        help="Directory with outage_summary.csv, ping_summary_by_phase.csv, etc.",
    )
    parser.add_argument(
        "--out-dir", default=os.path.join(os.path.dirname(__file__), "..", "figures"),
        help="Directory for PDF/PNG outputs (default ./figures)",
    )
    parser.add_argument("--dpi", type=int, default=200, help="PNG resolution")
    args = parser.parse_args()

    results_dir = discover_results_dir(args.in_dir)
    out_dir = os.path.abspath(args.out_dir)
    paths = generate_figures(results_dir, out_dir, dpi=args.dpi)
    print("\nWrote:")
    for p in sorted(set(paths)):
        print(f"  {p}")


if __name__ == "__main__":
    main()
