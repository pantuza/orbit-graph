"""
Thin bridge between StarryNet and the detached sdn_routing package.
"""

from __future__ import annotations

import os
from typing import List, Optional, Set, Tuple

from starrynet.sdn_routing import SdnConfig, SdnController
from starrynet.sn_utils import sn_get_container_info, sn_remote_cmd


def is_sdn_mode(intra_routing: str) -> bool:
    return intra_routing.strip().upper() == "SDN"


def build_sdn_controller(
    sn,
    *,
    route_dump_nodes: Optional[Tuple[int, ...]] = None,
    reinstall_on_delay_update: bool = True,
    incremental_install: bool = True,
    proactive_handover: bool = True,
) -> SdnController:
    """Create an SdnController from a StarryNet instance."""
    base = os.path.join(sn.configuration_file_path, sn.file_path)
    delay_dir = os.path.join(base, "delay")
    metrics_dir = os.path.join(base, "sdn_metrics")
    nodes = route_dump_nodes or (26, 27)
    config = SdnConfig(
        constellation_size=sn.constellation_size,
        node_count=sn.node_size,
        delay_dir=delay_dir,
        metrics_dir=metrics_dir,
        reinstall_on_delay_update=reinstall_on_delay_update,
        incremental_install=incremental_install,
        proactive_handover=proactive_handover,
        route_dump_nodes=nodes,
    )
    return SdnController(config, sn.remote_ssh, sn.container_id_list)


def run_sdn_initial_routes(
    sn,
    *,
    route_dump_nodes: Optional[Tuple[int, ...]] = None,
    reinstall_on_delay_update: bool = True,
    incremental_install: bool = True,
    proactive_handover: bool = True,
) -> None:
    """Install routes for second 1 (post link init). No BIRD/OSPF."""
    controller = build_sdn_controller(
        sn,
        route_dump_nodes=route_dump_nodes,
        reinstall_on_delay_update=reinstall_on_delay_update,
        incremental_install=incremental_install,
        proactive_handover=proactive_handover,
    )
    sn.sdn_controller = controller
    controller.install_snapshot(1, reason="init")
    controller.dump_routes("post_init", node_ids=list(route_dump_nodes or (26, 27)))


def _work_dir(sn) -> str:
    return os.path.join(sn.configuration_file_path, sn.file_path)


def _container_name(sn, node_id: int) -> str:
    return str(sn_get_container_info(sn.remote_ssh)[node_id - 1])


def resolve_dest_ip(sn, des: int) -> str:
    """IPv4 address used for reachability tests toward node des."""
    if des > sn.constellation_size:
        return f"9.{des}.{des}.10"
    controller = getattr(sn, "sdn_controller", None)
    if controller is not None:
        host = controller.dataplane._host_cache.get(des)
        if host:
            return host
    cid = _container_name(sn, des)
    lines = sn_remote_cmd(
        sn.remote_ssh,
        "docker exec -i " + cid +
        " ip -4 -o addr show scope global | awk '{print $4}' | head -1",
    )
    return lines[0].strip().split("/")[0]


