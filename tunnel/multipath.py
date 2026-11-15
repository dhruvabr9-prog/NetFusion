"""Reliable UDP file transfer striped across local IPv4 interfaces.

This transfers files to a cooperating NetFusion receiver. It does not route
general Windows traffic or combine unrelated speed-test measurements.
"""

import argparse
import hashlib
import json
import os
import math
import select
import socket
import threading
import time
import uuid
from pathlib import Path

from bonding.network_paths import discover_network_paths
from tunnel.protocol import (
    HEADER_FORMAT,
    HEADER_SIZE,
    MAX_DATAGRAM_SIZE,
    PROTOCOL_VERSION,
    decode_ack,
    decode_data,
    encode_ack,
    encode_data,
)

CHUNK_SIZE = MAX_DATAGRAM_SIZE - HEADER_SIZE
ACK_TIMEOUT = 0.35
MAX_ATTEMPTS = 8
WINDOW_SIZE = 96
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024


def discover_transfer_paths(source_ips=None):
    """Return active IPv4 paths, optionally restricted to selected IPs."""
    requested = set(source_ips or [])
    paths = [
        path for path in discover_network_paths()
        if not requested or path["source_ip"] in requested
    ]
    unique = {}
    for path in paths:
        unique.setdefault(path["source_ip"], path)
    if requested - set(unique):
        raise ValueError("One or more selected source IPs are no longer active.")
    return list(unique.values())


def _control(message):
    return json.dumps(message, separators=(",", ":")).encode("utf-8")


