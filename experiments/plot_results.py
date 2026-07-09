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
PING_RAW = "ping_raw.csv"
CONTROL_BY_REASON = "control_summary_by_reason.csv"
CONTROL_RAW = "control_raw.csv"
EVENT_CLASS = "event_class.csv"

# Handover-type control reasons and their event classification (see
# experiments/classify_events.py): a coverage-preserving handover swaps a
# ground station's serving satellite while a replacement is in view; a coverage
# gap leaves a ground station with no satellite in view, so no protocol can route.
HANDOVER_REASONS = ("topology_change", "proactive_handover")
CLASS_LABELS = {
    "handover": "Coverage-preserving GSL handover",
    "coverage_gap": "Coverage-gap event (no satellite in view)",
}

PHASE_ORDER = ("post_init", "handover", "post_handover", "steady")
PHASE_TITLES = {
    "post_init": "Post-init",
    "handover": "Handover",
    "post_handover": "Post-handover",
    "steady": "Steady",
}
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
    # Consistent, paper-legible typography across every figure. Sizes are chosen
    # so that titles, axis labels, ticks, and legends remain readable after the
    # figure is scaled to column (or text) width in the two-column IEEE layout.
    plt.rcParams.update({
        "font.size": 14,
        "axes.titlesize": 16,
        "axes.labelsize": 15,
        "xtick.labelsize": 13,
        "ytick.labelsize": 13,
        "legend.fontsize": 13,
        "figure.titlesize": 17,
    })
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


def _truthy(val: object) -> bool:
    return str(val).strip().lower() in ("true", "1", "yes")


def _first_event_rows_per_rep(
    rows: List[dict],
    reason: str,
) -> List[dict]:
    """One row per (nodes, mode, rep): earliest ``time_index`` for ``reason``."""
    best: Dict[Tuple[str, str, str], dict] = {}
    for row in rows:
        if row.get("reason") != reason:
            continue
        nodes, mode, rep = row.get("nodes"), row.get("mode"), row.get("rep")
        ti = _int(row.get("time_index"))
        if nodes is None or mode is None or rep is None or ti is None:
            continue
        key = (nodes, mode, rep)
        prev = best.get(key)
        if prev is None or ti < _int(prev.get("time_index")):
            best[key] = row
    return list(best.values())


def _spread(values: List[float], *, use_median: bool) -> float:
    if len(values) < 2:
        return 0.0
    if use_median:
        med = statistics.median(values)
        return statistics.median([abs(v - med) for v in values])
    return statistics.stdev(values)


def _aggregate_numeric_by_nodes_mode(
    rows: List[dict],
    value_key: str,
    *,
    skip_still_down: bool = True,
    use_median: bool = False,
) -> Dict[Tuple[str, str], Tuple[float, float, int]]:
    """Return {(nodes, mode): (center, spread, n)} from per-rep values."""
    grouped: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for row in rows:
        if skip_still_down and _truthy(row.get("still_down")):
            continue
        val = _float(row.get(value_key))
        if val is None:
            continue
        grouped[(row["nodes"], row["mode"])].append(val)

    out: Dict[Tuple[str, str], Tuple[float, float, int]] = {}
    for key, values in grouped.items():
        if use_median:
            center = statistics.median(values)
            spread = _spread(values, use_median=True)
        else:
            center = statistics.fmean(values)
            spread = _spread(values, use_median=False)
        out[key] = (center, spread, len(values))
    return out


def _ping_phase_plottable(row: dict, value_key: str) -> bool:
    """Skip truncated-run rows (100% steady loss with no RTT samples)."""
    if row.get("phase") == "steady":
        loss = _float(row.get("loss_mean_pct"))
        n_rtt = _int(row.get("n_rtt_samples")) or 0
        if loss is not None and loss >= 100.0 and n_rtt == 0:
            return False
    return _float(row.get(value_key)) is not None


