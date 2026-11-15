import threading
import time

from bonding.network_paths import check_all_network_paths


class NetworkScheduler:
    """
    Prototype scheduler for assigning new flows to network paths.

    This selects a source IP for each new flow. It does not yet implement
    a tunnel, split a single connection across paths, or redirect Windows
    system traffic.
    """

    def __init__(self, refresh_interval=10):
        self.refresh_interval = max(1, refresh_interval)
        self._lock = threading.Lock()
        self._paths = []
        self._last_refresh = 0.0
        self._round_robin_counter = 0
        self._flow_assignments = {}

    def refresh_paths(self, force=False):
        now = time.monotonic()

        with self._lock:
            refresh_needed = (
                force
                or not self._paths
                or now - self._last_refresh >= self.refresh_interval
            )

        if not refresh_needed:
            return self.get_paths()

        snapshot = check_all_network_paths()
        new_paths = snapshot.get("paths", [])

        with self._lock:
            self._paths = new_paths
            self._last_refresh = time.monotonic()

        return self.get_paths()

    def get_paths(self):
        with self._lock:
            return [dict(path) for path in self._paths]

    def get_online_paths(self):
        return [
            path
            for path in self.get_paths()
            if path.get("is_up")
            and path.get("connectivity", {}).get("connected", False)
        ]

    def select_path(self, strategy="round_robin"):
        paths = self.get_online_paths()

        if not paths:
            return {
                "selected": False,
                "error": "No network paths passed connectivity checks.",
            }

        if strategy == "latency":
            def latency_key(path):
                latency = path.get("connectivity", {}).get("latency_ms")
                return float("inf") if latency is None else latency

            selected = min(paths, key=latency_key)

        elif strategy == "round_robin":
            with self._lock:
                selected = paths[
                    self._round_robin_counter % len(paths)
                ]
                self._round_robin_counter += 1

        else:
            return {
                "selected": False,
                "error": f"Unsupported strategy: {strategy}",
            }

        return {
            "selected": True,
            "strategy": strategy,
            "interface": selected.get("interface"),
            "type": selected.get("type"),
            "source_ip": selected.get("source_ip"),
            "latency_ms": selected.get("connectivity", {}).get(
                "latency_ms"
            ),
            "note": (
                "This assigns a source address to a new flow. "
                "Actual multi-path tunnelling is not implemented yet."
            ),
        }

    def assign_flow(self, flow_id, strategy="round_robin"):
        """
        Assign a logical flow identifier to a selected path.

        Repeated calls with the same flow_id return its existing assignment.
        This is a scheduler demonstration, not packet routing.
        """
        if not isinstance(flow_id, str) or not flow_id.strip():
            return {
                "assigned": False,
                "error": "flow_id must be a non-empty string.",
            }

        with self._lock:
            existing = self._flow_assignments.get(flow_id)

        if existing is not None:
            return {
                "assigned": True,
                "flow_id": flow_id,
                "assignment": existing,
                "reused": True,
            }

        selection = self.select_path(strategy)

        if not selection.get("selected"):
            return {
                "assigned": False,
                "flow_id": flow_id,
                "error": selection.get("error"),
            }

        assignment = {
            "interface": selection["interface"],
            "source_ip": selection["source_ip"],
            "strategy": strategy,
            "assigned_at": time.time(),
        }

        with self._lock:
            # If another caller assigned this flow concurrently, preserve
            # the first assignment.
            assignment = self._flow_assignments.setdefault(
                flow_id, assignment
            )

        return {
            "assigned": True,
            "flow_id": flow_id,
            "assignment": assignment,
            "reused": False,
        }

    def get_assignments(self):
        with self._lock:
            return {
                flow_id: dict(assignment)
                for flow_id, assignment in self._flow_assignments.items()
            }


if __name__ == "__main__":
    import json

    scheduler = NetworkScheduler()
    scheduler.refresh_paths(force=True)

    print("AVAILABLE PATHS")
    print(json.dumps(scheduler.get_paths(), indent=2))

    print("\nFLOW ASSIGNMENTS")
    print(json.dumps(scheduler.assign_flow("download-1"), indent=2))
    print(json.dumps(scheduler.assign_flow("download-2"), indent=2))

    print("\nREUSING AN EXISTING FLOW")
    print(json.dumps(scheduler.assign_flow("download-1"), indent=2))

    print("\nALL ASSIGNMENTS")
    print(json.dumps(scheduler.get_assignments(), indent=2))