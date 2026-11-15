import socket
import time

NETWORKS = [
    ("Wi-Fi", "10.24.115.189"),
    ("USB tethering", "10.213.88.215"),
]

TARGET = ("1.1.1.1", 443)


def test_path(name, source_ip):
    print(f"\nTesting {name}: {source_ip}")

    try:
        with socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM,
        ) as sock:
            sock.settimeout(8)
            sock.bind((source_ip, 0))

            start = time.perf_counter()
            sock.connect(TARGET)

            elapsed = (time.perf_counter() - start) * 1000

            print("Status: CONNECTED")
            print(f"TCP connection time: {elapsed:.2f} ms")
            print(f"Local address: {sock.getsockname()}")

    except OSError as error:
        print("Status: FAILED")
        print(f"Reason: {error}")


if __name__ == "__main__":
    for name, source_ip in NETWORKS:
        test_path(name, source_ip)