def _draw_violin_box_strip(
    ax,
    pos: float,
    values: List[float],
    color: str,
    *,
    width: float = 0.6,
    jitter: float = 0.05,
    seed: int = 0,
) -> None:
    """Draw a violin (density) + inner box (median/IQR) + jittered strip (every rep).

    With few repetitions a kernel density oversmooths, so the individual points
    are always drawn on top: the reader sees the real sample behind the shape.
    A degenerate group (all identical values, e.g. SDN's 0 ms) draws only the
    dots, honestly showing a point mass instead of an invented density.
    """
    import numpy as np

    vals = [float(v) for v in values if v is not None]
    if not vals:
        return
    arr = np.asarray(vals, dtype=float)

    if len(arr) >= 2 and float(np.ptp(arr)) > 0:
        parts = ax.violinplot(
            [arr], positions=[pos], widths=width,
            showmeans=False, showextrema=False, showmedians=False,
        )
        for body in parts["bodies"]:
            body.set_facecolor(color)
            body.set_edgecolor(color)
            body.set_alpha(0.25)
        bp = ax.boxplot(
            [arr], positions=[pos], widths=width * 0.32,
            patch_artist=True, showfliers=False, manage_ticks=False,
            medianprops=dict(color="black", linewidth=1.3),
        )
        for patch in bp["boxes"]:
            patch.set_facecolor("white")
            patch.set_alpha(0.9)
            patch.set_edgecolor(color)
        for artist in bp["whiskers"] + bp["caps"]:
            artist.set_color(color)

    rng = np.random.default_rng(seed)
    xs = pos + rng.uniform(-jitter, jitter, size=len(arr))
    ax.scatter(
        xs, arr, s=16, color=color, edgecolor="black", linewidth=0.3,
        alpha=0.7, zorder=4,
    )


def _first_outage_values_by_nodes_mode(
    raw_rows: List[dict],
    reason: str,
    mode: str,
) -> Dict[int, List[float]]:
    """Per-rep first-handover outage (seconds) for one mode, keyed by node count."""
    rows = _first_event_rows_per_rep(raw_rows, reason)
    out: Dict[int, List[float]] = defaultdict(list)
    for row in rows:
        if row.get("mode") != mode:
            continue
        if _truthy(row.get("still_down")):
            continue
        n = _int(row.get("nodes"))
        val = _float(row.get("outage_ms"))
        if n is None or val is None:
            continue
        out[n].append(val / 1000.0)
    return out


def _plot_outage_series(
    ax,
    nodes: List[int],
    agg: Dict[Tuple[str, str], Tuple[float, float, int]],
    *,
    mode: str,
    label: str,
) -> bool:
    xs, ys, yerr = [], [], []
    for n in nodes:
        key = (str(n), mode)
        if key not in agg:
            continue
        mean_ms, std_ms, _n = agg[key]
        mean_s = mean_ms / 1000.0
        std_s = std_ms / 1000.0
        xs.append(n)
        ys.append(mean_s)
        yerr.append(_clip_lower_yerr(mean_s, std_s))
    if not xs:
        return False
    ax.errorbar(
        xs, ys, yerr=yerr, marker="o", capsize=4, linewidth=2,
        label=label, color=MODE_COLORS.get(mode, None),
    )
    return True


def plot_outage_vs_nodes(
    rows: List[dict],
    out_dir: str,
    dpi: int,
    raw_rows: Optional[List[dict]] = None,
) -> Optional[List[str]]:
    """Data-plane outage at first handover per rep (not pooled multi-handover).

    With per-rep raw data, each constellation size shows the full distribution
    of the ten repetitions as a violin + box + jittered strip, so OSPF's spread
    and SDN's zero point mass are both visible; otherwise falls back to a
    mean+/-stddev summary series.
    """
    plt = _import_matplotlib()
    import numpy as np

    nodes = _sorted_nodes(rows if raw_rows is None else raw_rows)
    if not nodes:
        print("  skip outage_vs_nodes: no node data")
        return None

    sdn_reason = _sdn_handover_outage_reason(rows)

    if raw_rows:
        ospf_vals = _first_outage_values_by_nodes_mode(
            raw_rows, OSPF_HANDOVER_OUTAGE, "ospf")
        sdn_vals = _first_outage_values_by_nodes_mode(
            raw_rows, sdn_reason, "sdn")
        if not ospf_vals and not sdn_vals:
            print("  skip outage_vs_nodes: no handover outage rows")
            return None

        fig, ax = plt.subplots(figsize=(6.0, 4.2))
        offset = 0.2
        for i, n in enumerate(nodes):
            _draw_violin_box_strip(
                ax, i - offset, ospf_vals.get(n, []),
                MODE_COLORS["ospf"], width=0.34, seed=n,
            )
            _draw_violin_box_strip(
                ax, i + offset, sdn_vals.get(n, []),
                MODE_COLORS["sdn"], width=0.34, seed=n + 1,
            )
        ax.set_xticks(range(len(nodes)))
        ax.set_xticklabels([str(n) for n in nodes])
        handles = [
            plt.Line2D([0], [0], marker="o", linestyle="none",
                       color=MODE_COLORS["ospf"], label="OSPF (topology change)"),
            plt.Line2D([0], [0], marker="o", linestyle="none",
                       color=MODE_COLORS["sdn"], label="SDN (proactive handover)"),
        ]
        ax.legend(handles=handles, loc="upper left")
    else:
        fig, ax = plt.subplots(figsize=(6.0, 4.2))
        any_data = False
        series = [
            ("ospf", OSPF_HANDOVER_OUTAGE, "OSPF (topology change)"),
            ("sdn", sdn_reason, f"SDN ({sdn_reason.replace('_', ' ')})"),
        ]
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
                xs.append(n)
                ys.append(mean / 1000.0)
                yerr.append(_clip_lower_yerr(mean / 1000.0, std / 1000.0))
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
        ax.legend()
        if len(nodes) > 1:
            ax.set_xticks(nodes)

    ax.set_xlabel("Constellation size (nodes)")
    ax.set_ylabel("Data-plane outage at handover (s)")
    ax.set_title("Handover black-hole duration — first handover per run")
    ax.set_ylim(bottom=-0.3)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    return _save_figure(fig, out_dir, "outage_vs_nodes", dpi)


