"""Unit tests for SDN routing logic (no Docker required)."""

import numpy as np

from starrynet.sdn_routing.dataplane import (
    DockerDataplane,
    build_peer_maps,
    canonical_host_ip,
    node_routable_ips,
    peer_gateway_from_maps,
)
from starrynet.sdn_routing.routing import compute_fib, fib_equal
from starrynet.sdn_routing.topology import graph_from_matrix


def test_peer_gateway_from_maps():
    node_addrs = {
        7: [("10.0.6.30", "B7-eth8"), ("10.0.13.40", "B7-eth13")],
        8: [("10.0.6.20", "B8-eth7"), ("10.0.16.20", "B8-eth13")],
        13: [("10.0.13.10", "B13-eth7"), ("10.0.16.30", "B13-eth8")],
    }
    peer_gw, _dev, _ifaces, local = build_peer_maps(
        node_addrs, constellation_size=25, iface_names_only=True)
    assert peer_gateway_from_maps(local, peer_gw, 7, 8) == "10.0.6.20"
    assert peer_gateway_from_maps(local, peer_gw, 8, 13) is not None
    assert peer_gateway_from_maps(local, peer_gw, 1, 2) is None


def test_fib_equal():
    a = {1: {2: 2, 3: 2}, 2: {1: 1, 3: 3}}
    b = {1: {2: 2, 3: 2}, 2: {1: 1, 3: 3}}
    c = {1: {2: 2, 3: 3}, 2: {1: 1, 3: 3}}
    assert fib_equal(a, b)
    assert not fib_equal(a, c)


def test_triangle_shortest_path():
    matrix = np.zeros((3, 3))
    matrix[0, 1] = matrix[1, 0] = 10
    matrix[1, 2] = matrix[2, 1] = 1
    matrix[0, 2] = matrix[2, 0] = 100
    graph = graph_from_matrix(matrix, link_threshold=0.01)
    fib = compute_fib(graph, 3)
    assert fib[1][3] == 2
    assert fib[1][2] == 2


def test_canonical_host_ip_gs():
    host = canonical_host_ip(27, [], constellation_size=25)
    assert host == "9.27.27.10"


def test_canonical_host_ip_satellite_gsl():
    ifaces = [(7, 26, "9.1.7.50"), (7, 8, "10.0.1.40")]
    host = canonical_host_ip(7, ifaces, constellation_size=25)
    assert host == "9.1.7.50"


def test_canonical_host_ip_satellite_isl_lowest_peer():
    ifaces = [
        (11, 6, "10.0.11.10"),
        (11, 12, "10.0.12.30"),
    ]
    host = canonical_host_ip(11, ifaces, constellation_size=25)
    assert host == "10.0.11.10"


def test_canonical_ignores_other_owner():
    # Addresses on B12-* must not be picked for node 11
    ifaces = [(11, 6, "10.0.11.10"), (12, 11, "10.0.11.40")]
    host = canonical_host_ip(11, ifaces, constellation_size=25)
    assert host == "10.0.11.10"


def test_build_peer_maps_skips_multi_node_subnet():
    node_addrs = {
        7: [("10.0.13.40", "eth1")],
        8: [("10.0.13.10", "eth2")],
        13: [("10.0.13.10", "eth1")],
    }
    peer_gw, _, _, _ = build_peer_maps(node_addrs, constellation_size=25)
    assert (8, 7) not in peer_gw
    assert (7, 8) not in peer_gw


def test_build_peer_maps_rejects_subnet_mismatch():
    node_addrs = {
        7: [("10.0.13.10", "B7-eth8"), ("10.0.6.30", "B7-eth13")],
        8: [("10.0.6.20", "B8-eth7")],
        13: [("10.0.13.40", "B13-eth7")],
    }
    peer_gw, _, _, local = build_peer_maps(
        node_addrs, constellation_size=25, iface_names_only=True)
    assert local[(7, 8)] == "10.0.13.10"
    assert (8, 7) not in peer_gw


def test_subnet_fallback_skips_indirect_same_subnet():
    """Nodes sharing a /24 only via different peers must not be paired."""
    node_addrs = {
        7: [("10.0.13.40", "B7-eth13"), ("10.0.6.30", "B7-eth8")],
        8: [("10.0.16.20", "B8-eth13"), ("10.0.6.20", "B8-eth7")],
        13: [("10.0.13.10", "B13-eth7"), ("10.0.16.30", "B13-eth8")],
    }
    peer_gw, _, _, _local = build_peer_maps(
        node_addrs, constellation_size=25, iface_names_only=False)
    assert peer_gw[(8, 7)] == "10.0.6.20"
    assert peer_gw[(13, 7)] == "10.0.13.10"
    assert peer_gw[(13, 8)] == "10.0.16.30"


def test_build_peer_maps_from_iface_names():
    node_addrs = {
        7: [
            ("10.0.6.30", "B7-eth8"),
            ("10.0.13.40", "B7-eth13"),
            ("9.1.7.50", "B7-eth26"),
        ],
        8: [("10.0.6.20", "B8-eth7"), ("10.0.16.20", "B8-eth13")],
        13: [("10.0.13.10", "B13-eth7"), ("10.0.16.30", "B13-eth8")],
    }
    peer_gw, peer_dev, _ifaces, _local = build_peer_maps(
        node_addrs, constellation_size=25, iface_names_only=True)
    assert peer_gw[(8, 7)] == "10.0.6.20"
    assert peer_gw[(13, 7)] == "10.0.13.10"
    assert peer_gw[(13, 8)] == "10.0.16.30"
    assert peer_dev[(7, 8)] == "B7-eth8"
    assert peer_dev[(8, 13)] == "B8-eth13"


