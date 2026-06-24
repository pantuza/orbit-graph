"""Tests for plot_results (no display; uses matplotlib Agg backend)."""

import os

import pytest

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

from experiments.plot_results import (  # noqa: E402
    OUTAGE_SUMMARY,
    OUTAGE_RAW,
    PING_BY_PHASE,
    CONTROL_BY_REASON,
    CONTROL_RAW,
    discover_results_dir,
    generate_figures,
    read_table,
    _first_event_rows_per_rep,
    _ping_phase_plottable,
)


def _write_csv(path: str, header: str, *rows: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header + "\n")
        for row in rows:
            fh.write(row + "\n")


def test_read_table_validates_columns(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    rows = read_table(str(p), ["a", "b"])
    assert len(rows) == 1
    with pytest.raises(ValueError, match="missing columns"):
        read_table(str(p), ["a", "missing"])


def test_generate_figures_from_minimal_csvs(tmp_path):
    _write_csv(
        tmp_path / OUTAGE_SUMMARY,
        "nodes,mode,reason,n,reps,outage_ms_mean,outage_ms_std,still_down",
        "27,ospf,topology_change,1,1,5000.0,100.0,0",
        "27,sdn,proactive_handover,1,1,0.0,0.0,0",
        "38,ospf,topology_change,1,10,5200.0,200.0,0",
        "38,sdn,proactive_handover,1,10,0.0,0.0,0",
        "27,ospf,damage_recovery,1,1,3000.0,0.0,0",
        "27,sdn,damage_recovery,1,1,7000.0,0.0,0",
    )
    _write_csv(
        tmp_path / OUTAGE_RAW,
        "mode,profile,nodes,rep,seed,reason,time_index,event,outage_ms,still_down",
        "ospf,full,27,1,1001,topology_change,53,topology_change@t53,5000.0,False",
        "ospf,full,27,1,1001,topology_change,80,topology_change@t80,90000.0,True",
        "sdn,full,27,1,1001,proactive_handover,53,proactive_handover@t53,0.0,False",
        "ospf,full,38,1,1001,topology_change,23,topology_change@t23,5200.0,False",
        "sdn,full,38,1,1001,proactive_handover,23,proactive_handover@t23,0.0,False",
        "ospf,full,27,1,1001,damage_recovery,5,damage_recovery@t5,6000.0,False",
        "ospf,full,27,1,1001,damage_recovery,10,damage_recovery@t10,0.0,False",
        "sdn,full,27,1,1001,damage_recovery,5,damage_recovery@t5,12000.0,False",
        "sdn,full,27,1,1001,damage_recovery,10,damage_recovery@t10,2500.0,False",
        "ospf,full,38,1,1001,damage_recovery,5,damage_recovery@t5,5000.0,False",
        "ospf,full,38,1,1001,damage_recovery,10,damage_recovery@t10,0.0,False",
        "sdn,full,38,1,1001,damage_recovery,5,damage_recovery@t5,15000.0,False",
        "sdn,full,38,1,1001,damage_recovery,10,damage_recovery@t10,2800.0,False",
    )
    _write_csv(
        tmp_path / PING_BY_PHASE,
        "nodes,mode,phase,n,loss_mean_pct,loss_std_pct,"
        "rtt_mean_ms,rtt_std_ms,n_rtt_samples",
        "27,ospf,handover,1,10,0,0,240.0,0,1",
        "27,sdn,handover,1,0,0,30.0,0,1",
        "38,ospf,post_handover,1,100,0,,,0",
        "38,sdn,post_handover,1,0,0,29.0,0,1",
        "66,ospf,steady,10,100,0,,,0",
        "66,sdn,steady,10,100,0,,,0",
    )
    _write_csv(
        tmp_path / CONTROL_RAW,
        "mode,profile,nodes,rep,seed,time_index,reason,event,time_ms,"
        "compute_ms,install_ms,routing_event,installed,fib_unchanged",
        "ospf,full,27,1,1001,53,topology_change,topology_change@t53,250.0,,,True,,",
        "sdn,full,27,1,1001,53,proactive_handover,proactive_handover@t53,"
        "5000.0,2.0,4998.0,True,150,False",
        "sdn,full,27,1,1001,80,proactive_handover,proactive_handover@t80,"
        "9000.0,2.0,8998.0,True,5,False",
        "ospf,full,38,1,1001,23,topology_change,topology_change@t23,240.0,,,True,,",
        "sdn,full,38,1,1001,23,proactive_handover,proactive_handover@t23,"
        "5300.0,3.0,5297.0,True,164,False",
    )
    _write_csv(
        tmp_path / CONTROL_BY_REASON,
        "nodes,mode,reason,n,reps,time_ms_mean,time_ms_std,"
        "compute_ms_mean,install_ms_mean,installed_mean,installed_std",
        "27,ospf,topology_change,1,1,250.0,0.0,,,",
        "27,sdn,proactive_handover,1,1,5000.0,0.0,2.0,4998.0,150.0,0.0",
        "38,ospf,topology_change,1,10,240.0,10.0,,,",
        "38,sdn,proactive_handover,1,10,5300.0,100.0,3.0,5297.0,164.0,5.0",
    )
    out = tmp_path / "figures"
    paths = generate_figures(str(tmp_path), str(out), dpi=80)
    assert paths
    stems = {os.path.splitext(os.path.basename(p))[0] for p in paths}
    assert "outage_vs_nodes" in stems
    assert "outage_damage_recovery" in stems
    assert "rtt_by_phase" in stems
    assert "loss_by_phase" in stems
    assert "control_handover" in stems
    assert "routes_installed_handover" in stems
    for p in paths:
        assert os.path.getsize(p) > 0


def test_first_event_rows_per_rep_prefers_earliest():
    rows = [
        {"nodes": "27", "mode": "sdn", "rep": "1", "time_index": "80",
         "reason": "proactive_handover", "installed": "5"},
        {"nodes": "27", "mode": "sdn", "rep": "1", "time_index": "53",
         "reason": "proactive_handover", "installed": "150"},
    ]
    picked = _first_event_rows_per_rep(rows, "proactive_handover")
    assert len(picked) == 1
    assert picked[0]["time_index"] == "53"
    assert picked[0]["installed"] == "150"


def test_ping_phase_plottable_skips_truncated_steady():
    row = {"phase": "steady", "loss_mean_pct": "100", "n_rtt_samples": "0",
           "rtt_mean_ms": ""}
    assert not _ping_phase_plottable(row, "rtt_mean_ms")
    row["n_rtt_samples"] = "10"
    assert not _ping_phase_plottable(row, "rtt_mean_ms")


def test_discover_results_dir(tmp_path):
    assert discover_results_dir(str(tmp_path)) == str(tmp_path.resolve())
    with pytest.raises(FileNotFoundError):
        discover_results_dir(str(tmp_path / "nope"))