def _write_lines(path: str, lines: List[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


def route_get_now(sn, src: int, des: int, tag: str) -> str:
    """Run `ip route get` on src toward des (kernel FIB lookup)."""
    dest_ip = resolve_dest_ip(sn, des)
    cid = _container_name(sn, src)
    lines = sn_remote_cmd(
        sn.remote_ssh,
        f"docker exec -i {cid} ip route get {dest_ip}",
    )
    path = os.path.join(_work_dir(sn), f"route-get-{src}-{des}_{tag}.txt")
    _write_lines(path, lines)
    print(f"[SDN] route get {src}->{des} ({dest_ip}) tag={tag}: {''.join(lines).strip()}")
    return path


def trace_nodes_now(sn, src: int, des: int, tag: str) -> str:
    """ICMP traceroute from src to des (immediate, outside emulation clock)."""
    dest_ip = resolve_dest_ip(sn, des)
    cid = _container_name(sn, src)
    lines = sn_remote_cmd(
        sn.remote_ssh,
        "docker exec -i " + cid +
        " traceroute -n -I -q 1 -w 1 -m 20 " + dest_ip,
    )
    path = os.path.join(_work_dir(sn), f"trace-{src}-{des}_{tag}.txt")
    _write_lines(path, lines)
    print(f"[SDN] traceroute {src}->{des} ({dest_ip}) tag={tag}: wrote {path}")
    return path


def debug_path_now(sn, src: int, des: int, tag: str) -> None:
    """Post-install path check: route lookup + traceroute."""
    route_get_now(sn, src, des, tag)
    trace_nodes_now(sn, src, des, tag)


def _ping_one(sn, node_id: int, target_ip: str, *, count: int = 1) -> str:
    """Single ping from node_id; return packet-loss summary line."""
    cid = _container_name(sn, node_id)
    lines = sn_remote_cmd(
        sn.remote_ssh,
        f"docker exec -i {cid} ping -c {count} -W 3 {target_ip}",
    )
    for line in lines:
        if "packet loss" in line:
            return line.strip()
    return "no statistics"


def warm_sdn_path_neighbors(
    sn,
    hops: Tuple[Tuple[int, int], ...],
) -> None:
    """
    Prime ARP on each hop along a multi-hop path.

    Neighbor pings (L2 to next-hop IP) populate ARP entries that kernel
    forwarding needs for /32 routes via the same gateway.
    """
    controller = getattr(sn, "sdn_controller", None)
    if controller is None:
        return
    dp = controller.dataplane
    for src, nh in hops:
        lip = dp._local_ip.get((src, nh)) or dp._read_iface_ipv4(
            src, f"B{src}-eth{nh}")
        gw = dp.peer_gateway_ip(src, nh)
        if gw is None and lip:
            gw = dp._neighbor_ip_on_subnet(nh, lip)
        if gw is None:
            gw = dp._read_iface_ipv4(nh, f"B{nh}-eth{src}")
        if gw is None:
            gw = dp._peer_gateway.get((nh, src))
        if gw is None:
            print(f"[SDN] Warm skip B{src}->B{nh}: no gateway")
            continue
        summary = _ping_one(sn, src, gw)
        print(f"[SDN] Warm B{src} -> {gw} (toward B{nh}): {summary}")


def warm_sdn_dest(sn, src: int, des: int) -> None:
    """Ping canonical destination from src (tests transit + GS delivery)."""
    dest_ip = resolve_dest_ip(sn, des)
    summary = _ping_one(sn, src, dest_ip)
    print(f"[SDN] Warm B{src} -> {dest_ip} (B{des} host): {summary}")


def ping_nodes_now(sn, src: int, des: int, tag: str) -> None:
    """Run ping immediately (outside the emulation clock) for baseline checks."""
    dest_ip = resolve_dest_ip(sn, des)
    cid = _container_name(sn, src)
    lines = sn_remote_cmd(
        sn.remote_ssh,
        f"docker exec -i {cid} ping {dest_ip} -c 4 -i 1",
    )
    ping_path = os.path.join(_work_dir(sn), f"ping-{src}-{des}_{tag}.txt")
    _write_lines(ping_path, lines)
    print(f"[SDN] Immediate ping {src}->{des} ({dest_ip}) tag={tag}: wrote {ping_path}")


def dump_sdn_routes(sn, label: str = "pre_teardown", node_ids: Optional[List[int]] = None) -> None:
    """Dump kernel route tables from selected nodes (verification)."""
    controller = getattr(sn, "sdn_controller", None)
    if controller is None:
        return
    controller.dump_routes(label, node_ids=node_ids)


def sync_damaged_nodes(sn) -> None:
    """Map StarryNet damage_list (0-indexed sat indices) to 1-indexed node ids."""
    if not getattr(sn, "sdn_controller", None):
        return
    damaged: Set[int] = {int(i) + 1 for i in sn.damage_list}
    sn.sdn_controller.set_damaged_nodes(damaged)


def sdn_after_delay_update(sn, timeptr: int) -> None:
    controller = getattr(sn, "sdn_controller", None)
    if controller is None:
        return
    if not controller.config.reinstall_on_delay_update:
        return
    sync_damaged_nodes(sn)
    controller.install_snapshot(timeptr, reason="delay_update")


def sdn_proactive_handover(sn, time_index: int) -> None:
    """
    Install post-handover routes after new GSL links exist, before old ones drop.

    Uses the delay matrix at time_index (post-handover topology) while the
    previous GSL is still up — make-before-break at the link level.
    """
    controller = getattr(sn, "sdn_controller", None)
    if controller is None:
        return
    if not controller.config.proactive_handover:
        return
    sync_damaged_nodes(sn)
    controller.install_snapshot(int(time_index), reason="proactive_handover")


def sdn_after_topology_change(sn, time_index: int) -> None:
    controller = getattr(sn, "sdn_controller", None)
    if controller is None:
        return
    sync_damaged_nodes(sn)
    if controller.config.proactive_handover:
        controller.finalize_topology_change(int(time_index))
    else:
        controller.install_snapshot(int(time_index), reason="topology_change")


def sdn_after_damage_or_recovery(sn, timeptr: int) -> None:
    controller = getattr(sn, "sdn_controller", None)
    if controller is None:
        return
    sync_damaged_nodes(sn)
    controller.install_snapshot(timeptr, reason="damage_recovery")


def damaged_satellites_from_list(damage_list: List) -> Set[int]:
    return {int(i) + 1 for i in damage_list}
