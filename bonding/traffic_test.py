
import argparse
import socket
import time
from urllib.parse import urlparse

import requests


DEFAULT_URL = "https://speed.cloudflare.com/__down?bytes=10000000"


def download_through_path(source_ip, url=DEFAULT_URL, timeout=30):
    """
    Download data using an HTTP connection bound to source_ip.

    This measures application-level download throughput for one connection.
    It does not combine bandwidth across interfaces.
    """
    result = {
        "source_ip": source_ip,
        "url": url,
        "success": False,
        "bytes_received": 0,
        "duration_seconds": None,
        "download_mbps": None,
        "error": None,
    }

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        result["error"] = "A valid HTTP or HTTPS URL is required."
        return result

    session = requests.Session()

    # Prevent environment proxy settings from silently routing the request
    # through a proxy instead of using the intended source connection.
    session.trust_env = False

    original_getaddrinfo = socket.getaddrinfo

    try:
        # Resolve the host, then make the TCP socket bind to the chosen
        # local source address before connecting.
        def bound_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            return original_getaddrinfo(
                host, port, family, type, proto, flags
            )

        # Use a custom HTTP adapter that binds each new TCP connection.
        from requests.adapters import HTTPAdapter
        from urllib3 import PoolManager

        class SourceBoundAdapter(HTTPAdapter):
            def init_poolmanager(
                self, connections, maxsize, block=False, **pool_kwargs
            ):
                pool_kwargs["source_address"] = (source_ip, 0)
                self.poolmanager = PoolManager(
                    num_pools=connections,
                    maxsize=maxsize,
                    block=block,
                    **pool_kwargs,
                )

        session.mount("http://", SourceBoundAdapter())
        session.mount("https://", SourceBoundAdapter())

        start = time.perf_counter()

        with session.get(
            url,
            stream=True,
            timeout=timeout,
            headers={"User-Agent": "NetFusion/0.1"},
        ) as response:
            response.raise_for_status()

            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    result["bytes_received"] += len(chunk)

        duration = time.perf_counter() - start

        result["duration_seconds"] = round(duration, 3)
        result["success"] = True

        if duration > 0:
            result["download_mbps"] = round(
                result["bytes_received"] * 8 / duration / 1_000_000,
                3,
            )

    except (requests.RequestException, OSError, ValueError) as error:
        result["error"] = str(error)

    finally:
        session.close()

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Test download throughput over one NetFusion network path."
    )

    parser.add_argument(
        "--source",
        required=True,
        help="Local IPv4 address to bind to, e.g. 10.67.12.147",
    )

    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="HTTP/HTTPS download URL",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="Request timeout in seconds",
    )

    args = parser.parse_args()

    print(f"Testing source IP: {args.source}")
    print("Downloading test data...")

    result = download_through_path(
        source_ip=args.source,
        url=args.url,
        timeout=args.timeout,
    )

    print("\nRESULT")
    print(f"Success: {result['success']}")
    print(f"Source IP: {result['source_ip']}")
    print(f"Bytes received: {result['bytes_received']}")
    print(f"Duration: {result['duration_seconds']} seconds")
    print(f"Download throughput: {result['download_mbps']} Mbps")

    if result["error"]:
        print(f"Error: {result['error']}")


if __name__ == "__main__":
    main()