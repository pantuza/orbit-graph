"""Install forwarding state into StarryNet containers (kernel static routes)."""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from starrynet.sn_utils import sn_remote_cmd
from starrynet.sdn_routing.routing import Fib

# (dest /32 cidr, via gateway or None for on-link, output interface)
_ROUTE_KEY = Tuple[str, Optional[str], str]

# Per-host /32 routes are appropriate for small emulations (tens of nodes) where
# each container has a stable canonical address. At mega-constellation scale,
# production controllers aggregate (e.g. per-link /24 or SRv6/label stacks), not
# one FIB entry per remote satellite host.
# StarryNet may use B{n}-eth{m} or generic eth* depending on rename timing.
_IFACE_LINE_RE = re.compile(
    r"^\d+:\s+(\S+?)(?:@.*)?\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)",
)
_IFACE_B_RE = re.compile(r"^B(\d+)-eth(\d+)$")


def canonical_host_ip(
    node_id: int,
    ifaces: List[Tuple[int, int, str]],
    constellation_size: int,
) -> Optional[str]:
    """
    Pick one stable IPv4 identity per node for /32 routing targets.

    Ground stations: 9.N.N.10 (matches sn_ping for GS destinations).
    Satellites: GSL .50 if present, else ISL address on the lowest-numbered peer link
    (deterministic, owned by this node only).
    """
    if node_id > constellation_size:
        return f"9.{node_id}.{node_id}.10"

    owned = [(peer, ip) for owner, peer, ip in ifaces if owner == node_id]

    gsl = sorted(
        [(peer, ip) for peer, ip in owned if peer > constellation_size],
        key=lambda item: item[0],
    )
    for _peer, ip in gsl:
        if ip.endswith(".50"):
            return ip
    if gsl:
        return gsl[0][1]

    isl = sorted(
        [(peer, ip) for peer, ip in owned if peer <= constellation_size],
        key=lambda item: item[0],
    )
    if isl:
        return isl[0][1]

    return None


def node_routable_ips(
    node_id: int,
    ifaces: List[Tuple[int, int, str]],
    constellation_size: int,
) -> List[str]:
    """
    IPv4 identities that need explicit /32 routes system-wide.

    - Every 9.* address (GSL + GS home): needed for ping sources like 9.1.7.60.
    - Canonical ISL 10.0.* per satellite: needed to reach nodes with no GSL.
    Do not install /32 for all ISL addresses (table explosion + bad gateways).
    """
    seen: Set[str] = set()
    primary = canonical_host_ip(node_id, ifaces, constellation_size)
    if primary:
        seen.add(primary)
    for owner, _peer, ip in ifaces:
        if owner != node_id:
            continue
        if _is_management_ip(ip):
            continue
        if ip.startswith("9."):
            seen.add(ip)
    if node_id > constellation_size:
        seen.add(f"9.{node_id}.{node_id}.10")
    if not seen:
        return []
    ordered: List[str] = []
    if primary and primary in seen:
        ordered.append(primary)
    for ip in sorted(seen):
        if ip not in ordered:
            ordered.append(ip)
    return ordered


def _host_from_route(dest_cidr: str) -> str:
    return dest_cidr.replace("/32", "")


def _subnet_key(ip: str, prefix_len: int) -> str:
    """Group addresses on the same L2 segment (/24 in StarryNet)."""
    octets = ip.split(".")
    if prefix_len >= 24:
        return ".".join(octets[:3])
    if prefix_len >= 16:
        return ".".join(octets[:2])
    return octets[0]


def _is_management_ip(ip: str) -> bool:
    return ip.startswith("192.168.")


def peer_gateway_from_maps(
    local_ip: Dict[Tuple[int, int], str],
    peer_gateway: Dict[Tuple[int, int], str],
    src: int,
    next_hop: int,
) -> Optional[str]:
    """Resolve next-hop gateway from in-memory maps only (no docker exec)."""
    local = local_ip.get((src, next_hop))
    gw = peer_gateway.get((next_hop, src))
    if gw is not None and gw != local:
        return gw
    remote = local_ip.get((next_hop, src))
    if (
        local
        and remote
        and remote != local
        and _subnet_key(local, 24) == _subnet_key(remote, 24)
    ):
        return remote
    return None