def plot_outage_ecdf(
    raw_rows: List[dict],
    out_dir: str,
    dpi: int,
    class_map: Optional[Dict[Tuple[int, int], str]] = None,
) -> Optional[List[str]]:
    """Empirical CDF of handover data-plane outage, pooled across sizes.

    Reads as: the fraction of handover repetitions completed within a given
    outage. SDN rises vertically at 0 ms (every handover seamless); OSPF's
    curve extends across seconds, exposing the black-hole tail.

    When an event classification is available, the ECDF pools the worst
    coverage-preserving handover per repetition (excluding coverage-gap events,
    which are a physical-connectivity limitation shown separately); otherwise it
    falls back to the first handover per repetition.
    """
    plt = _import_matplotlib()
    import numpy as np

    if class_map:
        agg = _worst_outage_by_class(raw_rows, class_map)

        def _pooled_mode(mode: str) -> np.ndarray:
            vals: List[float] = []
            for (_n, m, et), lst in agg.items():
                if m == mode and et == "handover":
                    vals.extend(lst)
            return np.sort(np.asarray(vals, dtype=float))

        ospf = _pooled_mode("ospf")
        sdn = _pooled_mode("sdn")
    else:
        sdn_reason = _sdn_handover_outage_reason(raw_rows)

        def _pooled(reason: str, mode: str) -> np.ndarray:
            by_nodes = _first_outage_values_by_nodes_mode(raw_rows, reason, mode)
            vals: List[float] = []
            for lst in by_nodes.values():
                vals.extend(lst)
            return np.sort(np.asarray(vals, dtype=float))

        ospf = _pooled(OSPF_HANDOVER_OUTAGE, "ospf")
        sdn = _pooled(sdn_reason, "sdn")

    if ospf.size == 0 and sdn.size == 0:
        print("  skip outage_ecdf: no handover outage rows")
        return None

    fig, ax = plt.subplots(figsize=(6.0, 4.2))
    for arr, mode, label in (
        (ospf, "ospf", "OSPF (topology change)"),
        (sdn, "sdn", "SDN (proactive handover)"),
    ):
        if arr.size == 0:
            continue
        y = np.arange(1, arr.size + 1) / arr.size
        # Step from 0 so a point mass at 0 s shows as a vertical rise at x=0.
        xs = np.concatenate(([0.0], arr))
        ys = np.concatenate(([0.0], y))
        ax.step(xs, ys, where="post", linewidth=2.2,
                color=MODE_COLORS[mode], label=f"{label} (n={arr.size})")

    ax.set_xlabel("Data-plane outage at handover (s)")
    ax.set_ylabel("Fraction of handover repetitions")
    ax.set_title("Handover outage ECDF")
    ax.set_ylim(0, 1.02)
    ax.set_xlim(left=-0.2)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right")
    fig.tight_layout()
    return _save_figure(fig, out_dir, "outage_ecdf", dpi)


def read_event_class(path: str) -> Dict[Tuple[int, int], str]:
    """Load event_class.csv into {(nodes, time_index): event_type}."""
    mapping: Dict[Tuple[int, int], str] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            n = _int(row.get("nodes"))
            t = _int(row.get("time_index"))
            et = row.get("event_type")
            if n is not None and t is not None and et:
                mapping[(n, t)] = et
    return mapping


