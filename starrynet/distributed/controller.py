import json

from starrynet.distributed.worker import Worker


class Controller:
    """Central controller for distributed OrbitGraph execution."""

    def __init__(self, config_path):
        self.workers = {}
        self.node_to_worker = {}
        self.placement = {}

        self._load_config(config_path)

    def _load_config(self, config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        for entry in config["workers"]:
            worker = Worker(
                name=entry["name"],
                docker_context=entry["docker_context"],
                data_ip=entry["data_ip"],
            )

            self.workers[worker.name] = worker

        self.placement = config.get("placement", {})

    def health_check(self):
        return {
            name: worker.health_check()
            for name, worker in self.workers.items()
        }

    def place_nodes(self, node_ids):
        strategy = self.placement.get("strategy")

        if strategy != "single_worker":
            raise NotImplementedError(
                f"Unsupported placement strategy: {strategy}"
            )

        worker_name = self.placement.get("default_worker")

        if worker_name not in self.workers:
            raise RuntimeError(
                f"Unknown worker: {worker_name}"
            )

        for node_id in node_ids:
            self.node_to_worker[node_id] = worker_name

    def worker_for_node(self, node_id):
        worker_name = self.node_to_worker.get(node_id)

        if worker_name is None:
            raise RuntimeError(
                f"Node {node_id} has not been placed"
            )

        return self.workers[worker_name]

    def create_node(self, node_id):
        worker = self.worker_for_node(node_id)
        return worker.create_node(node_id)

    def remove_node(self, node_id):
        worker = self.worker_for_node(node_id)
        return worker.remove_node(node_id)

    def exec_node(self, node_id, *command, check=True):
        worker = self.worker_for_node(node_id)

        return worker.exec_node(
            node_id,
            *command,
            check=check,
        )