def _iface_peer(node_id: int, iface: str) -> Optional[int]:
    """Parse B{owner}-eth{peer} and return peer id when owner matches node_id."""
    match = _IFACE_B_RE.match(iface.split("@")[0])
    if not match:
        return None
    owner, peer = int(match.group(1)), int(match.group(2))
    if owner != node_id:
        return None
    return peer


def build_peer_maps(
    node_addrs: Dict[int, List[Tuple[str, str]]],
    constellation_size: int,
    *,
    iface_names_only: bool = False,
) -> Tuple[
    Dict[Tuple[int, int], str],
    Dict[Tuple[int, int], str],
    Dict[int, List[Tuple[int, int, str]]],
    Dict[Tuple[int, int], str],
]:
    """
    Build peer gateway and egress interface maps.

    When StarryNet has renamed links to B{n}-eth{m}, use those names directly
    (reliable). Subnet pairing is only used as a fallback for eth* labs where
    multiple satellites can appear in the same /24 and corrupt next-hop gateways.
    """
    peer_gateway: Dict[Tuple[int, int], str] = {}
    peer_dev: Dict[Tuple[int, int], str] = {}
    node_ifaces: Dict[int, List[Tuple[int, int, str]]] = {
        nid: [] for nid in node_addrs
    }
    local_ip: Dict[Tuple[int, int], str] = {}

    for node_id, entries in node_addrs.items():
        for ip, iface in entries:
            if _is_management_ip(ip):
                continue
            peer = _iface_peer(node_id, iface)
            if peer is None:
                continue
            clean_iface = iface.split("@")[0]
            local_ip[(node_id, peer)] = ip
            peer_dev[(node_id, peer)] = clean_iface
            node_ifaces[node_id].append((node_id, peer, ip))

    for (src, peer), lip in local_ip.items():
        remote = local_ip.get((peer, src))
        if not remote:
            continue
        # Ignore B{n}-eth{m} rows where the name peer does not match the /24 partner.
        if _subnet_key(lip, 24) != _subnet_key(remote, 24):
            continue
        peer_gateway[(peer, src)] = remote

    subnets: Dict[str, List[Tuple[int, str, str]]] = defaultdict(list)
    for node_id, entries in node_addrs.items():
        for ip, iface in entries:
            if _is_management_ip(ip):
                continue
            subnets[_subnet_key(ip, 24)].append((node_id, ip, iface))

    for members in subnets.values():
        by_node: Dict[int, Tuple[str, str]] = {}
        for node_id, ip, iface in members:
            by_node[node_id] = (ip, iface)

        nodes = sorted(by_node)
        if len(nodes) != 2:
            continue

        for i, src in enumerate(nodes):
            for dst in nodes[i + 1:]:
                has_direct = (src, dst) in local_ip or (dst, src) in local_ip
                is_gsl = (
                    src > constellation_size or dst > constellation_size
                )
                # Sat-to-sat: require a direct B{src}-eth{dst} mapping so we do
                # not pair nodes that only share a /24 toward a third peer.
                if not has_direct and not is_gsl:
                    continue
                if (src, dst) in local_ip and (dst, src) in local_ip:
                    src_ip = local_ip[(src, dst)]
                    dst_ip = local_ip[(dst, src)]
                    src_iface = peer_dev.get((src, dst), by_node[src][1])
                    dst_iface = peer_dev.get((dst, src), by_node[dst][1])
                elif (src, dst) in local_ip:
                    src_ip, src_iface = by_node[src]
                    dst_ip, dst_iface = by_node[dst]
                else:
                    src_ip, src_iface = by_node[src]
                    dst_ip, dst_iface = by_node[dst]
                if _subnet_key(dst_ip, 24) != _subnet_key(src_ip, 24):
                    continue
                peer_gateway.setdefault((dst, src), dst_ip)
                peer_gateway.setdefault((src, dst), src_ip)
                local_ip.setdefault((src, dst), src_ip)
                local_ip.setdefault((dst, src), dst_ip)
                peer_dev.setdefault((src, dst), src_iface)
                peer_dev.setdefault((dst, src), dst_iface)
                if not any(p == dst for _o, p, _i in node_ifaces[src]):
                    node_ifaces[src].append((src, dst, src_ip))
                if not any(p == src for _o, p, _i in node_ifaces[dst]):
                    node_ifaces[dst].append((dst, src, dst_ip))

    for node_id, ifaces in node_ifaces.items():
        if not ifaces and node_id in node_addrs:
            for ip, _iface in node_addrs[node_id]:
                if ip == f"9.{node_id}.{node_id}.10":
                    node_ifaces[node_id].append((node_id, node_id, ip))

    return peer_gateway, peer_dev, node_ifaces, local_ip