def _worst_outage_by_class(
    raw_rows: List[dict],
    class_map: Dict[Tuple[int, int], str],
) -> Dict[Tuple[int, str, str], List[float]]:
    """Per-rep worst handover outage (seconds), keyed by (nodes, mode, event_type).

    Each repetition contributes the maximum outage it saw among events of a given
    class, so the plotted distribution reflects the decisive event of that class
    rather than an incidental precursor.
    """
    per_rep: Dict[Tuple[int, str, str, str], float] = {}
    for row in raw_rows:
        if row.get("reason") not in HANDOVER_REASONS:
            continue
        n = _int(row.get("nodes"))
        t = _int(row.get("time_index"))
        rep = row.get("rep")
        mode = row.get("mode")
        val = _float(row.get("outage_ms"))
        if n is None or t is None or rep is None or mode is None or val is None:
            continue
        event_type = class_map.get((n, t))
        if event_type is None:
            continue
        key = (n, mode, event_type, rep)
        secs = val / 1000.0
        if key not in per_rep or secs > per_rep[key]:
            per_rep[key] = secs

    out: Dict[Tuple[int, str, str], List[float]] = defaultdict(list)
    for (n, mode, event_type, _rep), secs in per_rep.items():
        out[(n, mode, event_type)].append(secs)
    return out


def _plot_class_panel(
    ax,
    agg: Dict[Tuple[int, str, str], List[float]],
    nodes: List[int],
    event_type: str,
    *,
    title: str,
) -> bool:
    """One panel: OSPF vs SDN worst-outage distribution for one event class."""
    any_data = False
    offset = 0.2
    for i, n in enumerate(nodes):
        ospf = agg.get((n, "ospf", event_type), [])
        sdn = agg.get((n, "sdn", event_type), [])
        if ospf:
            any_data = True
            _draw_violin_box_strip(
                ax, i - offset, ospf, MODE_COLORS["ospf"], width=0.34, seed=n)
        if sdn:
            any_data = True
            _draw_violin_box_strip(
                ax, i + offset, sdn, MODE_COLORS["sdn"], width=0.34, seed=n + 1)
    ax.set_title(title)
    ax.set_xlabel("Constellation size (nodes)")
    ax.set_xticks(range(len(nodes)))
    ax.set_xticklabels([str(n) for n in nodes])
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_ylim(bottom=-max(1.0, ax.get_ylim()[1] * 0.02))
    return any_data


def plot_handover_classified(
    raw_rows: List[dict],
    class_map: Dict[Tuple[int, int], str],
    out_dir: str,
    dpi: int,
) -> Optional[List[str]]:
    """Central result: handover outage split by physical event class.

    Left panel: coverage-preserving GSL handovers, where a replacement satellite
    is in view and OrbitGraph installs the new path before the old link is torn
    down. Right panel: coverage-gap events, where a ground station has no
    satellite in view and the ground-to-ground path is physically severed, so
    neither OSPF nor OrbitGraph can route around it.
    """
    plt = _import_matplotlib()

    agg = _worst_outage_by_class(raw_rows, class_map)
    if not agg:
        print("  skip handover_classified: no classified handover rows")
        return None

    ho_nodes = sorted({n for (n, _m, et) in agg if et == "handover"})
    gap_nodes = sorted({n for (n, _m, et) in agg if et == "coverage_gap"})

    def _render_class(nodes, event_type, title, stem, fig_w, legend_loc):
        """One standalone single-column figure for a single event class."""
        fig, ax = plt.subplots(figsize=(fig_w, 4.2))
        if not _plot_class_panel(ax, agg, nodes, event_type, title=title):
            plt.close(fig)
            print(f"  skip {stem}: no plottable values")
            return []
        ax.set_ylabel("Worst outage per run (s)")
        handles = [
            plt.Line2D([0], [0], marker="o", linestyle="none",
                       color=MODE_COLORS["ospf"], label="OSPF"),
            plt.Line2D([0], [0], marker="o", linestyle="none",
                       color=MODE_COLORS["sdn"], label="OrbitGraph (SDN)"),
        ]
        ax.legend(handles=handles, loc=legend_loc)
        fig.tight_layout()
        return _save_figure(fig, out_dir, stem, dpi)

    written: List[str] = []
    written.extend(_render_class(
        ho_nodes, "handover", "Coverage-preserving handovers",
        "handover_coverage_preserving", 4.2 + 1.1 * len(ho_nodes),
        "upper left",
    ))
    if gap_nodes:
        # Data clusters at the top of a 0-baseline axis, so the legend sits in
        # the empty lower area rather than over the title/violins.
        written.extend(_render_class(
            gap_nodes, "coverage_gap",
            "Coverage-gap events\n(no satellite in view)",
            "handover_coverage_gap", 4.0, "center",
        ))

    if not written:
        print("  skip handover_classified: no plottable values")
        return None
    return written


DAMAGE_RECOVERY = "damage_recovery"
DAMAGE_TIME = 5
RECOVERY_TIME = 10


