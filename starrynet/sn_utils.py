import os
import shutil
import subprocess
import threading
import json
import copy
import argparse
from time import sleep
import time
import numpy
import random
"""
Starrynet utils that are used in sn_synchronizer
author: Yangtao Deng (dengyt21@mails.tsinghua.edu.cn) and Zeqi Lai (zeqilai@tsinghua.edu.cn)
"""
try:
    import threading
except ImportError:
    os.system("pip3 install threading")
    import threading

try:
    import paramiko
except ImportError:
    os.system("pip3 install paramiko")
    import paramiko

try:
    import requests
except ImportError:
    os.system("pip3 install requests")
    import requests


def get_right_satellite(current_sat_id, current_orbit_id, orbit_num):
    if current_orbit_id == orbit_num - 1:
        return [current_sat_id, 0]
    else:
        return [current_sat_id, current_orbit_id + 1]


def get_down_satellite(current_sat_id, current_orbit_id, sat_num):
    if current_sat_id == sat_num - 1:
        return [0, current_orbit_id]
    else:
        return [current_sat_id + 1, current_orbit_id]


def sn_load_file(path, GS_lat_long):
    config_path = os.path.abspath(path)
    with open(config_path, "r", encoding="utf8") as f:
        table = json.load(f)
    data = {}
    data['cons_name'] = table["Name"]
    data['altitude'] = table["Altitude (km)"]
    data['cycle'] = table["Cycle (s)"]
    data['inclination'] = table["Inclination"]
    data['phase_shift'] = table["Phase shift"]
    data['orbit'] = table["# of orbit"]
    data['sat'] = table["# of satellites"]
    data['link'] = table["Satellite link"]
    data['duration'] = table["Duration (s)"]
    data['ip'] = table["IP version"]
    data['intra_as_routing'] = table["Intra-AS routing"]
    data['inter_as_routing'] = table["Inter-AS routing"]
    data['link_policy'] = table["Link policy"]
    data['handover_policy'] = table["Handover policy"]
    data['update_time'] = table["update_time (s)"]
    data['sat_bw'] = table["satellite link bandwidth (\"X\" Gbps)"]
    data['sat_ground_bw'] = table["sat-ground bandwidth (\"X\" Gbps)"]
    data['sat_loss'] = table["satellite link loss (\"X\"% )"]
    data['sat_ground_loss'] = table["sat-ground loss (\"X\"% )"]
    data['ground_num'] = table["GS number"]
    data['multi_machine'] = table[
        "multi-machine (\"0\" for no, \"1\" for yes)"]
    data['antenna_number'] = table["antenna number"]
    data['antenna_inclination'] = table["antenna_inclination_angle"]
    data['remote_machine_IP'] = table["remote_machine_IP"]
    data['remote_machine_username'] = table["remote_machine_username"]
    data['remote_machine_password'] = table["remote_machine_password"]
    data['local_mode'] = table.get("local_mode", 0)

    parser = argparse.ArgumentParser(description='manual to this script')
    parser.add_argument('--cons_name', type=str, default=data['cons_name'])
    parser.add_argument('--satellite_altitude',
                        type=int,
                        default=data['altitude'])
    parser.add_argument('--inclination', type=int, default=data['inclination'])
    parser.add_argument('--orbit_number', type=int, default=data['orbit'])
    parser.add_argument('--sat_number', type=int, default=data['sat'])
    parser.add_argument('--fac_num', type=int, default=len(GS_lat_long))
    parser.add_argument('--link_style', type=str, default=data['link'])
    parser.add_argument('--IP_version', type=str, default=data['ip'])
    parser.add_argument('--link_policy', type=str, default=data['link_policy'])
    # link delay updating granularity
    parser.add_argument('--update_interval',
                        type=int,
                        default=data['update_time'])
    parser.add_argument('--duration', type=int, default=data['duration'])
    parser.add_argument('--inter_routing',
                        type=str,
                        default=data['inter_as_routing'])
    parser.add_argument('--intra_routing',
                        type=str,
                        default=data['intra_as_routing'])
    parser.add_argument('--cycle', type=int, default=data['cycle'])
    parser.add_argument('--time_slot', type=int, default=100)
    parser.add_argument('--sat_bandwidth', type=int, default=data['sat_bw'])
    parser.add_argument('--sat_ground_bandwidth',
                        type=int,
                        default=data['sat_ground_bw'])
    parser.add_argument('--sat_loss', type=int, default=data['sat_loss'])
    parser.add_argument('--sat_ground_loss',
                        type=int,
                        default=data['sat_ground_loss'])
    parser.add_argument('--ground_num', type=int, default=data['ground_num'])
    parser.add_argument('--multi_machine',
                        type=int,
                        default=data['multi_machine'])
    parser.add_argument('--antenna_number',
                        type=int,
                        default=data['antenna_number'])
    parser.add_argument('--antenna_inclination',
                        type=int,
                        default=data['antenna_inclination'])
    parser.add_argument('--user_num', type=int, default=0)
    parser.add_argument('--remote_machine_IP',
                        type=str,
                        default=data['remote_machine_IP'])
    parser.add_argument('--remote_machine_username',
                        type=str,
                        default=data['remote_machine_username'])
    parser.add_argument('--remote_machine_password',
                        type=str,
                        default=data['remote_machine_password'])
    parser.add_argument('--local_mode', type=int, default=data['local_mode'])

    parser.add_argument('--path',
                        '-p',
                        type=str,
                        default="starrynet/config.xls")
    parser.add_argument('--hello_interval', '-i', type=int, default=10)
    parser.add_argument('--node_number', '-n', type=int, default=27)
    parser.add_argument('--GS',
                        '-g',
                        type=str,
                        default="50.110924/8.682127/46.635700/14.311817")

    # Allow host scripts (e.g. experiments/compare_single_run.py) to add their
    # own CLI flags without conflicting with StarryNet's optional overrides.
    sn_args, _unknown = parser.parse_known_args()
    return sn_args


def sn_get_param(file_):
    f = open(file_)
    ADJ = f.readlines()
    for i in range(len(ADJ)):
        ADJ[i] = ADJ[i].strip('\n')
    ADJ = [x.split(',') for x in ADJ]
    f.close()
    return ADJ