class DockerDataplane:
    """Push FIB entries into containers via batched docker exec + ip route."""

    def __init__(
        self,
        remote_ssh,
        container_id_list: List[str],
        constellation_size: int,
        parallel_workers: int = 8,
    ):
        self.remote_ssh = remote_ssh
        self.container_id_list = container_id_list
        self.constellation_size = constellation_size
        self.parallel_workers = max(1, parallel_workers)
        self._host_cache: Dict[int, str] = {}
        self._node_ips: Dict[int, List[str]] = {}
        self._peer_gateway: Dict[Tuple[int, int], str] = {}
        self._local_ip: Dict[Tuple[int, int], str] = {}
        self._peer_dev: Dict[Tuple[int, int], str] = {}
        self._installed: Dict[int, Set[_ROUTE_KEY]] = {}
        self._forwarding_enabled = False
        self._addresses_refreshed = False
        self._use_canonical_dev = True
        # Link init renames interfaces via async docker exec -d; wait long
        # enough that all containers expose B{n}-eth{m} before installing routes.
        n = len(self.container_id_list)
        self._iface_wait_timeout = max(90.0, min(300.0, 30.0 + n * 0.8))
        self._iface_no_rename_abort = self._iface_wait_timeout

    def _container(self, node_id: int) -> str:
        idx = node_id - 1
        if idx < 0 or idx >= len(self.container_id_list):
            raise IndexError(
                f"Node {node_id} out of range: have "
                f"{len(self.container_id_list)} containers "
                f"(expected >= {node_id}). Docker may have failed to start "
                f"the full constellation."
            )
        return str(self.container_id_list[idx])

    def _run(self, cmd: str) -> List[str]:
        return sn_remote_cmd(self.remote_ssh, cmd)

    def wait_for_starrynet_iface_names(self) -> bool:
        """
        Block until StarryNet renames docker interfaces to B{n}-eth{m}.

        Link init uses async `docker exec -d` renames; installing routes on eth*
        before rename completes leaves stale dev names and breaks forwarding.
        """
        n = len(self.container_id_list)
        deadline = time.time() + self._iface_wait_timeout
        pattern = re.compile(r"B(\d+)-eth(\d+)")
        started = time.time()
        saw_any_named = False

        while time.time() < deadline:
            ready = 0
            for node_id in range(1, n + 1):
                cid = self._container(node_id)
                lines = self._run(f"docker exec {cid} ip -o link show")
                if any(pattern.search(line) for line in lines):
                    ready += 1
            if ready > 0:
                saw_any_named = True
            if ready >= n:
                self._use_canonical_dev = True
                print(
                    f"[SDN] StarryNet interface names ready on {ready}/{n} containers."
                )
                return True
            time.sleep(0.5)

        if not saw_any_named:
            self._use_canonical_dev = False
            print(
                "[SDN] No B{n}-eth{m} renames detected; installing on eth* names."
            )
            return False

        if saw_any_named:
            self._use_canonical_dev = True
            print(
                f"[SDN] WARNING: only {ready}/{n} containers renamed after "
                f"{self._iface_wait_timeout:.0f}s; using B{{n}}-eth{{m}} egress names."
            )
            return True

        self._use_canonical_dev = False
        print(
            f"[SDN] WARNING: timed out ({self._iface_wait_timeout:.0f}s) waiting for "
            f"B{{n}}-eth{{m}} names; using scanned interface names."
        )
        return False

    def enable_ip_forwarding(self) -> None:
        """Enable forwarding and disable reverse-path filter on every node (once)."""
        if self._forwarding_enabled:
            return

        sysctl_script = (
            "sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1; "
            "sysctl -w net.ipv4.conf.all.forwarding=1 >/dev/null 2>&1; "
            "sysctl -w net.ipv4.conf.default.forwarding=1 >/dev/null 2>&1; "
            "sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null 2>&1; "
            "sysctl -w net.ipv4.conf.default.rp_filter=0 >/dev/null 2>&1; "
            "sysctl -w net.ipv4.conf.all.accept_local=1 >/dev/null 2>&1; "
            "sysctl -w net.ipv4.conf.default.accept_local=1 >/dev/null 2>&1; "
            "for d in /proc/sys/net/ipv4/conf/*/accept_local; do "
            "echo 1 > \"$d\" 2>/dev/null || true; done; "
            "for d in /proc/sys/net/ipv4/conf/*/rp_filter; do "
            "echo 0 > \"$d\" 2>/dev/null || true; done; "
            "for d in /proc/sys/net/ipv4/conf/*/forwarding; do "
            "echo 1 > \"$d\" 2>/dev/null || true; done; "
            "iptables -P FORWARD ACCEPT 2>/dev/null || true; "
            "iptables -P INPUT ACCEPT 2>/dev/null || true; "
            "iptables -P OUTPUT ACCEPT 2>/dev/null || true; "
            "iptables -C INPUT -p icmp -j ACCEPT 2>/dev/null || "
            "iptables -A INPUT -p icmp -j ACCEPT 2>/dev/null || true"
        )

        def _one(node_id: int) -> None:
            cid = self._container(node_id)
            self._run(
                f"docker exec {cid} sh -c {self._shell_quote(sysctl_script)}")

        n = len(self.container_id_list)
        with ThreadPoolExecutor(max_workers=self.parallel_workers) as pool:
            list(pool.map(_one, range(1, n + 1)))
        self._forwarding_enabled = True
        print(
            f"[SDN] Forwarding enabled (ip_forward=1, rp_filter=0) on {n} containers."
        )

    def refresh_address_caches(self) -> None:
        """One ip addr dump per container; fill host and peer-gateway maps."""
        self._host_cache.clear()
        self._node_ips.clear()
        self._peer_gateway.clear()
        self._local_ip.clear()
        self._peer_dev.clear()

        node_addrs: Dict[int, List[Tuple[str, str]]] = {}

        def _scan(node_id: int) -> Tuple[int, List[Tuple[str, str]]]:
            cid = self._container(node_id)
            lines = self._run(f"docker exec {cid} ip -4 -o addr show scope global")
            entries: List[Tuple[str, str]] = []
            for line in lines:
                clean = line.strip().replace("\\", "")
                match = _IFACE_LINE_RE.match(clean)
                if not match:
                    continue
                iface, ip, _prefix = match.group(1), match.group(2), int(match.group(3))
                if _is_management_ip(ip):
                    continue
                entries.append((ip, iface.split("@")[0]))
            return node_id, entries

        n = len(self.container_id_list)
        with ThreadPoolExecutor(max_workers=self.parallel_workers) as pool:
            for node_id, entries in pool.map(_scan, range(1, n + 1)):
                node_addrs[node_id] = entries

        peer_gw_inferred, self._peer_dev, node_ifaces, self._local_ip = (
            build_peer_maps(
                node_addrs,
                self.constellation_size,
                iface_names_only=self._use_canonical_dev,
            )
        )
        self._fill_peer_gateways_from_neighbors(fallback=peer_gw_inferred)

        total_ips = 0
        for node_id, entries in node_addrs.items():
            scan_ifaces: List[Tuple[int, int, str]] = []
            for ip, iface in entries:
                peer = _iface_peer(node_id, iface) or node_id
                scan_ifaces.append((node_id, peer, ip))
            ips = node_routable_ips(
                node_id, scan_ifaces, self.constellation_size)
            if ips:
                self._node_ips[node_id] = ips
                self._host_cache[node_id] = ips[0]
                total_ips += len(ips)

        pairing = (
            "B{n}-eth{m} interfaces"
            if self._use_canonical_dev
            else "subnet fallback (eth*)"
        )
        print(
            f"[SDN] Address scan ({pairing}): {len(self._host_cache)} nodes, "
            f"{total_ips} routable IPs, "
            f"{len(self._peer_gateway)} peer gateways (from neighbor ifaces), "
            f"{len(self._peer_dev)} egress ifaces."
        )
        self._addresses_refreshed = True

    def _read_iface_ipv4(self, node_id: int, iface: str) -> Optional[str]:
        """Read the IPv4 address assigned to a specific interface in a container."""
        cid = self._container(node_id)
        lines = self._run(
            f"docker exec {cid} ip -4 -o addr show dev {iface} scope global",
        )
        for line in lines:
            clean = line.strip().replace("\\", "")
            match = _IFACE_LINE_RE.match(clean)
            if match:
                return match.group(2)
        return None

    def _neighbor_ip_on_subnet(self, node_id: int, local_ip: str) -> Optional[str]:
        """Find the other host address on the same /24 as local_ip in node_id."""
        subnet = _subnet_key(local_ip, 24)
        cid = self._container(node_id)
        lines = self._run(
            f"docker exec {cid} ip -4 -o addr show scope global",
        )
        candidates: List[str] = []
        for line in lines:
            clean = line.strip().replace("\\", "")
            match = _IFACE_LINE_RE.match(clean)
            if not match:
                continue
            ip = match.group(2)
            if _is_management_ip(ip) or ip == local_ip:
                continue
            if _subnet_key(ip, 24) == subnet:
                candidates.append(ip)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # Prefer GSL-style .60 toward GS, else lowest host octet (ISL .10 side).
        for ip in sorted(candidates, key=lambda a: (not a.endswith(".60"), a)):
            return ip
        return candidates[0]

    def _fill_peer_gateways_from_neighbors(
        self,
        *,
        fallback: Optional[Dict[Tuple[int, int], str]] = None,
    ) -> None:
        """
        Set peer_gateway[(peer, src)] by reading B{peer}-eth{src} on the peer
        container. This avoids inferring next-hop IPs from shared /24 subnets.
        """
        edges: Set[Tuple[int, int]] = set(self._local_ip.keys())
        edges.update(self._peer_dev.keys())

        def _one(edge: Tuple[int, int]) -> Optional[Tuple[int, int, str]]:
            src, peer = edge
            lip = self._local_ip.get((src, peer))
            if lip is None:
                lip = self._read_iface_ipv4(src, f"B{src}-eth{peer}")
            if lip is None:
                return None
            gw = self._neighbor_ip_on_subnet(peer, lip)
            if gw is None:
                gw = self._read_iface_ipv4(peer, f"B{peer}-eth{src}")
            if gw:
                return (peer, src, gw)
            return None

        self._peer_gateway.clear()
        filled = 0
        with ThreadPoolExecutor(max_workers=self.parallel_workers) as pool:
            for result in pool.map(_one, edges):
                if result:
                    peer, src, gw = result
                    self._peer_gateway[(peer, src)] = gw
                    filled += 1
        if fallback:
            for key, gw in fallback.items():
                self._peer_gateway.setdefault(key, gw)
        print(
            f"[SDN] Neighbor gateways: {filled} from container ifaces, "
            f"{len(self._peer_gateway)} total."
        )

    def destination_route(self, node_id: int) -> Optional[str]:
        """Primary /32 for backward compatibility (ping destination resolution)."""
        host = self._host_cache.get(node_id)
        if host is None:
            return None
        return f"{host}/32"

    def destination_routes(self, node_id: int) -> List[str]:
        """All /32 targets for a node (every interface address)."""
        ips = self._node_ips.get(node_id)
        if not ips:
            one = self._host_cache.get(node_id)
            return [f"{one}/32"] if one else []
        return [f"{ip}/32" for ip in ips]

    def peer_gateway_ip_from_cache(self, src: int, next_hop: int) -> Optional[str]:
        """Neighbor gateway from address caches populated at last refresh."""
        return peer_gateway_from_maps(
            self._local_ip, self._peer_gateway, src, next_hop)

    def peer_gateway_ip(self, src: int, next_hop: int) -> Optional[str]:
        """Neighbor IP on the link toward next_hop (must not be our local address)."""
        cached = self.peer_gateway_ip_from_cache(src, next_hop)
        if cached is not None:
            return cached

        local = self._local_ip.get((src, next_hop))
        if local is None:
            local = self._read_iface_ipv4(src, f"B{src}-eth{next_hop}")
            if local:
                self._local_ip[(src, next_hop)] = local
        if local:
            gw = self._neighbor_ip_on_subnet(next_hop, local)
            if gw and gw != local:
                self._peer_gateway[(next_hop, src)] = gw
                return gw
            gw = self._read_iface_ipv4(next_hop, f"B{next_hop}-eth{src}")
            if gw and gw != local:
                self._peer_gateway[(next_hop, src)] = gw
                return gw
        remote = self._local_ip.get((next_hop, src))
        if (
            local
            and remote
            and remote != local
            and _subnet_key(local, 24) == _subnet_key(remote, 24)
        ):
            self._peer_gateway[(next_hop, src)] = remote
            return remote
        gw = self._peer_gateway.get((next_hop, src))
        if gw is None or gw == local:
            return None
        return gw

    def _ensure_fib_gateways(self, fib: Fib) -> int:
        """
        Resolve any missing (src, next_hop) gateways before route-key build.

        One docker exec per missing edge (parallel), not per /32 route entry.
        Returns the number of edges resolved live.
        """
        edges: Set[Tuple[int, int]] = set()
        for src, entries in fib.items():
            for _dest, next_hop in entries.items():
                edges.add((src, next_hop))

        missing = [
            edge for edge in edges
            if self.peer_gateway_ip_from_cache(*edge) is None
        ]
        if not missing:
            return 0

        def _resolve(edge: Tuple[int, int]) -> None:
            src, next_hop = edge
            self.peer_gateway_ip(src, next_hop)

        with ThreadPoolExecutor(max_workers=self.parallel_workers) as pool:
            list(pool.map(_resolve, missing))
        return len(missing)

    def _egress_dev(self, src: int, next_hop: int) -> str:
        canonical = f"B{src}-eth{next_hop}"
        if self._use_canonical_dev:
            return canonical
        return self._peer_dev.get((src, next_hop)) or canonical

    def _route_key_cidr(
        self, src: int, dest_cidr: str, next_hop: int,
    ) -> Optional[_ROUTE_KEY]:
        gw = self.peer_gateway_ip_from_cache(src, next_hop)
        if gw is None:
            return None
        dev = self._egress_dev(src, next_hop)
        host = _host_from_route(dest_cidr)
        if gw == host:
            return (dest_cidr, None, dev)
        return (dest_cidr, gw, dev)

    def _build_route_keys(self, fib: Fib) -> Tuple[Dict[int, Set[_ROUTE_KEY]], int]:
        keys: Dict[int, Set[_ROUTE_KEY]] = {
            n: set() for n in range(1, len(self.container_id_list) + 1)
        }
        failed = 0
        for src, entries in fib.items():
            for dest, next_hop in entries.items():
                dest_routes = self.destination_routes(dest)
                if not dest_routes:
                    failed += 1
                    continue
                for dest_cidr in dest_routes:
                    key = self._route_key_cidr(src, dest_cidr, next_hop)
                    if key is None:
                        failed += 1
                        continue
                    keys[src].add(key)
        return keys, failed

    def install_fib(
        self,
        fib: Fib,
        *,
        refresh_addresses: bool = False,
        reset_installed: bool = False,
    ) -> Dict[str, int]:
        """Apply FIB; return counts {installed, skipped, failed, deleted}.

        refresh_addresses re-scans container interfaces (needed when the topology
        changed). reset_installed additionally discards the installed-route
        baseline, forcing a full reinstall; leave it False for incremental,
        make-before-break updates that push only changed next-hops.
        """
        stats = {
            "installed": 0,
            "skipped": 0,
            "failed": 0,
            "deleted": 0,
            "on_link": 0,
            "nodes_touched": 0,
        }

        self.enable_ip_forwarding()
        if refresh_addresses or not self._addresses_refreshed:
            if reset_installed:
                self._installed.clear()
            if refresh_addresses:
                self.wait_for_starrynet_iface_names()
            self.refresh_address_caches()

        gateways_resolved = self._ensure_fib_gateways(fib)
        new_installed, failed = self._build_route_keys(fib)
        stats["failed"] = failed
        stats["gateways_resolved"] = gateways_resolved
        stats["on_link"] = sum(
            1 for routes in new_installed.values() for _d, gw, _dev in routes
            if gw is None
        )

        work: List[Tuple[int, Set[_ROUTE_KEY], Set[_ROUTE_KEY]]] = []
        for src in range(1, len(self.container_id_list) + 1):
            old = self._installed.get(src, set())
            new = new_installed.get(src, set())
            if new == old:
                stats["skipped"] += len(new)
                continue
            to_add = new - old
            to_del = old - new
            stats["skipped"] += len(new & old)
            if to_add or to_del:
                work.append((src, to_add, to_del))

        stats["nodes_touched"] = len(work)
        if not work:
            self._installed = new_installed
            return stats

        with ThreadPoolExecutor(max_workers=self.parallel_workers) as pool:
            futures = [
                pool.submit(self._apply_node_routes, src, to_add, to_del)
                for src, to_add, to_del in work
            ]
            for fut in as_completed(futures):
                installed, deleted = fut.result()
                stats["installed"] += installed
                stats["deleted"] += deleted

        self._installed = new_installed
        return stats

    @staticmethod
    def _route_del_cmd(dest_cidr: str, gw: Optional[str], dev: str) -> str:
        if gw:
            return (
                f"ip route del {dest_cidr} via {gw} dev {dev} "
                f"2>/dev/null || true"
            )
        return f"ip route del {dest_cidr} dev {dev} 2>/dev/null || true"

    @staticmethod
    def _route_add_cmd(dest_cidr: str, gw: Optional[str], dev: str) -> str:
        if gw:
            return f"ip route replace {dest_cidr} via {gw} dev {dev}"
        return f"ip route replace {dest_cidr} dev {dev}"

    @classmethod
    def _node_route_script(
        cls,
        to_add: Set[_ROUTE_KEY],
        to_del: Set[_ROUTE_KEY],
    ) -> List[str]:
        """Commands for one node, make-before-break: add/replace first, del last.

        `ip route replace` atomically updates a changed next-hop, so issuing all
        adds before any deletes means a destination is never without a route
        during an update. A delete only removes a destination that truly left the
        FIB; for a merely-rerouted destination the matching old (gw, dev) no
        longer exists after the replace, so its delete is a harmless no-op.
        """
        cmds: List[str] = []
        for dest_cidr, gw, dev in to_add:
            cmds.append(cls._route_add_cmd(dest_cidr, gw, dev))
        for dest_cidr, gw, dev in to_del:
            cmds.append(cls._route_del_cmd(dest_cidr, gw, dev))
        return cmds

    def _apply_node_routes(
        self,
        src: int,
        to_add: Set[_ROUTE_KEY],
        to_del: Set[_ROUTE_KEY],
    ) -> Tuple[int, int]:
        if not to_add and not to_del:
            return 0, 0
        cid = self._container(src)
        cmds = self._node_route_script(to_add, to_del)
        # Avoid oversized shell one-liners (ARG_MAX / docker exec limits).
        chunk = 48
        for i in range(0, len(cmds), chunk):
            script = "; ".join(cmds[i:i + chunk])
            self._run(f"docker exec {cid} sh -c {self._shell_quote(script)}")
        return len(to_add), len(to_del)

    @staticmethod
    def _shell_quote(script: str) -> str:
        return "'" + script.replace("'", "'\"'\"'") + "'"

    def dump_route_tables(
        self,
        node_ids: List[int],
        output_dir: str,
        label: str,
    ) -> List[str]:
        """Write `ip route show` from selected nodes; return output paths."""
        os.makedirs(output_dir, exist_ok=True)
        paths: List[str] = []

        def _dump(node_id: int) -> str:
            cid = self._container(node_id)
            lines = self._run(f"docker exec {cid} ip route show")
            path = os.path.join(output_dir, f"routes_B{node_id}_{label}.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            return path

        with ThreadPoolExecutor(max_workers=self.parallel_workers) as pool:
            futures = {pool.submit(_dump, nid): nid for nid in node_ids}
            for fut in as_completed(futures):
                paths.append(fut.result())
        return sorted(paths)