def _damage_outage_values(
    rows: List[dict],
    time_index: int,
    nodes: int,
    mode: str,
) -> List[float]:
    """Per-rep outage (seconds) at one damage/recovery tick for a node/mode cell."""
    out: List[float] = []
    for row in rows:
        if row.get("reason") != DAMAGE_RECOVERY:
            continue
        if _int(row.get("time_index")) != time_index:
            continue
        if _truthy(row.get("still_down")):
            continue
        if _int(row.get("nodes")) != nodes or row.get("mode") != mode:
            continue
        val = _float(row.get("outage_ms"))
        if val is not None:
            out.append(val / 1000.0)
    return out


def _plot_damage_panel(
    ax,
    raw_rows: List[dict],
    time_index: int,
    nodes: List[int],
    *,
    title: str,
) -> bool:
    """One panel: OSPF vs SDN outage distribution at a fixed damage/recovery tick."""
    any_data = False
    offset = 0.2
    for i, n in enumerate(nodes):
        ospf = _damage_outage_values(raw_rows, time_index, n, "ospf")
        sdn = _damage_outage_values(raw_rows, time_index, n, "sdn")
        if ospf:
            any_data = True
            _draw_violin_box_strip(
                ax, i - offset, ospf, MODE_COLORS["ospf"], width=0.34, seed=n)
        if sdn:
            any_data = True
            _draw_violin_box_strip(
                ax, i + offset, sdn, MODE_COLORS["sdn"], width=0.34, seed=n + 1)
    ax.set_title(title)
    ax.set_xlabel("Constellation size (nodes)")
    ax.set_xticks(range(len(nodes)))
    ax.set_xticklabels([str(n) for n in nodes])
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_ylim(bottom=-1)
    return any_data


def plot_outage_damage_recovery(
    raw_rows: List[dict],
    out_dir: str,
    dpi: int,
) -> Optional[List[str]]:
    """
    Link damage (t=5) vs recovery (t=10) data-plane outage distribution.

    Shows the full per-rep spread (violin + box + strip). SDN pushes a full
    FIB reinstall after damage; OSPF converges locally — not comparable to
    proactive handover (see outage_vs_nodes). This is the honest negative
    result: SDN outage can exceed OSPF here.
    """
    plt = _import_matplotlib()
    nodes = sorted({
        _int(r["nodes"]) for r in raw_rows
        if r.get("reason") == DAMAGE_RECOVERY and _int(r.get("nodes")) is not None
    })
    if not nodes:
        print("  skip outage_damage_recovery: no raw damage_recovery rows")
        return None

    # Independent y-axes: recovery outages are much smaller than damage outages,
    # and a shared scale would flatten the recovery panel into a single line at
    # zero, hiding the OSPF-vs-SDN difference there.
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8), sharey=False)
    left_ok = _plot_damage_panel(
        axes[0], raw_rows, DAMAGE_TIME, nodes,
        title=f"Link damage (t={DAMAGE_TIME})",
    )
    right_ok = _plot_damage_panel(
        axes[1], raw_rows, RECOVERY_TIME, nodes,
        title=f"Link recovery (t={RECOVERY_TIME})",
    )

    if not left_ok and not right_ok:
        plt.close(fig)
        print("  skip outage_damage_recovery: no plottable values")
        return None

    axes[0].set_ylabel("Data-plane outage (s)")
    axes[1].set_ylabel("Data-plane outage (s)")
    handles = [
        plt.Line2D([0], [0], marker="o", linestyle="none",
                   color=MODE_COLORS["ospf"], label="OSPF"),
        plt.Line2D([0], [0], marker="o", linestyle="none",
                   color=MODE_COLORS["sdn"], label="SDN"),
    ]
    axes[1].legend(handles=handles, loc="best")
    fig.suptitle(
        "Link failure: SDN reinstall vs OSPF convergence",
        y=1.02,
    )
    fig.tight_layout()
    return _save_figure(fig, out_dir, "outage_damage_recovery", dpi)


def _phase_values(
    raw_rows: List[dict],
    value_key: str,
    nodes: int,
    mode: str,
    phase: str,
) -> List[float]:
    """Per-rep values (loss_pct or avg_rtt_ms) for one node/mode/phase cell."""
    out: List[float] = []
    for row in raw_rows:
        if (_int(row.get("nodes")) != nodes or row.get("mode") != mode
                or row.get("phase") != phase):
            continue
        val = _float(row.get(value_key))
        if val is not None:
            out.append(val)
    return out


