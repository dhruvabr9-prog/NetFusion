# NetFusion

NetFusion is a local network monitoring dashboard with a FastAPI backend. This package integrates the provided frontend with interface discovery, per-interface traffic monitoring, source-bound connectivity tests, queued speed tests, and reliable multipath file transfer to a cooperating NetFusion receiver.

## Requirements

- Python 3.10 or newer
- Windows, Linux, or macOS (network adapter labels differ by operating system)
- Internet access for speed tests

## Install

From this folder, using your existing virtual environment if you have one:

```powershell
python -m pip install -r requirements.txt
```

## Run on Windows PowerShell

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

If this project folder does not already have `.venv`, create it first with `python -m venv .venv`, then install requirements using `\.venv\Scripts\python.exe -m pip install -r requirements.txt`.

Open the dashboard at http://127.0.0.1:8000/ and the API documentation at http://127.0.0.1:8000/docs.

## API

- `GET /health`
- `GET /interfaces`
- `GET /traffic`
- `POST /test-connection?source_ip=<current-local-ip>`
- `POST /speedtests` with JSON `{"source_ip":"<current-local-ip>"}`
- `GET /speedtests/{job_id}`
- `GET /speedtests`
- `POST /multipath/transfers` with JSON `{"file_path":"C:\\path\\file.zip","host":"receiver-ip","port":9000,"source_ips":["local-ip-1","local-ip-2"]}`
- `GET /multipath/transfers/{job_id}`

Speed tests run in a background worker, one at a time. Results are stored in memory and are cleared when the backend restarts. `speedtest-cli` relies on third-party Speedtest infrastructure, and results may vary or fail due to routing, server availability, ISP restrictions, or VPN/proxy settings.

## Multipath file transfer

On the receiver computer, from this project folder, run:

```powershell
python -m tunnel.multipath receive --host 0.0.0.0 --port 9000 --output-dir received
```

On the sending computer, run the dashboard and open **Bonding Lab**. Enter a local file path, the receiver's reachable IPv4 address or hostname, and select the active interfaces to use. The receiver stores verified files under the chosen output directory with a transfer ID in the filename. You can also send from PowerShell:

```powershell
python -m tunnel.multipath send --file .\large_test.bin --host RECEIVER_IP --port 9000
```

Use a receiver address reachable from each selected network. The receiver must already be running. Choose a bind address appropriate for the receiving network; this setup does not change Windows routing or firewall rules. The protocol splits one file into numbered chunks, acknowledges chunks, retransmits unacknowledged chunks, and verifies the final SHA-256 before reporting success. Retries can move to another active source path. The receiver currently has no authentication or encryption, so use a trusted network and do not expose it publicly. It transfers files between cooperating NetFusion peers; it does not bond arbitrary applications or make unrelated speed-test results additive.

## Current limitations

- Traffic numbers are observed interface-counter rates, not maximum available speed.
- Speed tests are separate measurements per source IP; their results must not be added and represented as bonded throughput.
- Receiver confirmation requires a reachable receiver running the NetFusion protocol. A local integration check can verify packets across the source interfaces, but it does not measure two independent Internet routes to a remote host.
- Throughput depends on both network paths, receiver capacity, latency, and packet loss. This is file transfer multipath, not system-wide Windows traffic bonding.