def sn_run_local_cmd(cmd):
    """Run a local shell command; return the full subprocess result."""
    return subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def sn_local_cmd(cmd):
    # stdin=DEVNULL: never hand the controlling terminal to a child command.
    # Otherwise `docker exec -t` (allocate TTY) would put the terminal into raw
    # mode, and with StarryNet's concurrent command threads the save/restore
    # races and leaves the terminal stuck (no carriage returns -> staircased
    # output). Detaching stdin makes that impossible.
    return sn_run_local_cmd(cmd).stdout.splitlines(keepends=True)


class LocalSFTP:
    """Drop-in replacement for paramiko.SFTPClient in local mode."""

    def put(self, local_path, remote_path):
        abs_src = os.path.abspath(local_path)
        abs_dst = os.path.abspath(remote_path)
        if abs_src != abs_dst:
            os.makedirs(os.path.dirname(abs_dst), exist_ok=True)
            shutil.copy(abs_src, abs_dst)


def sn_init_remote_machine(host, username, password):
    # transport = paramiko.Transport((host, 22))
    # transport.connect(username=username, password=password)
    remote_machine_ssh = paramiko.SSHClient()
    # remote_machine_ssh._transport = transport
    remote_machine_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    remote_machine_ssh.connect(hostname=host,
                               port=22,
                               username=username,
                               password=password)
    transport = paramiko.Transport((host, 22))
    transport.connect(username=username, password=password)
    return remote_machine_ssh, transport
    # transport.close()


def sn_init_remote_ftp(transport):
    ftp_client = paramiko.SFTPClient.from_transport(transport)  ## ftp client
    return ftp_client


def sn_remote_cmd(remote_ssh, cmd):
    if remote_ssh is None:
        return sn_local_cmd(cmd)
    stdin, stdout, stderr = remote_ssh.exec_command(cmd, get_pty=True)
    lines = stdout.readlines()
    return lines


# A thread designed for initializing working directory.
class sn_init_directory_thread(threading.Thread):

    def __init__(self, file_path, configuration_file_path, remote_ssh):
        threading.Thread.__init__(self)
        self.file_path = file_path
        self.remote_ssh = remote_ssh
        self.configuration_file_path = configuration_file_path

    def run(self):
        # Reset docker environment.
        work = os.path.join(self.configuration_file_path,
                            self.file_path.lstrip("./"))
        subprocess.run(
            f"rm -f {work}/*.txt",
            shell=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
        if os.path.exists(self.file_path + "/mid_files") == False:
            os.system("mkdir " + self.configuration_file_path + "/" +
                      self.file_path)
            os.system("mkdir " + self.configuration_file_path + "/" +
                      self.file_path + "/delay")
            os.system("mkdir " + self.configuration_file_path + "/" +
                      self.file_path + "/mid_files")
        if self.remote_ssh is not None:
            sn_remote_cmd(self.remote_ssh, "mkdir ~/" + self.file_path)
            sn_remote_cmd(self.remote_ssh, "mkdir ~/" + self.file_path + "/delay")


# A thread designed for initializing constellation nodes.
class sn_Node_Init_Thread(threading.Thread):

    def __init__(self, remote_ssh, docker_service_name, node_size,
                 container_id_list, container_global_idx):
        threading.Thread.__init__(self)
        self.remote_ssh = remote_ssh
        self.docker_service_name = docker_service_name
        self.node_size = node_size
        self.container_global_idx = container_global_idx
        self.container_id_list = copy.deepcopy(container_id_list)

    def run(self):

        # Reset docker environment.
        sn_reset_docker_env(self.remote_ssh, self.docker_service_name,
                            self.node_size)
        # Get container list in each machine (must match node_size).
        self.container_id_list = sn_get_container_info(self.remote_ssh)
        if len(self.container_id_list) != self.node_size:
            raise RuntimeError(
                f"Expected {self.node_size} running containers after init, "
                f"got {len(self.container_id_list)}. "
                f"{_docker_container_status_summary(self.remote_ssh)}"
            )
        # Rename all containers with the global idx
        sn_rename_all_container(self.remote_ssh, self.container_id_list,
                                self.container_global_idx)


def sn_get_container_info(remote_machine_ssh, *, running_only: bool = True):
    if remote_machine_ssh is None:
        # Local mode: return sorted container names (deterministic ordering).
        status_filter = " --filter status=running" if running_only else ""
        lines = sn_local_cmd(
            "docker ps --filter name=ovs_container"
            f"{status_filter} --format '{{{{.Names}}}}'")
        names = sorted(
            [l.strip() for l in lines if l.strip()],
            key=lambda n: int(n.rsplit('_', 1)[-1]))
        return names
    #  Read all container information in all_container_info
    all_container_info = sn_remote_cmd(remote_machine_ssh, "docker ps")
    n_container = len(all_container_info) - 1
    container_id_list = []
    for container_idx in range(1, n_container + 1):
        container_id_list.append(all_container_info[container_idx].split()[0])
    return container_id_list


def _docker_container_status_summary(remote_ssh) -> str:
    """One-line diagnostic of ovs_container states (local mode)."""
    if remote_ssh is not None:
        return "(remote mode: check docker ps on the host)"
    lines = sn_local_cmd(
        "docker ps -a --filter name=ovs_container "
        "--format '{{.Status}}'")
    counts: dict[str, int] = {}
    for line in lines:
        state = line.strip().split()[0] if line.strip() else "unknown"
        counts[state] = counts.get(state, 0) + 1
    if not counts:
        return "no ovs_container instances found"
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))


_OVS_CONTAINER_RM_CMD = (
    "docker rm -f $(docker ps -aq --filter name=ovs_container) "
    "2>/dev/null; true"
)


def _count_ovs_containers(remote_ssh) -> int:
    """Count ovs_container instances in any state (including removing)."""
    if remote_ssh is None:
        lines = sn_local_cmd("docker ps -aq --filter name=ovs_container")
    else:
        lines = sn_remote_cmd(
            remote_ssh, "docker ps -aq --filter name=ovs_container")
    return len([line for line in lines if line.strip()])