def _plot_phase_violin(
    raw_rows: List[dict],
    out_dir: str,
    dpi: int,
    *,
    value_key: str,
    ylabel: str,
    title: str,
    stem: str,
    ylim: Optional[Tuple[float, float]] = None,
) -> Optional[List[str]]:
    """Per-phase distribution (violin+box+strip) of a ping metric vs size.

    One panel per handover-relative phase; within each panel, OSPF and SDN
    violins sit side by side at every constellation size. Distribution view
    reveals bimodal OSPF loss (0% or 100%) that a mean would blur.
    """
    plt = _import_matplotlib()

    nodes = sorted({
        _int(r["nodes"]) for r in raw_rows if _int(r.get("nodes")) is not None
    })
    if not nodes:
        print(f"  skip {stem}: no node data")
        return None
    phases = [p for p in PHASE_ORDER
              if any(r.get("phase") == p for r in raw_rows)]
    if not phases:
        print(f"  skip {stem}: no phase rows")
        return None

    fig, axes = plt.subplots(
        1, len(phases), figsize=(3.0 * len(phases), 4.0),
        sharey=True, squeeze=False,
    )
    offset = 0.2
    any_data = False
    for ax, phase in zip(axes[0], phases):
        for i, n in enumerate(nodes):
            ospf = _phase_values(raw_rows, value_key, n, "ospf", phase)
            sdn = _phase_values(raw_rows, value_key, n, "sdn", phase)
            if ospf:
                any_data = True
                _draw_violin_box_strip(
                    ax, i - offset, ospf, MODE_COLORS["ospf"],
                    width=0.34, seed=n,
                )
            if sdn:
                any_data = True
                _draw_violin_box_strip(
                    ax, i + offset, sdn, MODE_COLORS["sdn"],
                    width=0.34, seed=n + 1,
                )
        ax.set_title(PHASE_TITLES.get(phase, phase.replace("_", " ").title()))
        ax.set_xticks(range(len(nodes)))
        ax.set_xticklabels([str(n) for n in nodes])
        ax.grid(True, axis="y", alpha=0.3)

    if not any_data:
        plt.close(fig)
        print(f"  skip {stem}: no plottable values")
        return None

    axes[0, 0].set_ylabel(ylabel)
    if ylim is not None:
        axes[0, 0].set_ylim(*ylim)
    handles = [
        plt.Line2D([0], [0], marker="o", linestyle="none",
                   color=MODE_COLORS["ospf"], label="OSPF"),
        plt.Line2D([0], [0], marker="o", linestyle="none",
                   color=MODE_COLORS["sdn"], label="SDN"),
    ]
    axes[0, -1].legend(handles=handles, loc="best")
    fig.suptitle(title, y=1.02)
    fig.supxlabel("Constellation size (nodes)")
    fig.tight_layout()
    return _save_figure(fig, out_dir, stem, dpi)


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
    mode_style = {
        "ospf": {"marker": "o", "zorder": 3, "linewidth": 2.0, "markersize": 6},
        "sdn": {"marker": "s", "zorder": 2, "linewidth": 1.5, "markersize": 5},
    }
    any_data = False
    for ax, phase in zip(axes[0], phases):
        # SDN first, OSPF on top so overlapping lines remain visible.
        for mode in ("sdn", "ospf"):
            xs, ys, yerr = [], [], []
            for n in nodes:
                match = _filter(rows, nodes=str(n), mode=mode, phase=phase)
                if not match:
                    continue
                row = match[0]
                if not _ping_phase_plottable(row, mean_key):
                    continue
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
            style = mode_style[mode]
            ax.errorbar(
                xs, ys, yerr=yerr, marker=style["marker"], capsize=3,
                linewidth=style["linewidth"], markersize=style["markersize"],
                zorder=style["zorder"],
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
    raw_rows: Optional[List[dict]] = None,
) -> Optional[List[str]]:
    """RTT by handover-relative phase vs constellation size."""
    if raw_rows:
        return _plot_phase_violin(
            raw_rows, out_dir, dpi,
            value_key="avg_rtt_ms",
            ylabel="RTT (ms)",
            title="Ping RTT by phase",
            stem="rtt_by_phase",
        )
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
    raw_rows: Optional[List[dict]] = None,
) -> Optional[List[str]]:
    """Packet loss by handover-relative phase vs constellation size."""
    if raw_rows:
        return _plot_phase_violin(
            raw_rows, out_dir, dpi,
            value_key="loss_pct",
            ylabel="Packet loss (%)",
            title="Ping loss by phase",
            stem="loss_by_phase",
            ylim=(-5, 105),
        )
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
    control_raw_rows: Optional[List[dict]] = None,
) -> Optional[List[str]]:
    """
    Handover control-plane cost at first handover per rep (log scale).

    OSPF ``time_ms`` is route-dump collection time (~10² ms). SDN splits into
    Dijkstra compute (~10⁰ ms) and docker-exec install (~10³–10⁴ ms).
    """
    plt = _import_matplotlib()
    import numpy as np

    nodes = _sorted_nodes(rows if control_raw_rows is None else control_raw_rows)
    if not nodes:
        print("  skip control_handover: no node data")
        return None

    ospf_y, ospf_err = [], []
    compute_y, install_y = [], []
    plot_nodes: List[int] = []

    if control_raw_rows:
        ospf_agg = _aggregate_numeric_by_nodes_mode(
            _first_event_rows_per_rep(control_raw_rows, OSPF_HANDOVER_OUTAGE),
            "time_ms",
            skip_still_down=False,
        )
        sdn_first = _first_event_rows_per_rep(
            control_raw_rows, SDN_HANDOVER_INSTALL)
        compute_agg = _aggregate_numeric_by_nodes_mode(
            sdn_first, "compute_ms", skip_still_down=False,
        )
        install_agg = _aggregate_numeric_by_nodes_mode(
            sdn_first, "install_ms", skip_still_down=False,
        )
        for n in nodes:
            ns = str(n)
            ospf_key = (ns, "ospf")
            sdn_key = (ns, "sdn")
            if ospf_key not in ospf_agg and sdn_key not in install_agg:
                continue
            plot_nodes.append(n)
            if ospf_key in ospf_agg:
                m, s, _ = ospf_agg[ospf_key]
                ospf_y.append(m)
                ospf_err.append(s)
            else:
                ospf_y.append(np.nan)
                ospf_err.append(0.0)
            if sdn_key in compute_agg:
                compute_y.append(compute_agg[sdn_key][0])
                install_y.append(install_agg.get(sdn_key, (np.nan, 0, 0))[0])
            else:
                compute_y.append(np.nan)
                install_y.append(np.nan)
    else:
        sdn_reasons = {r.get("reason") for r in rows if r.get("mode") == "sdn"}
        sdn_reason = (
            SDN_HANDOVER_INSTALL if SDN_HANDOVER_INSTALL in sdn_reasons
            else SDN_HANDOVER_OUTAGE_FALLBACK
        )
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
    fig, ax = plt.subplots(figsize=(6.4, 4.3))

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
    ax.set_ylabel("Control-plane time (ms, log scale)")
    ax.set_xlabel("Constellation size (nodes)")
    ax.set_title("Control-plane cost at handover")
    ax.set_xticks(x)
    ax.set_xticklabels([str(n) for n in plot_nodes])
    ax.legend(loc="upper left", framealpha=1.0)
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
                    ha="center", va="bottom", fontsize=10,
                )

    fig.tight_layout()
    return _save_figure(fig, out_dir, "control_handover", dpi)


