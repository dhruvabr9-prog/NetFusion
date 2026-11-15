import argparse
import hashlib
import math
import socket
import time
import uuid
from pathlib import Path

from tunnel.protocol import (
    MAX_DATAGRAM_SIZE,
    HEADER_SIZE,
    encode_data,
    encode_ack,
    decode_ack,
    encode_manifest,
    make_manifest,
)

CHUNK_SIZE = MAX_DATAGRAM_SIZE - HEADER_SIZE
ACK_TIMEOUT = 0.5
MAX_RETRIES = 10


def send_file(
    file_path,
    host="127.0.0.1",
    port=9000,
    source_ip=None,
    delay=0,
):
    path = Path(file_path)

    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    data = path.read_bytes()
    transfer_id = str(uuid.uuid4())

    total_chunks = math.ceil(len(data) / CHUNK_SIZE) if data else 0
    manifest = make_manifest(transfer_id, total_chunks, len(data))
    destination = (host, port)

    acknowledged = 0
    retransmissions = 0
    start = time.perf_counter()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        if source_ip:
            sock.bind((source_ip, 0))

        sock.settimeout(ACK_TIMEOUT)

        # Send the manifest. The receiver must be running first.
        sock.sendto(encode_manifest(manifest), destination)

        for sequence in range(total_chunks):
            start_index = sequence * CHUNK_SIZE
            chunk = data[start_index:start_index + CHUNK_SIZE]
            packet = encode_data(transfer_id, sequence, chunk)

            success = False

            for attempt in range(MAX_RETRIES + 1):
                sock.sendto(packet, destination)

                deadline = time.monotonic() + ACK_TIMEOUT

                while time.monotonic() < deadline:
                    try:
                        response, address = sock.recvfrom(256)
                    except socket.timeout:
                        break

                    if address != destination:
                        continue

                    try:
                        ack = decode_ack(response)
                    except ValueError:
                        continue

                    if (
                        ack["transfer_id"] == transfer_id
                        and ack["sequence"] == sequence
                    ):
                        success = True
                        acknowledged += 1
                        break

                if success:
                    break

                if attempt < MAX_RETRIES:
                    retransmissions += 1

            if not success:
                raise TimeoutError(
                    f"No ACK for chunk {sequence} after "
                    f"{MAX_RETRIES + 1} attempts. "
                    "Check that the receiver is running."
                )

            if delay > 0:
                time.sleep(delay)

            if acknowledged % 500 == 0 or acknowledged == total_chunks:
                print(
                    f"\rAcknowledged: {acknowledged}/{total_chunks}",
                    end="",
                    flush=True,
                )

        elapsed = time.perf_counter() - start

    print()

    return {
        "transfer_id": transfer_id,
        "file": str(path),
        "source_ip": source_ip or "OS-selected",
        "destination": f"{host}:{port}",
        "bytes_sent": len(data),
        "chunks_sent": total_chunks,
        "chunks_acknowledged": acknowledged,
        "retransmissions": retransmissions,
        "sha256": hashlib.sha256(data).hexdigest(),
        "duration_seconds": round(elapsed, 3),
        "send_rate_mbps": (
            round(len(data) * 8 / elapsed / 1_000_000, 3)
            if elapsed > 0 else 0
        ),
    }


def main():
    parser = argparse.ArgumentParser(
        description="NetFusion reliable UDP file sender"
    )

    parser.add_argument("--file", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--source", default=None)
    parser.add_argument("--delay", type=float, default=0)

    args = parser.parse_args()

    result = send_file(
        file_path=args.file,
        host=args.host,
        port=args.port,
        source_ip=args.source,
        delay=max(0, args.delay),
    )

    print("\nNetFusion reliable transfer summary")

    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()