def sn_purge_stale_ovs_bridge_endpoints(remote_ssh) -> None:
    """Disconnect ovs_container endpoints left on the default bridge."""
    if remote_ssh is not None:
        return
    res = sn_run_local_cmd(
        "docker network inspect bridge -f "
        "\"{{range $k, $v := .Containers}}{{$v.Name}}\\n{{end}}\"")
    for line in (res.stdout or "").splitlines():
        name = line.strip()
        if name.startswith("ovs_container_"):
            sn_run_local_cmd(
                f"docker network disconnect -f bridge {name} "
                "2>/dev/null; true")


def sn_wait_for_containers_gone(
    remote_ssh,
    *,
    timeout: float = 600.0,
    poll: float = 0.5,
) -> None:
    """Block until no ovs_container instances remain (any state)."""
    deadline = time.time() + timeout
    last_count = 0
    while time.time() < deadline:
        last_count = _count_ovs_containers(remote_ssh)
        if last_count == 0:
            return
        time.sleep(poll)

    summary = _docker_container_status_summary(remote_ssh)
    raise RuntimeError(
        f"Timed out after {timeout:.0f}s waiting for ovs_container cleanup "
        f"({last_count} remain). Container states: {summary}."
    )


def sn_remove_all_ovs_containers(
    remote_ssh,
    *,
    wait_timeout: float = 600.0,
) -> None:
    """Force-remove all ovs_container instances and wait until fully gone."""
    if remote_ssh is None:
        sn_local_cmd(_OVS_CONTAINER_RM_CMD)
    else:
        sn_remote_cmd(remote_ssh, _OVS_CONTAINER_RM_CMD)
    try:
        sn_wait_for_containers_gone(remote_ssh, timeout=wait_timeout)
    except RuntimeError:
        if remote_ssh is None:
            sn_purge_stale_ovs_bridge_endpoints(remote_ssh)
            sn_local_cmd(_OVS_CONTAINER_RM_CMD)
        sn_wait_for_containers_gone(remote_ssh, timeout=wait_timeout)
    if remote_ssh is None:
        # Large grids (12x12) can leave bridge endpoints briefly after rm -f.
        time.sleep(2.0)


def sn_ensure_ovs_containers_gone(
    remote_ssh=None,
    *,
    wait_timeout: float = 600.0,
) -> None:
    """Remove leftover ovs_container instances before the next emulation run."""
    remaining = _count_ovs_containers(remote_ssh)
    if remaining == 0:
        return
    print(
        f"Waiting for {remaining} leftover ovs_container(s) to be removed "
        "before starting the next run..."
    )
    sn_remove_all_ovs_containers(remote_ssh, wait_timeout=wait_timeout)


def _docker_run_ovs_container(container_idx: int) -> tuple[int, str]:
    """Create one ovs_container, retrying on stale bridge endpoint conflicts."""
    name = f"ovs_container_{container_idx}"
    cmd = (
        f"docker run -d --name {name} --cap-add ALL --privileged "
        "lwsen/starlab_node:1.0 ping -i 3600 127.0.0.1"
    )
    err = ""
    for attempt in range(1, 4):
        res = sn_run_local_cmd(cmd)
        if res.returncode == 0:
            return 0, ""
        err = (res.stderr or res.stdout or "").strip()
        if "already exists" not in err.lower() or attempt >= 3:
            return res.returncode, err
        sn_run_local_cmd(
            f"docker rm -f {name} 2>/dev/null; "
            f"docker network disconnect -f bridge {name} 2>/dev/null; true")
        time.sleep(1.0 * attempt)
    return 1, err


def sn_wait_for_containers(
    remote_ssh,
    node_size: int,
    *,
    timeout: float = 600.0,
) -> list:
    """
    Block until ``node_size`` ovs_container instances are running (local mode).

    Raises RuntimeError with diagnostics when Docker did not start every node.
    Large constellations (10x10, 12x12) can take minutes on Docker Desktop;
    increase Docker CPU/RAM if this times out.
    """
    deadline = time.time() + timeout
    last_count = 0
    while time.time() < deadline:
        names = sn_get_container_info(remote_ssh, running_only=True)
        last_count = len(names)
        if last_count == node_size:
            return names
        if last_count > node_size:
            raise RuntimeError(
                f"Found {last_count} running ovs_container instances but "
                f"expected {node_size}. Remove orphans with:\n"
                "  docker rm -f $(docker ps -aq --filter name=ovs_container)"
            )
        time.sleep(0.5)

    summary = _docker_container_status_summary(remote_ssh)
    raise RuntimeError(
        f"Timed out after {timeout:.0f}s waiting for {node_size} running "
        f"containers (have {last_count}). Container states: {summary}. "
        "Try: make clean-artifacts && docker rm -f "
        "$(docker ps -aq --filter name=ovs_container); "
        "increase Docker Desktop CPU/RAM for large grids."
    )


def sn_delete_remote_network_bridge(remote_ssh):
    all_br_info = sn_remote_cmd(remote_ssh, "docker network ls")
    for line in all_br_info:
        if "La" in line or "Le" in line or "GS" in line:
            network_name = line.split()[1]
            print('docker network rm ' + network_name)
            sn_remote_cmd(remote_ssh, 'docker network rm ' + network_name)


