from pathlib import Path

from starrynet.distributed.controller import Controller


CONFIG = (
    Path(__file__).parent
    / "starrynet"
    / "distributed"
    / "workers.json"
)


def main():
    controller = Controller(CONFIG)

    print("=== Workers ===")
    for name, status in controller.health_check().items():
        print(f"{name}: {status}")

    print("\n=== Placement ===")
    controller.place_nodes([1, 2])

    for node_id in [1, 2]:
        print(
            f"node {node_id} -> "
            f"{controller.worker_for_node(node_id).name}"
        )

    created = []

    try:
        print("\n=== Creating nodes ===")

        for node_id in [1, 2]:
            controller.create_node(node_id)
            created.append(node_id)

        print("\n=== Executing inside nodes ===")

        for node_id in [1, 2]:
            result = controller.exec_node(
                node_id,
                "hostname",
            )

            print(
                f"node {node_id}: "
                f"{result.stdout.strip()}"
            )

        print("\nDISTRIBUTED NODE TEST: PASS")

    finally:
        print("\n=== Cleanup ===")

        for node_id in reversed(created):
            controller.remove_node(node_id)


if __name__ == "__main__":
    main()