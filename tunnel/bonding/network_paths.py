import json
import socket
import time

import psutil


def classify_interface(name):
    """Identify the likely type of network adapter."""
    name_lower = name.lower()

    if any(word in name_lower for word in ("wi-fi", "wifi", "wlan")):
        return "Wi-Fi"

    if any(word in name_lower for word in ("rndis", "usb")):
        return "USB Tethering"

    if "ethernet" in name_lower:
        return "Ethernet"

    return "Other"


def discover_network_paths():
    """
    Discover active network interfaces with usable IPv4 addresses.

    Excludes:
    - Inactive interfaces
    - Loopback addresses
    - IPv4 link-local addresses (169.254.x.x)
    """
    addresses = psutil.net_if_addrs()
    statistics = psutil.net_if_stats()

    paths = []

    for name, adapter_addresses in addresses.items():
        stats = statistics.get(name)

        # Ignore adapters that are down.
        if not stats or not stats.isup:
            continue

        for address in adapter_addresses:
            if address.family != socket.AF_INET:
                continue

            ip_address = address.address

            # Ignore loopback and automatically assigned link-local IPs.
            if ip_address.startswith("127."):
                continue

            if ip_address.startswith("169.254."):
                continue

            paths.append({
                "interface": name,
                "type": classify_interface(name),
                "source_ip": ip_address,
                "is_up": True,
                "link_speed_mbps": (
                    stats.speed if stats.speed > 0 else None
                ),
                "status": "discovered",
            })

    return paths


def test_network_path(source_ip, timeout=5):
    """
    Test TCP connectivity to 1.1.1.1:443 using a specific local IPv4 address.

    This verifies connectivity through a source address. It does not
    measure bandwidth or prove that simultaneous bonding works.
    """
    result = {
        "source_ip": source_ip,
        "target": "1.1.1.1:443",
        "connected": False,
        "latency_ms": None,
        "error": None,
    }

    try:
        with socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM
        ) as sock:
            sock.settimeout(timeout)

            # Bind to the requested local address.
            sock.bind((source_ip, 0))

            start = time.perf_counter()
            sock.connect(("1.1.1.1", 443))

            result["connected"] = True
            result["latency_ms"] = round(
                (time.perf_counter() - start) * 1000,
                2
            )

    except OSError as error:
        result["error"] = str(error)

    return result


def check_all_network_paths():
    """Discover interfaces and test connectivity for each candidate path."""
    paths = discover_network_paths()
    results = []

    for path in paths:
        item = dict(path)

        item["connectivity"] = test_network_path(
            path["source_ip"]
        )

        if item["connectivity"]["connected"]:
            item["status"] = "online"
        else:
            item["status"] = "unverified"

        results.append(item)

    return {
        "checked_at": time.time(),
        "path_count": len(results),
        "online_path_count": sum(
            1 for item in results
            if item["connectivity"]["connected"]
        ),
        "paths": results,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            check_all_network_paths(),
            indent=2
        )
    )