def sn_reset_docker_env(remote_ssh, docker_service_name, node_size):
    print("Reset docker environment for constellation emulation ...")
    print("Remove legacy containers.")
    if remote_ssh is None:
        sn_remove_all_ovs_containers(remote_ssh)
        print("Remove legacy emulated ISLs.")
        sn_delete_remote_network_bridge(remote_ssh)
        print("Creating new containers...")
        failures: list[tuple[int, str]] = []
        disk_full = False
        for i in range(1, node_size + 1):
            rc, err = _docker_run_ovs_container(i)
            if rc != 0:
                failures.append((i, err))
                print(f"[Docker] ovs_container_{i} failed: {err}")
                if "no space left on device" in err.lower():
                    disk_full = True
            # Avoid overwhelming Docker Desktop when spawning 100+ nodes.
            if i % 25 == 0:
                time.sleep(1.0)
        if disk_full:
            sn_remove_all_ovs_containers(remote_ssh)
            raise RuntimeError(
                "Docker ran out of disk space while creating containers "
                f"(need {node_size}). Free space with:\n"
                "  docker system prune -af\n"
                "  docker volume prune -f\n"
                "Then increase Docker/OrbStack disk image size in settings "
                "and retry."
            )
        try:
            sn_wait_for_containers(remote_ssh, node_size)
        except RuntimeError as exc:
            if failures:
                sample = "; ".join(
                    f"#{idx}: {msg[:120]}" for idx, msg in failures[:3])
                raise RuntimeError(
                    f"{exc} First docker run failures: {sample}"
                ) from exc
            raise
    else:
        print(sn_remote_cmd(remote_ssh, "docker service rm " + docker_service_name))
        print(sn_remote_cmd(remote_ssh, "docker rm -f $(docker ps -a -q)"))
        print("Remove legacy emulated ISLs.")
        sn_delete_remote_network_bridge(remote_ssh)
        print("Creating new containers...")
        sn_remote_cmd(
            remote_ssh, "docker service create --replicas " + str(node_size) +
            " --name " + str(docker_service_name) +
            " --cap-add ALL lwsen/starlab_node:1.0 ping www.baidu.com")


def sn_rename_all_container(remote_ssh, container_id_list, new_idx):
    if remote_ssh is None:
        return  # already named ovs_container_N during docker run
    print("Rename all containers ...")
    new_idx = 1
    for container_id in container_id_list:
        sn_remote_cmd(
            remote_ssh, "docker rename " + str(container_id) +
            " ovs_container_" + str(new_idx))
        new_idx = new_idx + 1


# A thread designed for initializing constellation links.
class sn_Link_Init_Thread(threading.Thread):

    def __init__(self, remote_ssh, remote_ftp, orbit_num, sat_num,
                 constellation_size, fac_num, file_path,
                 configuration_file_path, sat_bandwidth, sat_ground_bandwidth,
                 sat_loss, sat_ground_loss):
        threading.Thread.__init__(self)
        self.remote_ssh = remote_ssh
        self.constellation_size = constellation_size
        self.fac_num = fac_num
        self.orbit_num = orbit_num
        self.sat_num = sat_num
        self.file_path = file_path
        self.configuration_file_path = configuration_file_path
        self.sat_bandwidth = sat_bandwidth
        self.sat_ground_bandwidth = sat_ground_bandwidth
        self.sat_loss = sat_loss
        self.sat_ground_loss = sat_ground_loss
        self.remote_ftp = remote_ftp

    def run(self):
        print('Run in link init thread.')
        self.remote_ftp.put(
            os.path.join(os.getcwd(), "starrynet/sn_orchestrater.py"),
            self.file_path + "/sn_orchestrater.py")
        self.remote_ftp.put(
            self.configuration_file_path + "/" + self.file_path +
            '/delay/1.txt', self.file_path + "/1.txt")
        print('Initializing links ...')
        sn_remote_cmd(
            self.remote_ssh, "python3 " + self.file_path +
            "/sn_orchestrater.py" + " " + str(self.orbit_num) + " " +
            str(self.sat_num) + " " + str(self.constellation_size) + " " +
            str(self.fac_num) + " " + str(self.sat_bandwidth) + " " +
            str(self.sat_loss) + " " + str(self.sat_ground_bandwidth) + " " +
            str(self.sat_ground_loss) + " " + self.file_path + "/1.txt")


# A thread designed for initializing bird routing.
class sn_Routing_Init_Thread(threading.Thread):

    def __init__(self, remote_ssh, remote_ftp, orbit_num, sat_num,
                 constellation_size, fac_num, file_path, sat_bandwidth,
                 sat_ground_bandwidth, sat_loss, sat_ground_loss):
        threading.Thread.__init__(self)
        self.remote_ssh = remote_ssh
        self.constellation_size = constellation_size
        self.fac_num = fac_num
        self.orbit_num = orbit_num
        self.sat_num = sat_num
        self.file_path = file_path
        self.sat_bandwidth = sat_bandwidth
        self.sat_ground_bandwidth = sat_ground_bandwidth
        self.sat_loss = sat_loss
        self.sat_ground_loss = sat_ground_loss
        self.remote_ftp = remote_ftp

    def run(self):
        print(
            "Copy bird configuration file to each container and run routing process."
        )
        self.remote_ftp.put(
            os.path.join(os.getcwd(), "starrynet/sn_orchestrater.py"),
            self.file_path + "/sn_orchestrater.py")
        print('Initializing routing ...')
        sn_remote_cmd(
            self.remote_ssh, "python3 " + self.file_path +
            "/sn_orchestrater.py" + " " + str(self.constellation_size) + " " +
            str(self.fac_num) + " " + self.file_path)
        print("Routing initialized!")


