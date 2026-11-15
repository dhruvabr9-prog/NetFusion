import argparse
import hashlib
import math
import socket
import time
import uuid
from pathlib import Path

from bonding.network_paths import discover_network_paths
from tunnel.protocol import (
    MAX_DATAGRAM_SIZE,
    encode_data,
    encode_manifest,
    make_manifest,
    HEADER_SIZE,
)

CHUNK_SIZE = MAX_DATAGRAM_SIZE - HEADER_SIZE


def discover_usable_paths():
    """Discover interfaces that have an IPv4 source address."""
    discovered = discover_network_paths()
    paths = []
    seen_ips = set()

    for path in discovered:
        source_ip = path.get("source_ip")

        if not source_ip or source_ip in seen_ips:
            continue

        try:
            socket.inet_pton(socket.AF_INET, source_ip)
        except OSError:
            continue

        seen_ips.add(source_ip)
        paths.append(path)

    return paths


def send_multipath(
    file_path,
    host,
    port=9000,
    delay=0.001,
    strategy="round_robin",
):
    path = Path(file_path)

    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    data = path.read_bytes()
    paths = discover_usable_paths()

    if not paths:
        raise RuntimeError("No usable IPv4 network paths found.")

    print("\nAvailable network paths:")

    for index, network in enumerate(paths):
        print(
            f"{index}: {network.get('interface', 'Unknown')} "
            f"({network.get('type', 'Unknown')}) "
            f"- {network['source_ip']}"
        )

    transfer_id = str(uuid.uuid4())

    total_chunks = (
        math.ceil(len(data) / CHUNK_SIZE)
        if data
        else 0
    )

    manifest = make_manifest(
        transfer_id,
        total_chunks,
        len(data),
    )

    destination = (host, port)
    sockets = []
    chunk_counts = [0] * len(paths)

    try:
        # Bind a separate UDP socket to each interface.
        for network in paths:
            sock = socket.socket(
                socket.AF_INET,
                socket.SOCK_DGRAM,
            )

            sock.bind((network["source_ip"], 0))
            sockets.append(sock)

        # Send the manifest through the first available path.
        # The receiver must receive the manifest before it can
        # determine the expected number of chunks.
        manifest_packet = encode_manifest(manifest)
        start = time.perf_counter()

        sockets[0].sendto(manifest_packet, destination)

        # Allow the manifest a moment to arrive before data.
        if delay > 0:
            time.sleep(delay)

        for sequence in range(total_chunks):
            start_index = sequence * CHUNK_SIZE
            chunk = data[
                start_index:start_index + CHUNK_SIZE
            ]

            packet = encode_data(
                transfer_id,
                sequence,
                chunk,
            )

            if strategy == "round_robin":
                path_index = sequence % len(sockets)

            elif strategy == "single":
                path_index = 0

            else:
                raise ValueError(
                    f"Unsupported strategy: {strategy}"
                )

            sockets[path_index].sendto(
                packet,
                destination,
            )

            chunk_counts[path_index] += 1

            if delay > 0:
                time.sleep(delay)

        elapsed = time.perf_counter() - start

    finally:
        for sock in sockets:
            sock.close()

    sha256 = hashlib.sha256(data).hexdigest()

    print("\n" + "=" * 45)
    print("NETFUSION MULTIPATH SEND SUMMARY")
    print("=" * 45)
    print(f"Transfer ID: {transfer_id}")
    print(f"File: {path.resolve()}")
    print(f"Bytes sent: {len(data)}")
    print(f"Chunks sent: {total_chunks}")
    print(f"SHA256 of original: {sha256}")
    print(f"Elapsed: {elapsed:.3f} seconds")

    if elapsed > 0:
        print(
            "Sender rate: "
            f"{len(data) * 8 / elapsed / 1_000_000:.3f} Mbps"
        )

    print("\nChunk assignments:")

    for index, network in enumerate(paths):
        print(
            f"{network.get('interface', 'Unknown')}: "
            f"{chunk_counts[index]} chunks "
            f"(source {network['source_ip']})"
        )

    print("=" * 45)
    print(
        "IMPORTANT: UDP send success does not guarantee delivery. "
        "Verify the received file's SHA256."
    )
    print(
        "This prototype distributes packets across local sockets; "
        "it does not yet provide reliable delivery or prove "
        "aggregate internet bandwidth."
    )

    return {
        "transfer_id": transfer_id,
        "bytes_sent": len(data),
        "chunks_sent": total_chunks,
        "sha256": sha256,
        "chunk_counts": chunk_counts,
    }


def main():
    parser = argparse.ArgumentParser(
        description="NetFusion experimental multipath sender"
    )

    parser.add_argument(
        "--file",
        required=True,
        help="File to send",
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Receiver IPv4 address",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=9000,
        help="Receiver UDP port",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.001,
        help="Delay between chunks in seconds",
    )

    parser.add_argument(
        "--strategy",
        choices=["round_robin", "single"],
        default="round_robin",
        help="How chunks are assigned to paths",
    )

    args = parser.parse_args()

    send_multipath(
        file_path=args.file,
        host=args.host,
        port=args.port,
        delay=max(0, args.delay),
        strategy=args.strategy,
    )


if __name__ == "__main__":
    main()
