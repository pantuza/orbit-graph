#!/usr/bin/env python3
"""
Run one OSPF or SDN batch for sanity-checking before full statistical runs.

Usage:
  python experiments/compare_single_run.py --mode ospf
  python experiments/compare_single_run.py --mode sdn
  python experiments/compare_single_run.py --mode sdn --profile basic
  python experiments/compare_single_run.py --mode sdn --profile full

Artifacts are written under a mode/profile-specific directory, e.g.:
  ./starlink-5-5-550-53-grid-LeastDelay-ospf-full/

Profiles:
  basic — no damage, ping right after route install + early emulation ticks
  full  — damage/recovery + handover-relative pings (the GSL handover time is
          read from Topo_leo_change.txt, which is geometry-dependent: t=53 for
          5x5, t=23 for 6x6, ...). Pings fire at the handover instant, shortly
          after, and at a late steady-state tick so the post-handover transient
          is captured at every constellation size.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import sys

_DEF_ORBITS = 5
_DEF_SATS = 5

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from starrynet.sn_ospf_adapter import (
    attach_ospf_metrics,
    dump_ospf_routes,
    run_ospf_post_init_checks,
)
from starrynet.sn_sdn_adapter import (
    debug_path_now,
    dump_sdn_routes,
    is_sdn_mode,
    ping_nodes_now,
    run_sdn_initial_routes,
    warm_sdn_dest,
    warm_sdn_path_neighbors,
)
from starrynet.sn_outage_probe import collect_outage_probe, start_outage_probe
from starrynet.sn_synchronizer import StarryNet
from starrynet.sn_utils import sn_remote_cmd

# Canonical 5x5 (25 sats) layout: GS 26/27, GSL peers 7/13, mid-path sat 8.
# These detailed path checks only apply to the default grid; larger grids use
# the size-aware GS endpoints derived in _grid_endpoints().
_BASIC_ROUTE_DUMP_NODES = (7, 8, 13, 26, 27)
_SDN_PATH_HOPS = ((26, 7), (7, 8), (8, 13), (13, 27))
_CANONICAL_SATS = 25


def _grid_endpoints(n_sats: int, gs_count: int) -> tuple[int, int]:
    """Ground-station node ids for an n_sats grid (GS are appended after sats)."""
    gs1 = n_sats + 1
    gs2 = n_sats + gs_count
    return gs1, gs2


def _prepare_config(
    base_config: str,
    orbits: int | None,
    sats: int | None,
) -> tuple[str, int, int, int]:
    """
    Return (config_path, n_sats, gs_count, duration).

    When orbits/sats are given, write a derived config so the constellation
    grid can be scaled without editing the checked-in config files. The
    artifact directory name (cons-orbit-sat-...) encodes the size automatically.
    """
    with open(base_config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if orbits is not None:
        cfg["# of orbit"] = orbits
    if sats is not None:
        cfg["# of satellites"] = sats
    n_sats = cfg["# of orbit"] * cfg["# of satellites"]
    gs_count = cfg["GS number"]
    duration = int(cfg["Duration (s)"])

    if orbits is None and sats is None:
        return base_config, n_sats, gs_count, duration

    derived = os.path.join(
        _ROOT, f".config_scaled_{cfg['# of orbit']}x{cfg['# of satellites']}_"
        f"{cfg['Intra-AS routing'].lower()}.json")
    with open(derived, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=4)
    return derived, n_sats, gs_count, duration


def _first_topology_change_time(work_dir: str, duration: int) -> int | None:
    """
    First GSL handover time from Topo_leo_change.txt (None if none/unreadable).

    The file (written by the observer during create_links) lists topology change
    blocks as `time <t>:` followed by `add:`/`del:` node-pair lines. The trailing
    `time <duration>:` block only carries `end of the emulation!`. We return the
    first <t> in (1, duration) that has at least one add/del node pair.
    """
    path = os.path.join(work_dir, "Topo_leo_change.txt")
    if not os.path.isfile(path):
        return None
    current: int | None = None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line.startswith("time "):
                try:
                    current = int(line[len("time "):].rstrip(":"))
                except ValueError:
                    current = None
            elif current is not None and re.match(r"^\d+-\d+$", line):
                if 1 < current < duration:
                    return current
    return None


def _full_ping_schedule(work_dir: str, duration: int) -> tuple[int, int, int]:
    """
    (handover_t, post_handover_t, steady_t) for the full profile.

    handover_t is the first GSL handover tick (geometry-dependent). We probe at
    the handover instant and shortly after to capture the reconvergence
    transient, plus a fixed late steady-state tick (same across sizes) where both
    protocols have settled. Falls back to a fixed probe when no handover is found.
    """
    handover_t = _first_topology_change_time(work_dir, duration)
    if handover_t is None or handover_t < 2 or handover_t > duration - 6:
        handover_t = min(50, duration - 8)
    post_handover_t = min(handover_t + 2, duration - 2)
    steady_t = duration - 5
    if steady_t <= post_handover_t:
        steady_t = min(post_handover_t + 10, duration - 1)
    return handover_t, post_handover_t, steady_t


def _verify_gs_source_routes(sn) -> None:
    """Ensure GSL source IPs (e.g. 9.1.7.60) are routed on mid-path nodes."""
    controller = getattr(sn, "sdn_controller", None)
    if controller is None:
        return
    dp = controller.dataplane
    checks = ((8, "9.1.7.60"), (13, "9.1.7.60"), (27, "9.1.7.60"))
    print("SDN reverse-path /32 checks (GS26 GSL source 9.1.7.60):")
    for node_id, target in checks:
        cid = str(sn.container_id_list[node_id - 1])
        lines = sn_remote_cmd(
            sn.remote_ssh, f"docker exec {cid} ip route show {target}")
        summary = " ".join(l.strip() for l in lines if l.strip()) or "MISSING"
        print(f"  B{node_id}: {summary}")


def _verify_sdn_path_hops(sn) -> None:
    """Print neighbor IPs read from peer containers for the 26->27 path."""
    controller = getattr(sn, "sdn_controller", None)
    if controller is None:
        return
    dp = controller.dataplane
    print("SDN path hop gateways (local /24 -> neighbor on peer):")
    for src, nh in _SDN_PATH_HOPS:
        lip = dp._local_ip.get((src, nh)) or dp._read_iface_ipv4(
            src, f"B{src}-eth{nh}")
        gw_iface = dp._read_iface_ipv4(nh, f"B{nh}-eth{src}")
        gw_subnet = dp._neighbor_ip_on_subnet(nh, lip) if lip else None
        pgw = dp._peer_gateway.get((nh, src))
        print(
            f"  B{src} -> B{nh}: local={lip} "
            f"gw_subnet={gw_subnet} gw_iface={gw_iface} map={pgw}"
        )


def _summarize_ping(path: str) -> str:
    if not os.path.isfile(path):
        return "missing"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for line in text.splitlines():
        if "packet loss" in line:
            return line.strip()
    return "no statistics line"


def _sdn_is_routing_event(data: dict) -> bool:
    if data.get("reason") != "delay_update":
        return True
    if data.get("fib_unchanged"):
        return False
    return data.get("installed", 0) > 0 or data.get("failed", 0) > 0


def _print_metrics_summary(work_dir: str, mode: str, profile: str) -> None:
    subdir = "sdn_metrics" if mode == "sdn" else "ospf_metrics"
    metrics_dir = os.path.join(work_dir, subdir)
    files = sorted(glob.glob(os.path.join(metrics_dir, "snapshot_*.json")))
    if not files:
        print(f"No {subdir} snapshots in {work_dir}")
        return
    label = "SDN" if mode == "sdn" else "OSPF"
    loaded = []
    for path in files:
        with open(path, encoding="utf-8") as fh:
            loaded.append(json.load(fh))

    if profile == "basic":
        show = [d for d in loaded if d.get("reason") == "init"]
        if not show:
            show = loaded[:1]
        print(f"{label} snapshots ({len(files)} total):")
    elif mode == "sdn":
        show = [d for d in loaded if _sdn_is_routing_event(d)]
        steady = len(loaded) - len(show)
        print(
            f"{label} snapshots: {len(loaded)} total "
            f"({len(show)} routing events, {steady} steady delay ticks omitted)"
        )
    else:
        show = loaded
        print(f"{label} snapshots ({len(files)} total):")

    for data in show:
        if mode == "sdn":
            fib_note = ""
            if data.get("fib_unchanged"):
                fib_note = " fib_unchanged"
            elif data.get("reason") == "delay_update":
                fib_note = " fib_changed"
            print(
                f"  t={data.get('time_index')} {data.get('reason')}: "
                f"recompute_ms={data.get('recompute_ms')} "
                f"installed={data.get('installed')} failed={data.get('failed')}"
                f"{fib_note}"
            )
        else:
            print(
                f"  t={data.get('time_index')} {data.get('reason')}: "
                f"collection_ms={data.get('collection_ms')} "
                f"bird_route_ok={data.get('bird_route_ok')}/"
                f"{data.get('nodes_dumped')}"
            )
    dump_sub = "route_dumps"
    dumps = sorted(glob.glob(os.path.join(metrics_dir, dump_sub, "*.txt")))
    if dumps:
        print(f"  {len(dumps)} files in {subdir}/{dump_sub}/")


def _run(
    mode: str,
    profile: str,
    *,
    suffix: str | None = None,
    seed: int | None = None,
    orbits: int | None = None,
    sats: int | None = None,
) -> str:
    if seed is not None:
        # Damage targets use random.uniform in-process; seeding makes the
        # damaged-link set reproducible per repetition.
        random.seed(seed)
        print(f"Random seed: {seed}")

    if mode == "sdn" and profile == "basic":
        base_config = os.path.join(_ROOT, "config_sdn_basic.json")
    elif mode == "sdn":
        base_config = os.path.join(_ROOT, "config_sdn.json")
    elif profile == "basic":
        base_config = os.path.join(_ROOT, "config_ospf_basic.json")
    else:
        base_config = os.path.join(_ROOT, "config.json")

    config, n_sats, gs_count, duration = _prepare_config(base_config, orbits, sats)
    gs1, gs2 = _grid_endpoints(n_sats, gs_count)
    node_total = n_sats + gs_count
    is_canonical = n_sats == _CANONICAL_SATS

    artifact_suffix = suffix or f"{mode}-{profile}"

    GS_lat_long = [[50.110924, 8.682127], [46.635700, 14.311817]]
    AS = [[1, gs2]]

    print(f"=== Batch: {mode.upper()} profile={profile} ({config}) ===")
    print(f"Grid: {n_sats} sats + {gs_count} GS = {node_total} nodes; "
          f"endpoints GS {gs1} <-> GS {gs2}")
    print(f"Artifacts suffix: {artifact_suffix}")
    sn = StarryNet(
        config,
        GS_lat_long,
        hello_interval=1,
        AS=AS,
        artifact_suffix=artifact_suffix,
    )
    sn.create_nodes()
    sn.create_links()

    # Topo_leo_change.txt is written by create_links(); read the GSL handover
    # tick now so full-profile pings can be scheduled relative to it.
    work_dir = os.path.join(sn.configuration_file_path, sn.file_path)

    # Route dumps: canonical mid-path sats when 5x5, else just the GS endpoints.
    route_nodes = list(_BASIC_ROUTE_DUMP_NODES) if is_canonical else [gs1, gs2]

    if is_sdn_mode(sn.intra_routing):
        # Full profile: recompute FIB on delay ticks; push only when next-hops change.
        reinstall = profile == "full"
        # Incremental (make-before-break) install is the production-like default;
        # SDN_FULL_REINSTALL=1 forces the naive full-table reinstall on every
        # event (paper baseline to quantify the cost of non-incremental updates).
        incremental = os.environ.get("SDN_FULL_REINSTALL", "") not in ("1", "true", "yes")
        run_sdn_initial_routes(
            sn,
            route_dump_nodes=tuple(route_nodes),
            reinstall_on_delay_update=reinstall,
            incremental_install=incremental,
        )
        if profile == "basic" and is_canonical:
            _verify_sdn_path_hops(sn)
            _verify_gs_source_routes(sn)
            warm_sdn_path_neighbors(sn, _SDN_PATH_HOPS)
            for hop_src in (7, 8, 13):
                warm_sdn_dest(sn, hop_src, 27)
        ping_nodes_now(sn, gs1, gs2, "post_init")
        debug_path_now(sn, gs1, gs2, "post_init")
        dump_sdn_routes(sn, label="after_post_init_ping", node_ids=route_nodes)
    else:
        sn.run_routing_deamon()
        run_ospf_post_init_checks(sn, route_dump_nodes=tuple(route_nodes))
        ping_nodes_now(sn, gs1, gs2, "post_init")
        debug_path_now(sn, gs1, gs2, "post_init")

    handover_t = post_handover_t = steady_t = None
    if profile == "basic":
        sn.set_ping(gs1, gs2, 2)
        sn.set_ping(gs1, gs2, 5)
        sn.set_ping(gs1, gs2, 10)
    else:
        sn.set_damage(0.3, 5)
        sn.set_recovery(10)
        handover_t, post_handover_t, steady_t = _full_ping_schedule(
            work_dir, duration)
        print(
            f"Handover-relative pings: handover@t={handover_t}, "
            f"post_handover@t={post_handover_t}, steady@t={steady_t}"
        )
        for ping_t in (handover_t, post_handover_t, steady_t):
            sn.set_ping(gs1, gs2, ping_t)

    # Continuous outage probe (full profile only): measures real data-plane
    # recovery time around each event, independent of the synchronous loop.
    outage_probe = None
    if profile == "full":
        outage_probe = start_outage_probe(sn, gs1, gs2, hz=10)

    sn.start_emulation()

    if outage_probe is not None:
        collect_outage_probe(sn, outage_probe)

    if is_sdn_mode(sn.intra_routing):
        dump_sdn_routes(sn, label="pre_teardown", node_ids=route_nodes)
    else:
        dump_ospf_routes(sn, label="pre_teardown", node_ids=route_nodes)

    sn.stop_emulation()

    print(f"Artifacts: {work_dir}")

    if profile == "basic":
        ping_times = ("post_init", 2, 5, 10)
    else:
        ping_times = (handover_t, post_handover_t, steady_t)

    pair = f"{gs1}-{gs2}"
    for t in ping_times:
        ping_path = os.path.join(work_dir, f"ping-{pair}_{t}.txt")
        print(f"Ping {gs1}->{gs2} @ {t}: {_summarize_ping(ping_path)}")

    if profile == "basic":
        for tag in ("post_init",):
            trace_path = os.path.join(work_dir, f"trace-{pair}_{tag}.txt")
            route_path = os.path.join(work_dir, f"route-get-{pair}_{tag}.txt")
            if os.path.isfile(trace_path):
                print(f"Traceroute @ {tag} (last lines):")
                with open(trace_path, encoding="utf-8") as fh:
                    for line in fh.readlines()[-8:]:
                        print(f"  {line.rstrip()}")
            if os.path.isfile(route_path):
                with open(route_path, encoding="utf-8") as fh:
                    print(f"Route get @ {tag}: {fh.read().strip()}")

    _print_metrics_summary(work_dir, mode, profile)

    if profile == "full":
        try:
            from outage import summarize_run as _summarize_outage_run
            _summarize_outage_run(work_dir, mode, pair)
        except Exception as exc:  # instrumentation must not fail the run
            print(f"[OUTAGE] summary skipped: {exc}")

    # Machine-readable markers so batch runners can locate artifacts and
    # record the constellation size / endpoints for this run.
    print(f"BATCH_ARTIFACT_DIR={os.path.abspath(work_dir)}")
    print(f"BATCH_SATS={n_sats}")
    print(f"BATCH_NODES={node_total}")
    print(f"BATCH_PING_PAIR={pair}")
    print(f"BATCH_HANDOVER_TIME={handover_t if handover_t is not None else -1}")
    print(f"BATCH_STEADY_TIME={steady_t if steady_t is not None else -1}")
    return work_dir


def main():
    parser = argparse.ArgumentParser(description="Single paired routing experiment run")
    parser.add_argument(
        "--mode",
        choices=("ospf", "sdn"),
        required=True,
        help="ospf: BIRD/OSPF baseline; sdn: centralized static routes",
    )
    parser.add_argument(
        "--profile",
        choices=("basic", "full"),
        default="basic",
        help="basic: no damage, early pings; full: damage + late pings",
    )
    parser.add_argument(
        "--suffix",
        default=None,
        help="Override artifact dir suffix (default: {mode}-{profile}). "
        "Used by the batch runner to isolate repetitions.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed RNG so damaged-link selection is reproducible per run.",
    )
    parser.add_argument(
        "--orbits",
        type=int,
        default=None,
        help="Override '# of orbit' (constellation grid rows). Default: config.",
    )
    parser.add_argument(
        "--sats",
        type=int,
        default=None,
        help="Override '# of satellites' per orbit. Default: config.",
    )
    args = parser.parse_args()
    os.chdir(_ROOT)
    _run(
        args.mode,
        args.profile,
        suffix=args.suffix,
        seed=args.seed,
        orbits=args.orbits,
        sats=args.sats,
    )


if __name__ == "__main__":
    main()