def plot_routes_installed_handover(
    rows: List[dict],
    out_dir: str,
    dpi: int,
    control_raw_rows: Optional[List[dict]] = None,
) -> Optional[List[str]]:
    """Routes pushed at first handover per rep (SDN incremental install)."""
    plt = _import_matplotlib()
    nodes = _sorted_nodes(rows if control_raw_rows is None else control_raw_rows)
    if not nodes:
        print("  skip routes_installed_handover: no node data")
        return None

    import numpy as np

    fig, ax = plt.subplots(figsize=(6.0, 4.2))
    plot_nodes: List[int] = []
    means: List[float] = []
    stds: List[float] = []
    per_node_values: Dict[int, List[float]] = {}

    if control_raw_rows:
        first = _first_event_rows_per_rep(
            control_raw_rows, SDN_HANDOVER_INSTALL)
        for row in first:
            if row.get("mode") != "sdn":
                continue
            n = _int(row.get("nodes"))
            val = _float(row.get("installed"))
            if n is None or val is None:
                continue
            per_node_values.setdefault(n, []).append(val)
        for n in nodes:
            vals = per_node_values.get(n)
            if not vals:
                continue
            plot_nodes.append(n)
            means.append(statistics.fmean(vals))
            stds.append(_spread(vals, use_median=False))
    else:
        sdn_rows = [
            r for r in rows
            if r.get("mode") == "sdn" and r.get("reason") == SDN_HANDOVER_INSTALL
        ]
        if not sdn_rows:
            sdn_rows = _filter(rows, mode="sdn", reason=SDN_HANDOVER_OUTAGE_FALLBACK)
        for n in nodes:
            match = [r for r in sdn_rows if _int(r.get("nodes")) == n]
            if not match:
                continue
            mean = _float(match[0].get("installed_mean"))
            if mean is None:
                continue
            plot_nodes.append(n)
            means.append(mean)
            stds.append(_float(match[0].get("installed_std")) or 0.0)

    if not plot_nodes:
        plt.close(fig)
        print("  skip routes_installed_handover: no installed values")
        return None

    x = np.arange(len(plot_nodes), dtype=float)
    ax.bar(
        x, means, width=0.6, yerr=stds, capsize=4,
        color=MODE_COLORS["sdn"], alpha=0.55, edgecolor=MODE_COLORS["sdn"],
        label="SDN routes installed (mean ± stddev)",
    )
    # Overlay each repetition as a jittered dot.
    rng = np.random.default_rng(0)
    for i, n in enumerate(plot_nodes):
        vals = per_node_values.get(n)
        if not vals:
            continue
        xs = i + rng.uniform(-0.12, 0.12, size=len(vals))
        ax.scatter(
            xs, vals, s=16, color="black", alpha=0.6, zorder=4,
            label="individual repetitions" if i == 0 else None,
        )

    ax.set_xlabel("Constellation size (nodes)")
    ax.set_ylabel("Kernel routes pushed")
    ax.set_title("Routes installed at handover")
    ax.set_xticks(x)
    ax.set_xticklabels([str(n) for n in plot_nodes])
    ax.set_ylim(bottom=0)
    ax.legend()
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
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
    raw_path = os.path.join(results_dir, OUTAGE_RAW)
    raw_rows: Optional[List[dict]] = None
    if os.path.isfile(raw_path):
        raw_rows = read_table(
            raw_path,
            ["nodes", "mode", "reason", "time_index", "outage_ms", "still_down"],
        )

    control_raw_path = os.path.join(results_dir, CONTROL_RAW)
    control_raw_rows: Optional[List[dict]] = None
    if os.path.isfile(control_raw_path):
        control_raw_rows = read_table(
            control_raw_path,
            ["nodes", "mode", "rep", "time_index", "reason",
             "time_ms", "compute_ms", "install_ms", "installed"],
        )

    ping_raw_path = os.path.join(results_dir, PING_RAW)
    ping_raw_rows: Optional[List[dict]] = None
    if os.path.isfile(ping_raw_path):
        ping_raw_rows = read_table(
            ping_raw_path,
            ["nodes", "mode", "phase", "loss_pct", "avg_rtt_ms"],
        )

    event_class_path = os.path.join(results_dir, EVENT_CLASS)
    class_map: Dict[Tuple[int, int], str] = {}
    if os.path.isfile(event_class_path):
        class_map = read_event_class(event_class_path)

    if raw_rows and class_map:
        # Preferred central result: outage split by physical event class.
        paths = plot_handover_classified(raw_rows, class_map, out_dir, dpi)
        if paths:
            written.extend(paths)
    elif "outage" in summaries:
        # Fallback for datasets without an event classification.
        rows = read_table(
            summaries["outage"],
            ["nodes", "mode", "reason", "outage_ms_mean", "outage_ms_std"],
        )
        paths = plot_outage_vs_nodes(rows, out_dir, dpi, raw_rows=raw_rows)
        if paths:
            written.extend(paths)

    if raw_rows:
        paths = plot_outage_ecdf(raw_rows, out_dir, dpi, class_map=class_map or None)
        if paths:
            written.extend(paths)
        paths = plot_outage_damage_recovery(raw_rows, out_dir, dpi)
        if paths:
            written.extend(paths)

    if "ping_phase" in summaries:
        rows = read_table(
            summaries["ping_phase"],
            ["nodes", "mode", "phase", "rtt_mean_ms", "rtt_std_ms",
             "loss_mean_pct", "loss_std_pct"],
        )
        paths = plot_rtt_by_phase(rows, out_dir, dpi, raw_rows=ping_raw_rows)
        if paths:
            written.extend(paths)
        paths = plot_loss_by_phase(rows, out_dir, dpi, raw_rows=ping_raw_rows)
        if paths:
            written.extend(paths)

    if "control_reason" in summaries:
        rows = read_table(
            summaries["control_reason"],
            ["nodes", "mode", "reason", "time_ms_mean", "time_ms_std",
             "compute_ms_mean", "install_ms_mean", "installed_mean",
             "installed_std"],
        )
        paths = plot_control_handover(
            rows, out_dir, dpi, control_raw_rows=control_raw_rows,
        )
        if paths:
            written.extend(paths)
        paths = plot_routes_installed_handover(
            rows, out_dir, dpi, control_raw_rows=control_raw_rows,
        )
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
