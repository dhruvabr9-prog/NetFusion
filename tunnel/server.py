import argparse
import hashlib
import json
import socket
import time
from pathlib import Path

from tunnel.protocol import (
    ACK_MESSAGE,
    DATA_MESSAGE,
    HEADER_FORMAT,
    HEADER_SIZE,
    MAX_DATAGRAM_SIZE,
    PROTOCOL_VERSION,
    decode_data,
    decode_manifest,
    encode_ack,
)


def serve(host="127.0.0.1", port=9000, output_dir="received"):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    transfers = {}

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((host, port))
        sock.settimeout(1.0)

        print(f"NetFusion reliable UDP receiver listening on {host}:{port}")
        print("Press Ctrl+C to stop.")

        try:
            while True:
                try:
                    datagram, address = sock.recvfrom(
                        MAX_DATAGRAM_SIZE + 1
                    )
                except socket.timeout:
                    continue

                if len(datagram) > MAX_DATAGRAM_SIZE:
                    print(f"Ignored oversized datagram from {address}")
                    continue

                try:
                    # Manifest packets are JSON.
                    if datagram.startswith(b"{"):
                        manifest = decode_manifest(datagram)
                        transfer_id = manifest["transfer_id"]

                        transfer = transfers.setdefault(
                            transfer_id,
                            {
                                "chunks": {},
                                "manifest": None,
                                "started_at": time.perf_counter(),
                                "last_progress": 0,
                            },
                        )

                        transfer["manifest"] = manifest

                        print(
                            f"\nManifest received: {transfer_id}\n"
                            f"Expected size: {manifest['total_bytes']} bytes\n"
                            f"Expected chunks: {manifest['total_chunks']}"
                        )

                        continue

                    if len(datagram) < HEADER_SIZE:
                        continue

                    # Inspect the packet type from the header.
                    version, message_type, transfer_uuid, sequence = (
                        __import__("struct").unpack(
                            HEADER_FORMAT,
                            datagram[:HEADER_SIZE],
                        )
                    )

                    if version != PROTOCOL_VERSION:
                        continue

                    if message_type != DATA_MESSAGE:
                        continue

                    packet = decode_data(datagram)
                    transfer_id = packet["transfer_id"]

                    transfer = transfers.setdefault(
                        transfer_id,
                        {
                            "chunks": {},
                            "manifest": None,
                            "started_at": time.perf_counter(),
                            "last_progress": 0,
                        },
                    )

                    manifest = transfer["manifest"]

                    # Validate incoming chunks when the manifest is known.
                    if manifest is not None:
                        if sequence >= manifest["total_chunks"]:
                            continue

                    chunks = transfer["chunks"]

                    # Store a chunk only once, but ACK duplicate packets too.
                    if sequence not in chunks:
                        chunks[sequence] = packet["payload"]

                    # ACK immediately so the sender can continue.
                    sock.sendto(
                        encode_ack(transfer_id, sequence),
                        address,
                    )

                    received_count = len(chunks)

                    if received_count >= transfer["last_progress"] + 500:
                        transfer["last_progress"] = received_count
                        expected_count = (
                            manifest["total_chunks"]
                            if manifest is not None
                            else "unknown"
                        )

                        print(
                            f"Progress {transfer_id}: "
                            f"{received_count}/{expected_count} chunks"
                        )

                    # Finish only after the manifest and all chunks arrive.
                    manifest = transfer["manifest"]

                    if manifest is None:
                        continue

                    total_chunks = manifest["total_chunks"]
                    total_bytes = manifest["total_bytes"]

                    if len(chunks) != total_chunks:
                        continue

                    if set(chunks) != set(range(total_chunks)):
                        continue

                    content = b"".join(
                        chunks[index] for index in range(total_chunks)
                    )

                    if len(content) != total_bytes:
                        print(f"ERROR: Size mismatch for {transfer_id}")
                        del transfers[transfer_id]
                        continue

                    # Verify file integrity before saving.
                    sha256 = hashlib.sha256(content).hexdigest()
                    output_file = output_path / f"{transfer_id}.bin"
                    output_file.write_bytes(content)

                    elapsed = max(
                        time.perf_counter() - transfer["started_at"],
                        0.000001,
                    )

                    throughput = len(content) * 8 / elapsed / 1_000_000

                    print("\n" + "=" * 50)
                    print("NETFUSION TRANSFER COMPLETE")
                    print("=" * 50)
                    print(f"Transfer ID: {transfer_id}")
                    print(f"Saved to: {output_file.resolve()}")
                    print(f"Bytes received: {len(content)}")
                    print(f"Chunks received: {len(chunks)}/{total_chunks}")
                    print(f"SHA256: {sha256}")
                    print(f"Elapsed: {elapsed:.3f} seconds")
                    print(f"Receiver throughput: {throughput:.3f} Mbps")
                    print("=" * 50 + "\n")

                    del transfers[transfer_id]

                except (
                    ValueError,
                    KeyError,
                    TypeError,
                    json.JSONDecodeError,
                ) as error:
                    print(f"Invalid packet from {address}: {error}")

        except KeyboardInterrupt:
            print("\nReceiver stopped.")

            for transfer_id, transfer in transfers.items():
                manifest = transfer["manifest"]

                if manifest is None:
                    print(f"Incomplete transfer {transfer_id}: no manifest")
                    continue

                total = manifest["total_chunks"]
                received = transfer["chunks"]
                missing = sorted(set(range(total)) - set(received))

                print(f"\nIncomplete transfer: {transfer_id}")
                print(f"Received chunks: {len(received)}/{total}")
                print(f"Missing chunks: {len(missing)}")
                print(f"First missing chunks: {missing[:20]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="NetFusion reliable UDP receiver"
    )

    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--output-dir", default="received")

    args = parser.parse_args()

    serve(args.host, args.port, args.output_dir)