# A thread designed for emulation.
class sn_Emulation_Start_Thread(threading.Thread):

    def __init__(self, remote_ssh, remote_ftp, sat_loss, sat_ground_bw,
                 sat_ground_loss, container_id_list, file_path,
                 configuration_file_path, update_interval, constellation_size,
                 ping_src, ping_des, ping_time, sr_src, sr_des, sr_target,
                 sr_time, damage_ratio, damage_time, damage_list,
                 recovery_time, route_src, route_time, duration,
                 utility_checking_time, perf_src, perf_des, perf_time,
                 sdn_sn=None, ospf_sn=None):
        threading.Thread.__init__(self)
        self.sdn_sn = sdn_sn
        self.ospf_sn = ospf_sn
        self.remote_ssh = remote_ssh
        self.remote_ftp = remote_ftp
        self.sat_loss = sat_loss
        self.sat_ground_bw = sat_ground_bw
        self.sat_ground_loss = sat_ground_loss
        self.container_id_list = copy.deepcopy(container_id_list)
        self.file_path = file_path
        self.configuration_file_path = configuration_file_path
        self.update_interval = update_interval
        self.constellation_size = constellation_size
        self.ping_src = ping_src
        self.ping_des = ping_des
        self.ping_time = ping_time
        self.perf_src = perf_src
        self.perf_des = perf_des
        self.perf_time = perf_time
        self.sr_src = sr_src
        self.sr_des = sr_des
        self.sr_target = sr_target
        self.sr_time = sr_time
        self.damage_ratio = damage_ratio
        self.damage_time = damage_time
        self.damage_list = damage_list
        self.recovery_time = recovery_time
        self.route_src = route_src
        self.route_time = route_time
        self.duration = duration
        self.utility_checking_time = utility_checking_time
        if self.container_id_list == []:
            self.container_id_list = sn_get_container_info(self.remote_ssh)

    def _sdn_delay_update(self, timeptr):
        if self.sdn_sn is None:
            return
        from starrynet.sn_sdn_adapter import sdn_after_delay_update
        sdn_after_delay_update(self.sdn_sn, timeptr)

    def _sdn_damage_recovery(self, timeptr):
        if self.sdn_sn is None:
            return
        from starrynet.sn_sdn_adapter import sdn_after_damage_or_recovery
        sdn_after_damage_or_recovery(self.sdn_sn, timeptr)

    def _sdn_proactive_handover(self, time_index):
        if self.sdn_sn is None:
            return
        from starrynet.sn_sdn_adapter import sdn_proactive_handover
        sdn_proactive_handover(self.sdn_sn, int(time_index))

    def _sdn_topology_change(self, time_index):
        if self.sdn_sn is None:
            return
        from starrynet.sn_sdn_adapter import sdn_after_topology_change
        sdn_after_topology_change(self.sdn_sn, int(time_index))

    def _ospf_delay_update(self, timeptr):
        if self.ospf_sn is None:
            return
        from starrynet.sn_ospf_adapter import ospf_after_delay_update
        ospf_after_delay_update(self.ospf_sn, timeptr)

    def _ospf_damage_recovery(self, timeptr):
        if self.ospf_sn is None:
            return
        from starrynet.sn_ospf_adapter import ospf_after_damage_or_recovery
        ospf_after_damage_or_recovery(self.ospf_sn, timeptr)

    def _ospf_topology_change(self, time_index):
        if self.ospf_sn is None:
            return
        from starrynet.sn_ospf_adapter import ospf_after_topology_change
        ospf_after_topology_change(self.ospf_sn, int(time_index))

    def run(self):
        ping_threads = []
        perf_threads = []
        timeptr = 2  # current emulating time
        topo_change_file_path = self.configuration_file_path + "/" + self.file_path + '/Topo_leo_change.txt'
        fi = open(topo_change_file_path, 'r')
        line = fi.readline()
        while line:  # starting reading change information and emulating
            words = line.split()
            if words[0] == 'time':
                print('Emulation in No.' + str(timeptr) + ' second.')
                # the time when the new change occurrs
                current_time = str(int(words[1][:-1]))
                while int(current_time) > timeptr:
                    start_time = time.time()
                    if timeptr in self.utility_checking_time:
                        sn_check_utility(
                            timeptr, self.remote_ssh,
                            self.configuration_file_path + "/" +
                            self.file_path)
                    if timeptr % self.update_interval == 0:
                        # updating link delays after link changes
                        sn_update_delay(self.file_path,
                                        self.configuration_file_path, timeptr,
                                        self.constellation_size,
                                        self.remote_ssh, self.remote_ftp)
                        self._sdn_delay_update(timeptr)
                        self._ospf_delay_update(timeptr)
                    if timeptr in self.damage_time:
                        sn_damage(
                            self.damage_ratio[self.damage_time.index(timeptr)],
                            self.damage_list, self.constellation_size,
                            self.remote_ssh, self.remote_ftp, self.file_path,
                            self.configuration_file_path)
                        self._sdn_damage_recovery(timeptr)
                        self._ospf_damage_recovery(timeptr)
                    if timeptr in self.recovery_time:
                        sn_recover(self.damage_list, self.sat_loss,
                                   self.remote_ssh, self.remote_ftp,
                                   self.file_path,
                                   self.configuration_file_path)
                        self._sdn_damage_recovery(timeptr)
                        self._ospf_damage_recovery(timeptr)
                    if timeptr in self.sr_time:
                        index = [
                            i for i, val in enumerate(self.sr_time)
                            if val == timeptr
                        ]
                        for index_num in index:
                            sn_sr(self.sr_src[index_num],
                                  self.sr_des[index_num],
                                  self.sr_target[index_num],
                                  self.container_id_list, self.remote_ssh)
                    if timeptr in self.ping_time:
                        if timeptr in self.ping_time:
                            index = [
                                i for i, val in enumerate(self.ping_time)
                                if val == timeptr
                            ]
                            for index_num in index:
                                ping_thread = threading.Thread(
                                    target=sn_ping,
                                    args=(self.ping_src[index_num],
                                          self.ping_des[index_num],
                                          self.ping_time[index_num],
                                          self.constellation_size,
                                          self.container_id_list,
                                          self.file_path,
                                          self.configuration_file_path,
                                          self.remote_ssh))
                                ping_thread.start()
                                ping_threads.append(ping_thread)
                    if timeptr in self.perf_time:
                        if timeptr in self.perf_time:
                            index = [
                                i for i, val in enumerate(self.perf_time)
                                if val == timeptr
                            ]
                            for index_num in index:
                                perf_thread = threading.Thread(
                                    target=sn_perf,
                                    args=(self.perf_src[index_num],
                                          self.perf_des[index_num],
                                          self.perf_time[index_num],
                                          self.constellation_size,
                                          self.container_id_list,
                                          self.file_path,
                                          self.configuration_file_path,
                                          self.remote_ssh))
                                perf_thread.start()
                                perf_threads.append(perf_thread)
                    if timeptr in self.route_time:
                        index = [
                            i for i, val in enumerate(self.route_time)
                            if val == timeptr
                        ]
                        for index_num in index:
                            sn_route(self.route_src[index_num],
                                     self.route_time[index_num],
                                     self.file_path,
                                     self.configuration_file_path,
                                     self.container_id_list, self.remote_ssh)
                    timeptr += 1
                    end_time = time.time()
                    passed_time = (
                        end_time -
                        start_time) if (end_time - start_time) < 1 else 1
                    sleep(1 - passed_time)
                    if timeptr >= self.duration:
                        return
                    print('Emulation in No.' + str(timeptr) + ' second.')
                print("A change in time " + current_time + ':')
                line = fi.readline()
                words = line.split()
                line = fi.readline()
                line = fi.readline()
                words = line.split()
                while words[0] != 'del:':  # addlink
                    word = words[0].split('-')
                    s = int(word[0])
                    f = int(word[1])
                    if s > f:
                        tmp = s
                        s = f
                        f = tmp
                    print("add link", s, f)
                    current_topo_path = self.configuration_file_path + "/" + self.file_path + '/delay/' + str(
                        current_time) + '.txt'
                    matrix = sn_get_param(current_topo_path)
                    sn_establish_new_GSL(self.container_id_list, matrix,
                                         self.constellation_size,
                                         self.sat_ground_bw,
                                         self.sat_ground_loss, s, f,
                                         self.remote_ssh)
                    line = fi.readline()
                    words = line.split()
                # Proactive SDN: push post-handover routes while old GSL still up.
                self._sdn_proactive_handover(current_time)
                line = fi.readline()
                words = line.split()
                if len(words) == 0:
                    return
                while words[0] != 'time':  # delete link
                    word = words[0].split('-')
                    s = int(word[0])
                    f = int(word[1])
                    if s > f:
                        tmp = s
                        s = f
                        f = tmp
                    print("del link " + str(s) + "-" + str(f) + "\n")
                    sn_del_link(s, f, self.container_id_list, self.remote_ssh)
                    line = fi.readline()
                    words = line.split()
                    if len(words) == 0:
                        return
                self._sdn_topology_change(current_time)
                self._ospf_topology_change(current_time)
                if timeptr in self.utility_checking_time:
                    sn_check_utility(
                        timeptr, self.remote_ssh,
                        self.configuration_file_path + "/" + self.file_path)
                if timeptr % self.update_interval == 0:
                    # updating link delays after link changes
                    sn_update_delay(self.file_path,
                                    self.configuration_file_path, timeptr,
                                    self.constellation_size, self.remote_ssh,
                                    self.remote_ftp)
                    self._sdn_delay_update(timeptr)
                    self._ospf_delay_update(timeptr)
                if timeptr in self.damage_time:
                    sn_damage(
                        self.damage_ratio[self.damage_time.index(timeptr)],
                        self.damage_list, self.constellation_size,
                        self.remote_ssh, self.remote_ftp, self.file_path,
                        self.configuration_file_path)
                    self._sdn_damage_recovery(timeptr)
                    self._ospf_damage_recovery(timeptr)
                if timeptr in self.recovery_time:
                    sn_recover(self.damage_list, self.sat_loss,
                               self.remote_ssh, self.remote_ftp,
                               self.file_path, self.configuration_file_path)
                    self._sdn_damage_recovery(timeptr)
                    self._ospf_damage_recovery(timeptr)
                if timeptr in self.sr_time:
                    index = [
                        i for i, val in enumerate(self.sr_time)
                        if val == timeptr
                    ]
                    for index_num in index:
                        sn_sr(self.sr_src[index_num], self.sr_des[index_num],
                              self.sr_target[index_num],
                              self.container_id_list, self.remote_ssh)
                if timeptr in self.ping_time:
                    if timeptr in self.ping_time:
                        index = [
                            i for i, val in enumerate(self.ping_time)
                            if val == timeptr
                        ]
                        for index_num in index:
                            ping_thread = threading.Thread(
                                target=sn_ping,
                                args=(self.ping_src[index_num],
                                      self.ping_des[index_num],
                                      self.ping_time[index_num],
                                      self.constellation_size,
                                      self.container_id_list, self.file_path,
                                      self.configuration_file_path,
                                      self.remote_ssh))
                            ping_thread.start()
                            ping_threads.append(ping_thread)
                if timeptr in self.perf_time:
                    if timeptr in self.perf_time:
                        index = [
                            i for i, val in enumerate(self.perf_time)
                            if val == timeptr
                        ]
                        for index_num in index:
                            perf_thread = threading.Thread(
                                target=sn_perf,
                                args=(self.perf_src[index_num],
                                      self.perf_des[index_num],
                                      self.perf_time[index_num],
                                      self.constellation_size,
                                      self.container_id_list, self.file_path,
                                      self.configuration_file_path,
                                      self.remote_ssh))
                            perf_thread.start()
                            perf_threads.append(perf_thread)
                if timeptr in self.route_time:
                    index = [
                        i for i, val in enumerate(self.route_time)
                        if val == timeptr
                    ]
                    for index_num in index:
                        sn_route(self.route_src[index_num],
                                 self.route_time[index_num], self.file_path,
                                 self.configuration_file_path,
                                 self.container_id_list, self.remote_ssh)
                timeptr += 1  # current emulating time
                if timeptr >= self.duration:
                    return
        fi.close()
        for ping_thread in ping_threads:
            ping_thread.join()
        for perf_thread in perf_threads:
            perf_thread.join()


