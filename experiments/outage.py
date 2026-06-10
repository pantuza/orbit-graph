#!/usr/bin/env python3
"""
Compute data-plane outage/recovery time from the continuous outage probe.

The probe (starrynet/sn_outage_probe.py) writes timestamped `ping -D -O` output;
each control-plane snapshot records `wall_start` (epoch) for its event. For each
routing event we measure how long the source could not reach the destination,
i.e. time from the event until the first successful reply. This is the *same*
yardstick for OSPF and SDN -- a fair, honest convergence/recovery metric that
isn't fooled by the synchronous emulation loop blocking during SDN installs.
"""

from __future__ import annotations

import glob
import json
import os
import re
from typing import Dict, List, Optional, Tuple

_TS_RE = re.compile(r"^\[(\d+\.\d+)\]\s*(.*)$")
_SEQ_RE = re.compile(r"icmp_seq=(\d+)")
_RTT_RE = re.compile(r"time=([\d.]+)\s*ms")

# Reasons that disrupt forwarding (init has no prior connectivity to lose).
# proactive_handover: SDN routes pushed before old GSL is torn down (Phase 3).
# topology_change: OSPF reconvergence after full mutation; SDN finalize (no-op).
OUTAGE_REASONS = ("damage_recovery", "topology_change", "proactive_handover")


def parse_probe(path: str) -> List[Tuple[float, bool]]:
    """Return per-packet (send_ts, reachable) samples in send order.

    A packet is reachable iff its icmp_seq eventually gets a `bytes from` reply.
    Crucially, `ping -O`'s "no answer yet for icmp_seq=N" is NOT loss: with a
    sub-RTT probe interval the kernel prints it for every in-flight packet that
    then replies. Only sequences that never reply (or return ICMP Unreachable)
    are losses. Reply send-time is approximated as reply_ts - rtt so the
    timeline reflects when packets were actually sent.
    """
    if not os.path.isfile(path):
        return []
    replies: dict[int, float] = {}   # seq -> reply epoch ts
    rtt_s: dict[int, float] = {}     # seq -> RTT seconds
    sent: dict[int, float] = {}      # seq -> earliest "in flight" epoch ts
    failed: dict[int, float] = {}    # seq -> ICMP-unreachable epoch ts

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = _TS_RE.match(line.strip())
            if not m:
                continue
            ts = float(m.group(1))
            rest = m.group(2)
            sm = _SEQ_RE.search(rest)
            if not sm:
                continue
            seq = int(sm.group(1))
            if "bytes from" in rest:
                replies.setdefault(seq, ts)
                rm = _RTT_RE.search(rest)
                if rm:
                    rtt_s[seq] = float(rm.group(1)) / 1000.0
            elif "nreachable" in rest:  # (U)nreachable / (u)nreachable
                failed.setdefault(seq, ts)
                sent.setdefault(seq, ts)
            elif "no answer yet" in rest:
                sent.setdefault(seq, ts)

    samples: List[Tuple[float, bool]] = []
    for seq in sorted(set(replies) | set(sent) | set(failed)):
        if seq in replies:
            send_ts = replies[seq] - rtt_s.get(seq, 0.0)
            samples.append((send_ts, True))
        else:
            send_ts = sent.get(seq, failed.get(seq))
            samples.append((float(send_ts), False))
    samples.sort(key=lambda s: s[0])
    return samples


def event_outage(
    samples: List[Tuple[float, bool]],
    wall_start: float,
    *,
    onset_lo: float = 1.0,
    onset_hi: float = 1.5,
    min_run: int = 3,
) -> dict:
    """Outage around an event applied at epoch `wall_start`.

    Finds the *sustained* loss onset near the event (within [-onset_lo,
    +onset_hi] s) and measures recovery as the first sustained run of replies
    after it. Outage is the black-hole duration (onset -> recovery); 0 means no
    disruption was detected for this pair. still_down means it never recovered
    before capture ended.

    min_run guards against jitter: at 10 Hz a single dropped/reordered packet
    (or one late-reply blip) is not an outage. We require `min_run` consecutive
    losses to start and `min_run` consecutive replies to end one (~300 ms),
    which is well below real reconvergence/install outages (>1 s) but rejects
    isolated drops that would otherwise short-circuit to a bogus 0 ms.

    onset_hi is deliberately tight: an event-caused disruption begins almost
    immediately (the topology mutation runs just before wall_start), so a loss
    burst starting seconds later belongs to a different event. Recovery itself
    is uncapped (an SDN install can keep the path down for several seconds).
    """
    if not samples:
        return {"outage_ms": None, "still_down": None, "detected": False}

    n = len(samples)

    def sustained_loss(i: int) -> bool:
        run = samples[i:i + min_run]
        return len(run) >= min_run and all(not ok for _, ok in run)

    loss_idx: Optional[int] = None
    for i, (ts, ok) in enumerate(samples):
        if ts < wall_start - onset_lo:
            continue
        if ts > wall_start + onset_hi:
            break
        if not ok and sustained_loss(i):
            loss_idx = i
            break

    if loss_idx is None:
        return {"outage_ms": 0.0, "still_down": False, "detected": True}

    loss_start = samples[loss_idx][0]
    for j in range(loss_idx, n):
        if samples[j][1] and all(ok for _, ok in samples[j:j + min_run]):
            return {
                "outage_ms": max(0.0, (samples[j][0] - loss_start) * 1000.0),
                "still_down": False,
                "detected": True,
            }

    return {
        "outage_ms": max(0.0, (samples[-1][0] - loss_start) * 1000.0),
        "still_down": True,
        "detected": True,
    }


def _load_event_snapshots(metrics_dir: str) -> List[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(metrics_dir, "snapshot_*.json"))):
        with open(path, encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def run_outages(
    artifact_dir: str,
    mode: str,
    pair: Optional[str],
) -> List[dict]:
    """Per-event outage rows for one run (mode in {ospf, sdn})."""
    sub = "sdn_metrics" if mode == "sdn" else "ospf_metrics"
    metrics_dir = os.path.join(artifact_dir, sub)
    probe_path = (
        os.path.join(artifact_dir, f"outage-{pair}.txt") if pair else None
    )
    # Fall back to any outage-*.txt if the pair wasn't supplied.
    if not probe_path or not os.path.isfile(probe_path):
        candidates = glob.glob(os.path.join(artifact_dir, "outage-*.txt"))
        probe_path = candidates[0] if candidates else None
    samples = parse_probe(probe_path) if probe_path else []

    rows: List[dict] = []
    for snap in _load_event_snapshots(metrics_dir):
        if snap.get("reason") not in OUTAGE_REASONS:
            continue
        wall_start = snap.get("wall_start")
        if wall_start is None:
            continue
        res = event_outage(samples, float(wall_start))
        rows.append({
            "reason": snap.get("reason"),
            "time_index": snap.get("time_index"),
            "event": f"{snap.get('reason')}@t{snap.get('time_index')}",
            "outage_ms": res["outage_ms"],
            "still_down": res["still_down"],
            "detected": res["detected"],
        })
    return rows


def summarize_run(artifact_dir: str, mode: str, pair: Optional[str]) -> None:
    """Print per-event outage for a single artifact dir."""
    rows = run_outages(artifact_dir, mode, pair)
    if not rows:
        print("  outage: (no probe / no events)")
        return
    print("  data-plane outage (event -> recovery):")
    for r in rows:
        ms = r["outage_ms"]
        ms_str = "NA" if ms is None else f"{ms:.0f} ms"
        flag = " (still down at end)" if r["still_down"] else ""
        print(f"    {r['event']}: {ms_str}{flag}")
