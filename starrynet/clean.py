#!/usr/bin/python
# -*- coding: UTF-8 -*-
"""
StarryNet Cleanup
"""

import os


ORBITGRAPH_NETWORK_PREFIXES = ("La_", "Le_", "GSL_", "GS_")


def cleanup():
    print("Deleting OrbitGraph bridges and containers...")

    # Remove somente containers pertencentes ao OrbitGraph.
    os.system(
        "docker rm -f $(docker ps -aq --filter name=ovs_container) "
        "2>/dev/null; true"
    )

    # Remove somente redes pertencentes ao OrbitGraph.
    with os.popen("docker network ls --format '{{.Name}}'") as f:
        all_br_info = f.readlines()

        for line in all_br_info:
            network_name = line.strip()

            if network_name.startswith(ORBITGRAPH_NETWORK_PREFIXES):
                print("docker network rm " + network_name)
                os.system("docker network rm " + network_name)


if __name__ == "__main__":
    cleanup()
    