def sn_check_utility(time_index, remote_ssh, file_path):
    if remote_ssh is None:
        result = sn_local_cmd("vm_stat")
    else:
        result = sn_remote_cmd(remote_ssh, "vmstat")
    f = open(file_path + "/utility-info" + "_" + str(time_index) + ".txt", "w")
    f.writelines(result)
    f.close()


def sn_update_delay(file_path, configuration_file_path, timeptr,
                    constellation_size, remote_ssh,
                    remote_ftp):  # updating delays
    remote_ftp.put(os.path.join(os.getcwd(), "starrynet/sn_orchestrater.py"),
                   file_path + "/sn_orchestrater.py")
    remote_ftp.put(
        configuration_file_path + "/" + file_path + '/delay/' + str(timeptr) +
        '.txt', file_path + '/' + str(timeptr) + '.txt')
    sn_remote_cmd(
        remote_ssh,
        "python3 " + file_path + "/sn_orchestrater.py " + file_path + '/' +
        str(timeptr) + '.txt ' + str(constellation_size) + " update")
    print("Delay updating done.\n")


def sn_damage(ratio, damage_list, constellation_size, remote_ssh, remote_ftp,
              file_path, configuration_file_path):
    print("Randomly setting damaged links...\n")
    random_list = []
    cumulated_damage_list = damage_list
    while len(random_list) < (int(constellation_size * ratio)):
        target = int(random.uniform(0, constellation_size - 1))
        random_list.append(target)
        cumulated_damage_list.append(target)
    numpy.savetxt(
        configuration_file_path + "/" + file_path +
        '/mid_files/damage_list.txt', random_list)
    remote_ftp.put(os.path.join(os.getcwd(), "starrynet/sn_orchestrater.py"),
                   file_path + "/sn_orchestrater.py")
    remote_ftp.put(
        configuration_file_path + "/" + file_path +
        '/mid_files/damage_list.txt', file_path + "/damage_list.txt")
    sn_remote_cmd(remote_ssh,
                  "python3 " + file_path + "/sn_orchestrater.py " + file_path)
    print("Damage done.\n")


