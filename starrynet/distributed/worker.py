import subprocess


class Worker:
    """Physical machine capable of hosting emulated OrbitGraph nodes."""

    def __init__(self, name, docker_context, data_ip):
        self.name = name
        self.docker_context = docker_context
        self.data_ip = data_ip

    def __repr__(self):
        return (
            f"Worker(name={self.name!r}, "
            f"docker_context={self.docker_context!r}, "
            f"data_ip={self.data_ip!r})"
        )

    def docker(self, *args, check=True):
        cmd = [
            "docker",
            "--context",
            self.docker_context,
            *map(str, args),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )

        if check and result.returncode != 0:
            raise RuntimeError(
                f"Docker command failed on worker {self.name}\n"
                f"command: {' '.join(cmd)}\n"
                f"stdout: {result.stdout.strip()}\n"
                f"stderr: {result.stderr.strip()}"
            )

        return result

    def health_check(self):
        result = self.docker(
            "info",
            "--format",
            "host={{.Name}} swarm={{.Swarm.LocalNodeState}}",
        )

        return result.stdout.strip()

    @staticmethod
    def container_name(node_id):
        return f"ovs_container_{node_id}"

    def create_node(self, node_id):
        name = self.container_name(node_id)

        print(f"[{self.name}] creating {name}")

        return self.docker(
            "run",
            "-d",
            "--name", name,
            "--privileged",
            "--cap-add", "ALL",
            "--label", "orbitgraph.managed=true",
            "--label", f"orbitgraph.node_id={node_id}",
            "lwsen/starlab_node:1.0",
            "ping", "-i", "3600", "127.0.0.1",
        )

    def remove_node(self, node_id):
        name = self.container_name(node_id)

        print(f"[{self.name}] removing {name}")

        return self.docker(
            "rm",
            "-f",
            name,
            check=False,
        )

    def exec_node(self, node_id, *command, check=True):
        name = self.container_name(node_id)

        return self.docker(
            "exec",
            name,
            *command,
            check=check,
        )