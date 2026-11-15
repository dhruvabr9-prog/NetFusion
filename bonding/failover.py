import time

from bonding.network_paths import check_all_network_paths


PREFERRED_TYPES = ["Wi-Fi", "USB Tethering", "Ethernet"]


def choose_active_path(paths):
    """Choose the highest-priority path that is online."""

    online_paths = [
        path for path in paths
        if path.get("connectivity", {}).get("connected", False)
    ]

    for preferred_type in PREFERRED_TYPES:
        for path in online_paths:
            if path.get("type") == preferred_type:
                return path

    return None


def monitor_failover(interval=10):
    """Continuously monitor available network paths."""

    active_source_ip = None

    print("=" * 55)
    print("          NETFUSION FAILOVER MANAGER")
    print("=" * 55)
    print("Preferred connection: Wi-Fi")
    print("Backup connection: USB tethering")
    print("Monitoring interval: {} seconds".format(interval))
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            result = check_all_network_paths()
            paths = result["paths"]

            selected = choose_active_path(paths)

            print("-" * 55)

            for path in paths:
                connectivity = path["connectivity"]
                is_online = connectivity["connected"]

                print(
                    "{} | {} | {} | {}".format(
                        path["interface"],
                        path["source_ip"],
                        "ONLINE" if is_online else "OFFLINE",
                        (
                            str(connectivity["latency_ms"]) + " ms"
                            if connectivity["latency_ms"] is not None
                            else "No response"
                        ),
                    )
                )

            if selected is None:
                if active_source_ip is not None:
                    print("\nFAILOVER: No working connection available.")

                active_source_ip = None
                print("ACTIVE PATH: NONE")

            else:
                new_source_ip = selected["source_ip"]

                if new_source_ip != active_source_ip:
                    if active_source_ip is None:
                        print("\nINITIAL PATH SELECTION")
                    else:
                        print("\nNETWORK CHANGE DETECTED")

                    print(
                        "Switching active path to: {} ({})".format(
                            selected["interface"],
                            new_source_ip,
                        )
                    )

                    active_source_ip = new_source_ip

                print(
                    "ACTIVE PATH: {} ({})".format(
                        selected["interface"],
                        selected["source_ip"],
                    )
                )

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nNetFusion failover monitoring stopped.")


if __name__ == "__main__":
    monitor_failover()