def sn_recover(damage_list, sat_loss, remote_ssh, remote_ftp, file_path,
               configuration_file_path):
    print("Recovering damaged links...\n")
    cumulated_damage_list = damage_list
    numpy.savetxt(
        configuration_file_path + "/" + file_path +
        '/mid_files/damage_list.txt', cumulated_damage_list)
    remote_ftp.put(os.path.join(os.getcwd(), "starrynet/sn_orchestrater.py"),
                   file_path + "/sn_orchestrater.py")
    remote_ftp.put(
        configuration_file_path + "/" + file_path +
        '/mid_files/damage_list.txt', file_path + "/damage_list.txt")
    sn_remote_cmd(
        remote_ssh, "python3 " + file_path + "/sn_orchestrater.py " +
        file_path + " " + str(sat_loss))
    cumulated_damage_list.clear()
    print("Link recover done.\n")


def sn_sr(src, des, target, container_id_list, remote_ssh):
    ifconfig_output = sn_remote_cmd(
        remote_ssh, "docker exec -i " + str(container_id_list[des - 1]) +
        " ifconfig | sed 's/[ \t].*//;/^\(eth0\|\)\(lo\|\)$/d'")
    des_IP = sn_remote_cmd(
        remote_ssh,
        "docker exec -i " + str(container_id_list[des - 1]) + " ifconfig " +
        ifconfig_output[0][:-1] + "|awk -F '[ :]+' 'NR==2{print $4}'")
    target_IP = sn_remote_cmd(
        remote_ssh, "docker exec -i " + str(container_id_list[target - 1]) +
        " ifconfig B" + str(target) + "-eth" + str(src) +
        "|awk -F '[ :]+' 'NR==2{print $4}'")
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[src - 1]) +
        " ip route del " + str(des_IP[0][:-3]) + "0/24")
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[src - 1]) +
        " ip route add " + str(des_IP[0][:-3]) + "0/24 dev B%d-eth%d via " %
        (src, target) + target_IP[0])
    print("docker exec -d " + str(container_id_list[src - 1]) +
          " ip route add " + str(des_IP[0][:-3]) + "0/24 dev B%d-eth%d via " %
          (src, target) + target_IP[0])


def _resolve_node_ipv4(des, constellation_size, container_id_list, remote_ssh):
    """Resolve destination IPv4 (works with eth* or B*-eth* interface names)."""
    cid = str(container_id_list[des - 1])
    if des > constellation_size:
        target = f"9.{des}.{des}.10"
        lines = sn_remote_cmd(
            remote_ssh,
            "docker exec -i " + cid +
            " ip -4 -o addr show scope global | awk '{print $4}'",
        )
        for line in lines:
            ip = line.strip().split("/")[0]
            if ip == target:
                return target
        return target
    lines = sn_remote_cmd(
        remote_ssh,
        "docker exec -i " + cid +
        " ip -4 -o addr show scope global | awk '{print $4}' | grep -v '^192\\.168\\.' | head -1",
    )
    return lines[0].strip().split("/")[0]


def sn_ping(src, des, time_index, constellation_size, container_id_list,
            file_path, configuration_file_path, remote_ssh):
    dest_ip = _resolve_node_ipv4(des, constellation_size, container_id_list,
                                 remote_ssh)
    # 2>&1: when the source has no route the kernel makes `ping` print
    # "connect: Network is unreachable" on stderr and exit with no summary.
    # sn_local_cmd only keeps stdout, so without this the file would be empty
    # and a real outage (e.g. OSPF mid-reconvergence) would look like missing
    # data instead of 100% loss. Merging stderr preserves that evidence.
    ping_result = sn_remote_cmd(
        remote_ssh, "docker exec -i " + str(container_id_list[src - 1]) +
        " ping " + dest_ip + " -c 4 -i 0.01 2>&1")
    f = open(
        configuration_file_path + "/" + file_path + "/ping-" + str(src) + "-" +
        str(des) + "_" + str(time_index) + ".txt", "w")
    f.writelines(ping_result)
    f.close()


def sn_perf(src, des, time_index, constellation_size, container_id_list,
            file_path, configuration_file_path, remote_ssh):
    dest_ip = _resolve_node_ipv4(des, constellation_size, container_id_list,
                                 remote_ssh)

    sn_remote_cmd(
        remote_ssh,
        "docker exec -id " + str(container_id_list[des - 1]) + " iperf3 -s ")
    print("iperf server success")
    perf_result = sn_remote_cmd(
        remote_ssh, "docker exec -i " + str(container_id_list[src - 1]) +
        " iperf3 -c " + dest_ip + " -t 5 ")
    print("iperf client success")
    f = open(
        configuration_file_path + "/" + file_path + "/perf-" + str(src) + "-" +
        str(des) + "_" + str(time_index) + ".txt", "w")
    f.writelines(perf_result)
    f.close()


def sn_route(src, time_index, file_path, configuration_file_path,
             container_id_list, remote_ssh):
    route_result = sn_remote_cmd(
        remote_ssh,
        "docker exec -i " + str(container_id_list[src - 1]) + " route ")
    f = open(
        configuration_file_path + "/" + file_path + "/route-" + str(src) +
        "_" + str(time_index) + ".txt", "w")
    f.writelines(route_result)
    f.close()


