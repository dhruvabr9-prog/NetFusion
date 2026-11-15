import json
import struct
import time
import uuid

PROTOCOL_VERSION = 1
MAX_DATAGRAM_SIZE = 1200

# Version (1 byte), message type (1 byte),
# transfer ID (16 bytes), sequence number (4 bytes).
HEADER_FORMAT = "!BB16sI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

DATA_MESSAGE = 1
ACK_MESSAGE = 2

MANIFEST_MESSAGE = "manifest"


def encode_data(transfer_id, sequence, payload):
    """Encode a numbered data chunk."""
    if isinstance(transfer_id, str):
        transfer_id = uuid.UUID(transfer_id).bytes

    if len(transfer_id) != 16:
        raise ValueError("transfer_id must be a 16-byte UUID")

    payload = bytes(payload)

    if HEADER_SIZE + len(payload) > MAX_DATAGRAM_SIZE:
        raise ValueError("Datagram exceeds maximum size")

    if not 0 <= sequence <= 0xFFFFFFFF:
        raise ValueError("Invalid sequence number")

    header = struct.pack(
        HEADER_FORMAT,
        PROTOCOL_VERSION,
        DATA_MESSAGE,
        transfer_id,
        sequence,
    )

    return header + payload


def decode_data(datagram):
    """Decode and validate a data packet."""
    if len(datagram) < HEADER_SIZE:
        raise ValueError("Packet smaller than header")

    if len(datagram) > MAX_DATAGRAM_SIZE:
        raise ValueError("Packet exceeds maximum size")

    version, message_type, transfer_id, sequence = struct.unpack(
        HEADER_FORMAT,
        datagram[:HEADER_SIZE],
    )

    if version != PROTOCOL_VERSION:
        raise ValueError("Unsupported protocol version")

    if message_type != DATA_MESSAGE:
        raise ValueError("Unexpected message type")

    return {
        "transfer_id": str(uuid.UUID(bytes=transfer_id)),
        "sequence": sequence,
        "payload": datagram[HEADER_SIZE:],
    }


def encode_ack(transfer_id, sequence):
    """Encode an acknowledgement for a received chunk."""
    if isinstance(transfer_id, str):
        transfer_id = uuid.UUID(transfer_id).bytes

    if len(transfer_id) != 16:
        raise ValueError("transfer_id must be a 16-byte UUID")

    if not 0 <= sequence <= 0xFFFFFFFF:
        raise ValueError("Invalid sequence number")

    return struct.pack(
        HEADER_FORMAT,
        PROTOCOL_VERSION,
        ACK_MESSAGE,
        transfer_id,
        sequence,
    )


def decode_ack(datagram):
    """Decode an acknowledgement packet."""
    if len(datagram) != HEADER_SIZE:
        raise ValueError("Invalid ACK packet size")

    version, message_type, transfer_id, sequence = struct.unpack(
        HEADER_FORMAT,
        datagram,
    )

    if version != PROTOCOL_VERSION:
        raise ValueError("Unsupported protocol version")

    if message_type != ACK_MESSAGE:
        raise ValueError("Unexpected message type")

    return {
        "transfer_id": str(uuid.UUID(bytes=transfer_id)),
        "sequence": sequence,
    }


def make_manifest(transfer_id, total_chunks, total_bytes):
    return {
        "version": PROTOCOL_VERSION,
        "type": MANIFEST_MESSAGE,
        "transfer_id": str(transfer_id),
        "total_chunks": total_chunks,
        "total_bytes": total_bytes,
        "created_at": time.time(),
    }


def encode_manifest(manifest):
    return json.dumps(
        manifest,
        separators=(",", ":"),
    ).encode("utf-8")


def decode_manifest(data):
    manifest = json.loads(data.decode("utf-8"))

    if manifest.get("version") != PROTOCOL_VERSION:
        raise ValueError("Unsupported protocol version")

    if manifest.get("type") != MANIFEST_MESSAGE:
        raise ValueError("Invalid manifest type")

    if type(manifest.get("total_chunks")) is not int:
        raise ValueError("Invalid total_chunks")

    if type(manifest.get("total_bytes")) is not int:
        raise ValueError("Invalid total_bytes")

    if manifest["total_chunks"] < 0 or manifest["total_bytes"] < 0:
        raise ValueError("Transfer sizes cannot be negative")

    expected_chunks = (
        (manifest["total_bytes"] + (MAX_DATAGRAM_SIZE - HEADER_SIZE) - 1)
        // (MAX_DATAGRAM_SIZE - HEADER_SIZE)
    )

    if manifest["total_chunks"] != expected_chunks:
        raise ValueError("Chunk count does not match file size")

    uuid.UUID(manifest["transfer_id"])

    return manifest


if __name__ == "__main__":
    transfer_id = str(uuid.uuid4())

    packet = encode_data(transfer_id, 0, b"NetFusion test")
    decoded = decode_data(packet)

    ack = encode_ack(transfer_id, decoded["sequence"])
    decoded_ack = decode_ack(ack)

    print("NetFusion protocol test")
    print("Data payload:", decoded["payload"].decode())
    print("ACK sequence:", decoded_ack["sequence"])
    print("Protocol test passed!")