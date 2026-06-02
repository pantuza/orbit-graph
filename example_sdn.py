#!/usr/bin/python
# -*- coding: UTF-8 -*-
"""
StarryNet example using centralized SDN routing (control plane separated from data plane).

Set config.json "Intra-AS routing" to "SDN", or use config_sdn.json as below.
Compare against example.py (OSPF) with identical traffic/failure scripts.
"""

from starrynet.sn_sdn_adapter import dump_sdn_routes, ping_nodes_now, run_sdn_initial_routes
from starrynet.sn_synchronizer import StarryNet

if __name__ == "__main__":
    AS = [[1, 27]]
    GS_lat_long = [[50.110924, 8.682127], [46.635700, 14.311817]]
    # Shorter run, no damage — same as compare_single_run.py --profile basic
    configuration_file_path = "./config_sdn_basic.json"
    hello_interval = 1

    print("Start StarryNet (SDN routing mode, basic profile).")
    sn = StarryNet(configuration_file_path, GS_lat_long, hello_interval, AS)
    sn.create_nodes()
    sn.create_links()
    run_sdn_initial_routes(
        sn, route_dump_nodes=(7, 13, 26, 27), reinstall_on_delay_update=False)
    ping_nodes_now(sn, 26, 27, "post_init")

    sn.set_ping(26, 27, 2)
    sn.set_ping(26, 27, 5)

    sn.start_emulation()
    dump_sdn_routes(sn, label="pre_teardown", node_ids=[7, 13, 26, 27])
    sn.stop_emulation()
    print("SDN metrics written under starlink-.../sdn_metrics/")