def sn_establish_new_GSL(container_id_list, matrix, constellation_size, bw,
                         loss, sat_index, GS_index, remote_ssh):
    i = sat_index
    j = GS_index
    # IP address  (there is a link between i and j)
    delay = str(matrix[i - 1][j - 1])
    address_16_23 = (j - constellation_size) & 0xff
    address_8_15 = i & 0xff
    GSL_name = "GSL_" + str(i) + "-" + str(j)
    # Create internal network in docker.
    sn_remote_cmd(
        remote_ssh, 'docker network create ' + GSL_name + " --subnet 9." +
        str(address_16_23) + "." + str(address_8_15) + ".0/24")
    print('[Create GSL:]' + 'docker network create ' + GSL_name +
          " --subnet 9." + str(address_16_23) + "." + str(address_8_15) +
          ".0/24")
    sn_remote_cmd(
        remote_ssh, 'docker network connect ' + GSL_name + " " +
        str(container_id_list[i - 1]) + " --ip 9." + str(address_16_23) + "." +
        str(address_8_15) + ".50")
    ifconfig_output = sn_remote_cmd(
        remote_ssh, "docker exec -i " + str(container_id_list[i - 1]) +
        " ip addr | grep -B 2 9." + str(address_16_23) + "." +
        str(address_8_15) +
        ".50 | head -n 1 | awk -F: '{ print $2 }' | tr -d [:blank:]")
    target_interface = str(ifconfig_output[0]).split("@")[0]
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[i - 1]) +
        " ip link set dev " + target_interface + " down")
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[i - 1]) +
        " ip link set dev " + target_interface + " name " + "B" +
        str(i - 1 + 1) + "-eth" + str(j))
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[i - 1]) +
        " ip link set dev B" + str(i - 1 + 1) + "-eth" + str(j) + " up")
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[i - 1]) +
        " tc qdisc add dev B" + str(i - 1 + 1) + "-eth" + str(j) +
        " root netem delay " + str(delay) + "ms")
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[i - 1]) +
        " tc qdisc add dev B" + str(i - 1 + 1) + "-eth" + str(j) +
        " root netem loss " + str(loss) + "%")
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[i - 1]) +
        " tc qdisc add dev B" + str(i - 1 + 1) + "-eth" + str(j) +
        " root netem rate " + str(bw) + "Gbps")
    print('[Add current node:]' + 'docker network connect ' + GSL_name + " " +
          str(container_id_list[i - 1]) + " --ip 9." + str(address_16_23) +
          "." + str(address_8_15) + ".50")
    sn_remote_cmd(
        remote_ssh, 'docker network connect ' + GSL_name + " " +
        str(container_id_list[j - 1]) + " --ip 9." + str(address_16_23) + "." +
        str(address_8_15) + ".60")
    ifconfig_output = sn_remote_cmd(
        remote_ssh, "docker exec -i " + str(container_id_list[j - 1]) +
        " ip addr | grep -B 2 9." + str(address_16_23) + "." +
        str(address_8_15) +
        ".60 | head -n 1 | awk -F: '{ print $2 }' | tr -d [:blank:]")
    target_interface = str(ifconfig_output[0]).split("@")[0]
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[j - 1]) +
        " ip link set dev " + target_interface + " down")
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[j - 1]) +
        " ip link set dev " + target_interface + " name " + "B" + str(j) +
        "-eth" + str(i - 1 + 1))
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[j - 1]) +
        " ip link set dev B" + str(j) + "-eth" + str(i - 1 + 1) + " up")
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[j - 1]) +
        " tc qdisc add dev B" + str(j) + "-eth" + str(i - 1 + 1) +
        " root netem delay " + str(delay) + "ms")
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[j - 1]) +
        " tc qdisc add dev B" + str(j) + "-eth" + str(i - 1 + 1) +
        " root netem loss " + str(loss) + "%")
    sn_remote_cmd(
        remote_ssh, "docker exec -d " + str(container_id_list[j - 1]) +
        " tc qdisc add dev B" + str(j) + "-eth" + str(i - 1 + 1) +
        " root netem rate " + str(bw) + "Gbps")
    print('[Add right node:]' + 'docker network connect ' + GSL_name + " " +
          str(container_id_list[j - 1]) + " --ip 9." + str(address_16_23) +
          "." + str(address_8_15) + ".60")


def sn_del_link(first_index, second_index, container_id_list, remote_ssh):
    sn_remote_cmd(
        remote_ssh, "docker exec -d " +
        str(container_id_list[second_index - 1]) + " ip link set dev B" +
        str(second_index) + "-eth" + str(first_index) + " down")
    sn_remote_cmd(
        remote_ssh, "docker exec -d " +
        str(container_id_list[first_index - 1]) + " ip link set dev B" +
        str(first_index) + "-eth" + str(second_index) + " down")
    GSL_name = "GSL_" + str(first_index) + "-" + str(second_index)
    sn_remote_cmd(
        remote_ssh, 'docker network disconnect ' + GSL_name + " " +
        str(container_id_list[first_index - 1]))
    sn_remote_cmd(
        remote_ssh, 'docker network disconnect ' + GSL_name + " " +
        str(container_id_list[second_index - 1]))
    sn_remote_cmd(remote_ssh, 'docker network rm ' + GSL_name)


# A thread designed for stopping the emulation.
class sn_Emulation_Stop_Thread(threading.Thread):

    def __init__(self, remote_ssh, remote_ftp, file_path):
        threading.Thread.__init__(self)
        self.remote_ssh = remote_ssh
        self.remote_ftp = remote_ftp
        self.file_path = file_path

    def run(self):
        print("Deleting all native bridges and containers...")
        if self.remote_ssh is None:
            sn_remove_all_ovs_containers(None)
            sn_delete_remote_network_bridge(None)
        else:
            self.remote_ftp.put(
                os.path.join(os.getcwd(), "starrynet/sn_orchestrater.py"),
                self.file_path + "/sn_orchestrater.py")
            sn_remote_cmd(self.remote_ssh,
                          "python3 " + self.file_path + "/sn_orchestrater.py")
