import hashlib
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from bonding.network_paths import discover_network_paths
from tunnel.multipath import send_file, serve


class MultipathTransferIntegrationTest(unittest.TestCase):
    def test_file_is_reassembled_and_checksum_confirmed(self):
        paths = discover_network_paths()
        if not paths:
            self.skipTest("No active non-loopback IPv4 interface is available")

        source_ips = [path["source_ip"] for path in paths]
        destinations = {source_ip: source_ip for source_ip in source_ips}
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("0.0.0.0", 0))
        port = probe.getsockname()[1]
        probe.close()

        received = []
        stop_event = threading.Event()

        with tempfile.TemporaryDirectory(prefix="netfusion-test-") as output_dir:
            receiver = threading.Thread(
                target=serve,
                args=("0.0.0.0", port, output_dir, received.append, stop_event),
                daemon=True,
            )
            receiver.start()
            time.sleep(0.15)
            try:
                content = bytes(range(256)) * 128
                source = Path(output_dir) / "source.dat"
                source.write_bytes(content)

                result = send_file(
                    source,
                    "127.0.0.1",
                    port,
                    source_ips,
                    destinations=destinations,
                )

                self.assertEqual(result["chunks_acknowledged"], result["chunks_sent"])
                self.assertEqual(len(result["paths"]), len(source_ips))
                self.assertTrue(all(path["chunks_sent"] > 0 for path in result["paths"]))
                self.assertEqual(len(received), 1)
                received_path = Path(received[0]["path"])
                self.assertEqual(received_path.read_bytes(), content)
                self.assertEqual(
                    result["sha256"],
                    hashlib.sha256(received_path.read_bytes()).hexdigest(),
                )
            finally:
                stop_event.set()
                receiver.join(timeout=2)
            self.assertFalse(receiver.is_alive())


if __name__ == "__main__":
    unittest.main()