def test_build_peer_maps_from_subnets():
    node_addrs = {
        7: [("9.1.7.50", "B7-eth26"), ("10.0.13.40", "B7-eth13")],
        13: [("9.2.13.50", "B13-eth27"), ("10.0.13.10", "B13-eth7")],
        26: [("9.1.7.60", "B26-eth7")],
        27: [("9.2.13.60", "B27-eth13"), ("9.27.27.10", "B27-default")],
    }
    peer_gw, peer_dev, _ifaces, _local = build_peer_maps(
        node_addrs, constellation_size=25, iface_names_only=False)
    assert peer_gw[(7, 26)] == "9.1.7.50"
    assert peer_gw[(26, 7)] == "9.1.7.60"
    assert peer_gw[(27, 13)] == "9.2.13.60"
    assert peer_gw[(13, 27)] == "9.2.13.50"
    assert peer_gw[(13, 7)] == "10.0.13.10"
    assert peer_gw[(7, 13)] == "10.0.13.40"
    assert peer_dev[(7, 13)] == "B7-eth13"


def test_node_routable_ips_gs_includes_gsl():
    ifaces = [
        (26, 26, "9.26.26.10"),
        (26, 7, "9.1.7.60"),
    ]
    ips = node_routable_ips(26, ifaces, constellation_size=25)
    assert ips == ["9.26.26.10", "9.1.7.60"]


def test_node_routable_ips_satellite():
    ifaces = [
        (7, 26, "9.1.7.50"),
        (7, 8, "10.0.13.40"),
        (7, 13, "10.0.6.30"),
    ]
    ips = node_routable_ips(7, ifaces, constellation_size=25)
    assert ips == ["9.1.7.50"]


def test_node_routable_ips_isl_only_satellite():
    ifaces = [(3, 4, "10.0.1.40"), (3, 8, "10.0.2.30")]
    ips = node_routable_ips(3, ifaces, constellation_size=25)
    assert ips == ["10.0.1.40"]


def test_node_routable_ips_gs27_includes_default():
    ifaces = [(27, 13, "9.2.13.60"), (27, 27, "9.27.27.10")]
    ips = node_routable_ips(27, ifaces, constellation_size=25)
    assert "9.27.27.10" in ips
    assert "9.2.13.60" in ips


def test_host_routes_unique_per_dest():
    hosts = {7: "9.1.7.50", 13: "9.2.13.50", 27: "9.27.27.10", 1: "10.0.1.40"}
    gw, dev = "10.0.4.20", "B7-eth2"
    keys = {(f"{hosts[d]}/32", gw, dev) for d in hosts}
    assert len(keys) == len(hosts)


def test_make_before_break_adds_precede_dels():
    """Incremental update must replace/add routes before deleting old ones."""
    to_add = {("9.38.38.10/32", "9.2.18.50", "B37-eth18")}
    to_del = {("9.38.38.10/32", "9.1.10.50", "B37-eth10")}
    cmds = DockerDataplane._node_route_script(to_add, to_del)
    add_idx = next(i for i, c in enumerate(cmds) if c.startswith("ip route replace"))
    del_idx = next(i for i, c in enumerate(cmds) if "ip route del" in c)
    assert add_idx < del_idx, cmds


def test_route_add_uses_replace_for_atomic_update():
    cmd = DockerDataplane._route_add_cmd("9.38.38.10/32", "9.2.18.50", "B37-eth18")
    assert cmd == "ip route replace 9.38.38.10/32 via 9.2.18.50 dev B37-eth18"
    on_link = DockerDataplane._route_add_cmd("9.1.7.60/32", None, "B7-eth26")
    assert on_link == "ip route replace 9.1.7.60/32 dev B7-eth26"


def test_route_del_is_best_effort():
    cmd = DockerDataplane._route_del_cmd("9.38.38.10/32", "9.1.10.50", "B37-eth10")
    assert cmd.startswith("ip route del 9.38.38.10/32 via 9.1.10.50 dev B37-eth10")
    assert cmd.rstrip().endswith("|| true")


def test_node_route_script_empty_when_no_changes():
    assert DockerDataplane._node_route_script(set(), set()) == []


if __name__ == "__main__":
    test_triangle_shortest_path()
    test_canonical_host_ip_gs()
    test_canonical_host_ip_satellite_gsl()
    test_canonical_host_ip_satellite_isl_lowest_peer()
    test_canonical_ignores_other_owner()
    test_build_peer_maps_skips_multi_node_subnet()
    test_build_peer_maps_rejects_subnet_mismatch()
    test_subnet_fallback_skips_indirect_same_subnet()
    test_build_peer_maps_from_iface_names()
    test_build_peer_maps_from_subnets()
    test_host_routes_unique_per_dest()
    test_make_before_break_adds_precede_dels()
    test_route_add_uses_replace_for_atomic_update()
    test_route_del_is_best_effort()
    test_node_route_script_empty_when_no_changes()
    print("ok")