def _read_control(packet):
    if not packet.startswith(b"{"):
        return None
    try:
        result = json.loads(packet.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return result if isinstance(result, dict) else None


def send_file(file_path, host, port=9000, source_ips=None, progress=None, destinations=None):
    """Send a file with chunk ACKs, retries, path striping, and checksum."""
    path = Path(file_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("The selected path is not a file.")
    size = path.stat().st_size
    if size > MAX_FILE_SIZE:
        raise ValueError(f"Files larger than {MAX_FILE_SIZE} bytes are not supported.")
    hasher = hashlib.sha256()
    with path.open("rb") as source_file:
        while block := source_file.read(1024 * 1024):
            hasher.update(block)
    digest = hasher.hexdigest()
    transfer_id = str(uuid.uuid4())
    paths = discover_transfer_paths(source_ips)
    if not paths:
        raise RuntimeError("No active IPv4 paths are available.")

    sockets = []
    active = []
    path_sends = []
    retransmissions = 0
    total_chunks = math.ceil(size / CHUNK_SIZE) if size else 0
    started = time.perf_counter()
    manifest = _control({
        "type": "manifest", "version": PROTOCOL_VERSION,
        "transfer_id": transfer_id, "filename": path.name,
        "total_bytes": size, "total_chunks": total_chunks,
        "sha256": digest,
    })

    source_stream = None
    try:
        source_stream = path.open("rb")
        for item in paths:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets.append(sock)
            sock.setblocking(False)
            sock.bind((item["source_ip"], 0))
            active.append(True)
            path_sends.append(0)
        path_destinations = [
            ((destinations or {}).get(item["source_ip"], host), int(port))
            for item in paths
        ]

        # Every path must reach the receiver before data is striped. This
        # avoids a missing manifest when the first data chunk takes another path.
        for index, sock in enumerate(sockets):
            acknowledged = False
            for attempt in range(MAX_ATTEMPTS):
                try:
                    sock.sendto(manifest, path_destinations[index])
                except OSError:
                    active[index] = False
                    break
                deadline = time.monotonic() + ACK_TIMEOUT
                while time.monotonic() < deadline:
                    readable, _, _ = select.select([sock], [], [], max(0, deadline - time.monotonic()))
                    if not readable:
                        break
                    try:
                        packet, _ = sock.recvfrom(MAX_DATAGRAM_SIZE + 1)
                    except ConnectionResetError:
                        # Windows may report delayed UDP ICMP errors on a socket.
                        continue
                    response = _read_control(packet)
                    if response and response.get("transfer_id") == transfer_id and response.get("type") == "manifest_ack":
                        acknowledged = True
                        break
                if acknowledged:
                    break
                if attempt:
                    retransmissions += 1
            if active[index] and not acknowledged:
                active[index] = False
        if not any(active):
            raise TimeoutError("The receiver did not acknowledge a manifest on any selected path.")

        pending = {}
        next_sequence = 0
        completed = False
        while next_sequence < total_chunks or pending:
            while next_sequence < total_chunks and len(pending) < WINDOW_SIZE:
                preferred = next_sequence % len(sockets)
                candidates = [preferred] + [i for i in range(len(sockets)) if i != preferred]
                selected = next((i for i in candidates if active[i]), None)
                if selected is None:
                    raise OSError("All transfer paths failed.")
                offset = next_sequence * CHUNK_SIZE
                source_stream.seek(offset)
                chunk = source_stream.read(min(CHUNK_SIZE, size - offset))
                packet = encode_data(transfer_id, next_sequence, chunk)
                try:
                    sockets[selected].sendto(packet, path_destinations[selected])
                except OSError:
                    active[selected] = False
                    continue
                path_sends[selected] += 1
                pending[next_sequence] = {"packet": packet, "path": selected, "attempts": 1, "sent": time.monotonic()}
                next_sequence += 1

            deadline = min((item["sent"] + ACK_TIMEOUT for item in pending.values()), default=time.monotonic() + ACK_TIMEOUT)
            readable, _, _ = select.select([s for i, s in enumerate(sockets) if active[i]], [], [], max(0, deadline - time.monotonic()))
            for sock in readable:
                try:
                    response, _ = sock.recvfrom(MAX_DATAGRAM_SIZE + 1)
                except OSError:
                    continue
                control = _read_control(response)
                if control and control.get("transfer_id") == transfer_id:
                    if control.get("type") == "complete" and control.get("sha256") == digest:
                        completed = True
                    continue
                try:
                    ack = decode_ack(response)
                except ValueError:
                    continue
                if ack["transfer_id"] == transfer_id:
                    pending.pop(ack["sequence"], None)
            now = time.monotonic()
            for sequence, item in list(pending.items()):
                if now - item["sent"] < ACK_TIMEOUT:
                    continue
                if item["attempts"] >= MAX_ATTEMPTS:
                    del pending[sequence]
                    # Retry the chunk through another active path when possible.
                    fallback = next((i for i, is_active in enumerate(active) if is_active and i != item["path"]), None)
                    if fallback is None:
                        raise TimeoutError(f"No acknowledgement for chunk {sequence}.")
                    item["path"] = fallback
                    item["attempts"] = 0
                    pending[sequence] = item
                item["attempts"] += 1
                item["sent"] = now
                try:
                    sockets[item["path"]].sendto(item["packet"], path_destinations[item["path"]])
                    path_sends[item["path"]] += 1
                except OSError:
                    active[item["path"]] = False
                    item["sent"] = 0
                retransmissions += 1
            acknowledged_count = next_sequence - len(pending)
            if progress:
                progress({"acknowledged_chunks": acknowledged_count, "total_chunks": total_chunks})

        # Prompt a cached completion response if the first one was lost.
        end_deadline = time.monotonic() + 3
        while not completed and time.monotonic() < end_deadline:
            for index, sock in enumerate(sockets):
                if active[index]:
                    try:
                        sock.sendto(_control({"type": "confirm", "transfer_id": transfer_id}), path_destinations[index])
                    except OSError:
                        active[index] = False
            readable, _, _ = select.select([s for i, s in enumerate(sockets) if active[i]], [], [], 0.4)
            for sock in readable:
                try:
                    response = sock.recvfrom(MAX_DATAGRAM_SIZE + 1)[0]
                except ConnectionResetError:
                    continue
                control = _read_control(response)
                if control and control.get("type") == "complete" and control.get("transfer_id") == transfer_id and control.get("sha256") == digest:
                    completed = True
        if not completed:
            raise TimeoutError("All chunks were acknowledged, but the receiver did not confirm the saved file checksum.")
    finally:
        for sock in sockets:
            sock.close()
        if source_stream:
            source_stream.close()

    elapsed = max(time.perf_counter() - started, 1e-9)
    return {
        "transfer_id": transfer_id, "file": str(path), "destination": f"{host}:{port}",
        "bytes_sent": size, "chunks_sent": total_chunks, "chunks_acknowledged": total_chunks,
        "retransmissions": retransmissions, "sha256": digest,
        "duration_seconds": round(elapsed, 3),
        "throughput_mbps": round(size * 8 / elapsed / 1_000_000, 3),
        "paths": [{"interface": item["interface"], "type": item["type"], "source_ip": item["source_ip"], "active": active[i], "chunks_sent": path_sends[i]} for i, item in enumerate(paths)],
    }


def serve(host="0.0.0.0", port=9000, output_dir="received", progress=None, stop_event=None):
    """Run the cooperating receiver. Bind only where the operator intends."""
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    transfers = {}
    completed = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((host, int(port)))
        sock.settimeout(1)
        while stop_event is None or not stop_event.is_set():
            try:
                packet, peer = sock.recvfrom(MAX_DATAGRAM_SIZE + 1)
            except (socket.timeout, ConnectionResetError):
                continue
            if len(packet) > MAX_DATAGRAM_SIZE:
                continue
            control = _read_control(packet)
            if control:
                transfer_id = control.get("transfer_id")
                if control.get("type") == "manifest":
                    try:
                        if control.get("version") != PROTOCOL_VERSION:
                            raise ValueError("Unsupported protocol version")
                        uuid.UUID(transfer_id)
                        size, chunks = control.get("total_bytes"), control.get("total_chunks")
                        if type(size) is not int or size < 0 or size > MAX_FILE_SIZE:
                            raise ValueError("Invalid file size")
                        expected = math.ceil(size / CHUNK_SIZE) if size else 0
                        if type(chunks) is not int or chunks != expected or not isinstance(control.get("filename"), str):
                            raise ValueError("Invalid transfer manifest")
                        filename = control["filename"]
                        if (not filename or len(filename) > 255 or Path(filename).name != filename
                                or "/" in filename or "\\" in filename or "\x00" in filename):
                            raise ValueError("Invalid filename")
                        if not isinstance(control.get("sha256"), str) or len(control["sha256"]) != 64:
                            raise ValueError("Invalid checksum")
                        if transfer_id in completed:
                            sock.sendto(_control({"type": "manifest_ack", "transfer_id": transfer_id}), peer)
                            sock.sendto(_control(completed[transfer_id]), peer)
                            continue
                        if transfer_id not in transfers:
                            temp_path = destination / f".{transfer_id}.part"
                            stream = temp_path.open("x+b")
                            stream.truncate(size)
                            transfers[transfer_id] = {
                                "manifest": control,
                                "received": set(),
                                "stream": stream,
                                "temp_path": temp_path,
                            }
                        sock.sendto(_control({"type": "manifest_ack", "transfer_id": transfer_id}), peer)
                        if expected == 0:
                            digest = hashlib.sha256(b"").hexdigest()
                            if digest != control["sha256"]:
                                raise ValueError("Invalid empty-file checksum")
                            output_file = destination / f"{transfer_id}_{Path(control['filename']).name}"
                            with output_file.open("xb"):
                                pass
                            result = {"type": "complete", "transfer_id": transfer_id, "sha256": digest, "path": str(output_file)}
                            completed[transfer_id] = result
                            transfers.pop(transfer_id, None)
                            sock.sendto(_control(result), peer)
                            if progress:
                                progress({"transfer_id": transfer_id, "path": str(output_file), "bytes_received": 0, "sha256": digest})
                    except (ValueError, TypeError, KeyError):
                        sock.sendto(_control({"type": "error", "transfer_id": transfer_id, "message": "Invalid manifest"}), peer)
                    continue
                if control.get("type") == "confirm" and transfer_id in completed:
                    sock.sendto(_control(completed[transfer_id]), peer)
                continue
            try:
                data_packet = decode_data(packet)
            except ValueError:
                continue
            transfer_id = data_packet["transfer_id"]
            state = transfers.get(transfer_id)
            if state is None:
                if transfer_id in completed:
                    sock.sendto(_control(completed[transfer_id]), peer)
                continue
            if data_packet["sequence"] >= state["manifest"]["total_chunks"]:
                continue
            sequence = data_packet["sequence"]
            expected_length = min(CHUNK_SIZE, state["manifest"]["total_bytes"] - sequence * CHUNK_SIZE)
            if len(data_packet["payload"]) != expected_length:
                continue
            if sequence not in state["received"]:
                state["stream"].seek(sequence * CHUNK_SIZE)
                state["stream"].write(data_packet["payload"])
                state["received"].add(sequence)
            sock.sendto(encode_ack(transfer_id, data_packet["sequence"]), peer)
            manifest = state["manifest"]
            total = manifest["total_chunks"]
            if len(state["received"]) == total:
                state["stream"].flush()
                state["stream"].seek(0)
                hasher = hashlib.sha256()
                while block := state["stream"].read(1024 * 1024):
                    hasher.update(block)
                digest = hasher.hexdigest()
                state["stream"].close()
                if digest != manifest["sha256"]:
                    state["temp_path"].unlink(missing_ok=True)
                    del transfers[transfer_id]
                    continue
                output_file = destination / f"{transfer_id}_{Path(manifest['filename']).name}"
                os.replace(state["temp_path"], output_file)
                result = {"type": "complete", "transfer_id": transfer_id, "sha256": digest, "path": str(output_file)}
                completed[transfer_id] = result
                del transfers[transfer_id]
                sock.sendto(_control(result), peer)
                if progress:
                    progress({"transfer_id": transfer_id, "path": str(output_file), "bytes_received": manifest["total_bytes"], "sha256": digest})


def main():
    parser = argparse.ArgumentParser(description="Reliable NetFusion multipath transfer")
    sub = parser.add_subparsers(dest="command", required=True)
    receiver = sub.add_parser("receive", help="Listen for transfers")
    receiver.add_argument("--host", default="0.0.0.0")
    receiver.add_argument("--port", type=int, default=9000)
    receiver.add_argument("--output-dir", default="received")
    sender = sub.add_parser("send", help="Send a file across active interfaces")
    sender.add_argument("--file", required=True)
    sender.add_argument("--host", required=True, help="Receiver IPv4 address or hostname")
    sender.add_argument("--port", type=int, default=9000)
    sender.add_argument("--source", action="append", dest="source_ips", help="Source IPv4 address (repeatable)")
    args = parser.parse_args()
    if args.command == "receive":
        print(f"NetFusion receiver listening on {args.host}:{args.port}")
        serve(args.host, args.port, args.output_dir)
    else:
        print(json.dumps(send_file(args.file, args.host, args.port, args.source_ips), indent=2))


if __name__ == "__main__":
    main()
