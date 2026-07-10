# Running OrbitGraph Locally on macOS

OrbitGraph simulates satellite network constellations using Docker containers and Linux
traffic control (`tc`), building on the StarryNet emulator. This guide explains how to run
it on macOS without a remote Linux server.

## How it works

Each satellite and ground station is a Docker container. Links between them are Docker
bridge networks. The propagation delay, packet loss, and bandwidth of every link are
enforced by the Linux kernel's `tc netem` traffic shaper running inside each container.
Docker Desktop for Mac runs Linux containers in a lightweight VM, so all Linux networking
primitives work transparently.

## Prerequisites

- macOS with [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed
  and running
- [pyenv](https://github.com/pyenv/pyenv) and
  [pyenv-virtualenv](https://github.com/pyenv/pyenv-virtualenv)

## Setup

```bash
# 1. Create and activate the project virtualenv
pyenv virtualenv 3.11.4 orbit-graph
cd /path/to/orbit-graph
pyenv local orbit-graph        # writes .python-version, auto-activates on cd

# 2. Install dependencies into the virtualenv
python -m pip install -r tools/requirements.txt
python -m pip install -e .

# 3. Pull the satellite node container image (one-time, ~300 MB)
docker pull lwsen/starlab_node:1.0
```

> **Important:** always use `python -m pip` instead of bare `pip` to ensure packages are
> installed into the active pyenv virtualenv and not the system Python.

## Configuration

`config.json` controls the constellation. The relevant fields for local use:

| Field | Default | Meaning |
|---|---|---|
| `# of orbit` | 5 | Number of orbital planes |
| `# of satellites` | 5 | Satellites per plane (total = orbits × sats) |
| `GS number` | 2 | Ground stations |
| `Duration (s)` | 100 | Simulation wall-clock length in seconds |
| `local_mode` | 1 | **1 = run locally; 0 = use remote SSH machine** |

With the defaults you get 25 satellites + 2 ground stations = **27 nodes**.

## Running the example

```bash
python example.py
```

The example (`example.py`) exercises the full API: node creation, link setup, routing,
ping, iperf, damage/recovery, and segment routing.

## Makefile (setup and OSPF vs SDN comparison)

From the project root (with Docker running):

```bash
make setup          # pip install -e . + docker pull lwsen/starlab_node:1.0
make test           # SDN routing unit tests

make ospf           # full OSPF experiment  → starlink-…-ospf-full/
make sdn            # full SDN experiment   → starlink-…-sdn-full/
make stats          # summarize both full runs (ping + metrics)

make ospf_simple    # basic profile (no damage)
make sdn_simple
make stats_simple

make compare        # ospf + sdn + stats (long)
make help           # list all targets
```

Artifact directories are created automatically; no manual `mv` is needed.

## What happens step by step

### 1. Orbital computation — `StarryNet(...)`

Before any container is started the `Observer` class propagates all 25 satellite orbits
forward 100 seconds using the **SGP4** model (the same standard used by NORAD). For each
second it records every satellite's position in Latitude/Longitude/Altitude and converts
it to Cartesian coordinates (km).

From those positions it builds a **27×27 delay matrix** per second: each cell holds the
one-way propagation delay in milliseconds between two nodes, derived from their
3D distance divided by the speed of light. Ground stations are fixed points; satellites
move. The matrices are written to `starlink-.../delay/N.txt`.

The observer also diffs consecutive matrices to produce `Topo_leo_change.txt`, a
chronological log of exactly when links appear or disappear as satellites orbit overhead.

### 2. Node creation — `create_nodes()`

`docker run -d` is called 27 times, once per node. Each container:

- Is named `ovs_container_1` … `ovs_container_27`
- Has `--cap-add ALL --privileged` so `tc` and `ip link` work inside it
- Runs `ping -i 3600 127.0.0.1` as a no-op keepalive process

At this point the containers are alive but fully isolated from each other.

### 3. Link initialisation — `create_links()`

`sn_orchestrater.py` reads the delay matrix for second 1 and wires up the topology. For
each satellite, two link types are established in parallel threads:

- **Intra-orbit ISL** (`Le_` networks, `10.x.x.0/24`): connects each satellite to the
  next one in the same orbital plane.
- **Inter-orbit ISL** (`La_` networks, `10.x.x.0/24`): connects each satellite to the
  matching satellite in the adjacent orbital plane.

For every link the orchestrater:

1. Creates a Docker bridge network and connects both containers to it
2. Finds the new `veth` interface inside each container via `ip addr | grep`
3. Renames it from Docker's default `ethN` to a readable name like `B3-eth8` (container
   3's interface toward container 8)
4. Applies `tc qdisc netem delay Xms loss 1% rate 5Gbit` — the delay value is the
   physically correct propagation delay for that satellite pair at second 1

**Ground-satellite links (GSLs)** use the same Docker/`tc` steps with `9.x.x.0/24` subnets
and names `GS_N` / `GSL_X-Y`, but unlike fixed ISL peers, ground stations stay at
configured lat/long while satellites move: the `Observer` precomputes each second who is
in range (latitude window, max distance, optional antenna cap) into the delay matrices,
and `create_links` only brings up GSLs where `delay/1.txt` has a non-zero entry—later
changes come from `Topo_leo_change.txt`, not from GS motion.

### 4. Routing — `run_routing_deamon()`

During init, `generate_conf()` wrote an OSPF configuration file for every node
(`starlink-.../conf/bird-25-2/BN.conf`). Each config lists the node's interfaces by
their `BN-ethM` names.

The orchestrater copies each config into its container with `docker cp` and launches the
**Bird** routing daemon with `docker exec ... bird -c BN.conf`. All 27 Bird instances run
in parallel and speak OSPF over the virtual ISL/GSL interfaces. The code then waits 120
seconds for OSPF to converge: nodes exchange hello packets, elect designated routers, and
flood link-state advertisements until every node has a full topology map and a populated
kernel routing table.

### 5. Emulation loop — `start_emulation()`

A wall-clock loop runs one iteration per second for 100 seconds, driven by
`Topo_leo_change.txt`. At each tick scheduled events fire:

**Every 10 seconds — delay update:** the delay matrix for the current second is fed to
the orchestrater, which calls `tc qdisc change dev BN-ethM root netem delay Xms` inside
each affected container. Delays change continuously as satellites move.

**t=4 — iperf throughput test:** `iperf3 -s` starts in container 14; `iperf3 -c <ip>`
runs from container 13 for 5 seconds. Results are saved to a file in the working
directory.

**t=5 — link damage:** 30% of satellites are chosen at random. On each one, `tc qdisc
change ... loss 100%` is applied to all interfaces — packets are dropped, simulating a
satellite failure.

**t=10 — link recovery:** the same interfaces have `tc qdisc change ... loss 1%` applied,
restoring the configured loss rate.

**t=20 — static route injection:** `ip route add 9.27.27.0/24 dev B1-eth2 via 10.0.1.10`
is injected into container 1, forcing traffic toward ground station 27 through a specific
next hop regardless of OSPF's choice. This is the segment routing / traffic engineering
feature.

**t=53 — live topology change:** the precomputed change log fires. Satellite 7 has moved
into range of ground station 27 — `docker network create GSL_7-27` and `docker network
connect` add the link live. Simultaneously satellite 13 has moved out of range of ground
station 27 — `docker network disconnect` and `docker network rm GSL_13-27` tear it down.

### 6. Teardown — `stop_emulation()`

All `ovs_container_*` containers are removed with `docker rm -f`. All Docker networks
(`Le_`, `La_`, `GSL_`, `GS_`) are removed in parallel. The host is left clean.

## Cleanup after a crash

If the simulation is interrupted mid-run, clean up manually:

```bash
docker rm -f $(docker ps -aq --filter name=ovs_container)
docker network ls | grep -E 'Le_|La_|GSL_|GS_' | awk '{print $2}' | xargs -r docker network rm
```

## Local mode internals

`local_mode: 1` in `config.json` makes the following substitutions at startup
(`sn_synchronizer.py`):

- SSH/SFTP connections are skipped entirely
- `sn_remote_cmd(None, cmd)` runs `cmd` locally via `subprocess`
- `LocalSFTP.put(src, dst)` copies files locally with `shutil.copy` instead of uploading
  over SFTP
- `docker service create` (Docker Swarm) is replaced with individual `docker run` calls
- Container cleanup targets only `ovs_container_*` to avoid disturbing unrelated containers

Set `local_mode: 0` and fill in `remote_machine_IP/username/password` to revert to the
original remote-machine mode.
