"""Unit tests for SDN controller handover logic (no Docker required)."""

from unittest.mock import MagicMock, patch

from starrynet.sdn_routing.config import SdnConfig
from starrynet.sdn_routing.controller import SdnController


def _controller(tmp_path) -> SdnController:
    metrics = tmp_path / "sdn_metrics"
    delay = tmp_path / "delay"
    metrics.mkdir()
    delay.mkdir()
    cfg = SdnConfig(
        constellation_size=25,
        node_count=27,
        delay_dir=str(delay),
        metrics_dir=str(metrics),
        proactive_handover=True,
    )
    ctrl = SdnController(cfg, None, ["c"] * 27)
    ctrl.dataplane = MagicMock()
    return ctrl


def test_finalize_skips_install_when_proactive_fib_matches(tmp_path):
    fib = {1: {2: 2, 3: 3}, 2: {1: 1, 3: 3}}
    ctrl = _controller(tmp_path)
    ctrl._last_fib = fib

    with patch("starrynet.sdn_routing.controller.load_graph", return_value={}):
        with patch(
            "starrynet.sdn_routing.controller.compute_fib", return_value=fib,
        ):
            metrics = ctrl.finalize_topology_change(23)

    assert metrics["fib_unchanged"] is True
    assert metrics["installed"] == 0
    assert metrics["reason"] == "topology_change"
    assert metrics["proactive_finalized"] is True
    ctrl.dataplane.install_fib.assert_not_called()


def test_finalize_installs_when_proactive_fib_differs(tmp_path):
    old_fib = {1: {2: 2}}
    new_fib = {1: {2: 3}}
    ctrl = _controller(tmp_path)
    ctrl._last_fib = old_fib
    ctrl.dataplane.install_fib.return_value = {
        "installed": 5,
        "skipped": 0,
        "failed": 0,
        "deleted": 1,
        "on_link": 0,
        "nodes_touched": 2,
    }

    with patch("starrynet.sdn_routing.controller.load_graph", return_value={}):
        with patch(
            "starrynet.sdn_routing.controller.compute_fib", return_value=new_fib,
        ):
            metrics = ctrl.finalize_topology_change(23)

    assert metrics["fib_unchanged"] is False
    assert metrics["installed"] == 5
    ctrl.dataplane.install_fib.assert_called_once()
