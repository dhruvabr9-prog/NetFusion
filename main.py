import asyncio
import socket
import time
import uuid
import threading

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import psutil

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bonding.network_paths import check_all_network_paths, classify_interface, get_interface_descriptions
from tunnel.multipath import send_file as send_multipath_file


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CSS_DIR = STATIC_DIR / "css"
JS_DIR = STATIC_DIR / "js"

MONITOR_INTERVAL = 10

traffic_previous = {}
speedtest_jobs = {}
speedtest_lock = threading.Lock()
multipath_jobs = {}
multipath_lock = threading.Lock()


# ============================================================
# FAILOVER STATE
# ============================================================

failover_state = {
    "active_source_ip": None,
    "active_interface": None,
    "previous_source_ip": None,
    "last_checked": None,
    "last_event": "Waiting for the first network check.",
    "monitor_running": False,
    "network_results": {
        "paths": [],
        "checked_at": None,
    },
}


# ============================================================
# UTILITIES
# ============================================================

def current_time():
    return datetime.now(timezone.utc).isoformat()


def get_interfaces():
    """Discover active IPv4 network interfaces."""

    addresses = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    descriptions = get_interface_descriptions()

    results = []

    for interface_name, address_list in addresses.items():
        interface_stats = stats.get(interface_name)

        for address in address_list:
            if address.family != socket.AF_INET:
                continue

            ip_address = address.address

            if ip_address.startswith("127.") or ip_address.startswith("169.254."):
                continue

            is_up = bool(
                interface_stats and interface_stats.isup
            )

            interface_type = classify_interface(
                interface_name,
                descriptions.get(interface_name, ""),
            )

            results.append({
                "name": interface_name,
                "interface": interface_name,
                "type": interface_type,
                "ip": ip_address,
                "source_ip": ip_address,
                "up": is_up,
                "is_up": is_up,
            })

    return results


# ============================================================
# TRAFFIC MONITORING
# ============================================================

def get_traffic():
    """Return per-interface traffic counters and rates."""

    global traffic_previous

    counters = psutil.net_io_counters(pernic=True)
    now = time.monotonic()

    results = []

    for interface_name, counter in counters.items():
        previous = traffic_previous.get(interface_name)

        download_rate = 0.0
        upload_rate = 0.0

        if previous:
            previous_time, previous_recv, previous_sent = previous
            elapsed = now - previous_time

            if elapsed > 0:
                download_rate = max(
                    0.0,
                    (counter.bytes_recv - previous_recv)
                    / elapsed
                    / 1024,
                )

                upload_rate = max(
                    0.0,
                    (counter.bytes_sent - previous_sent)
                    / elapsed
                    / 1024,
                )

        traffic_previous[interface_name] = (
            now,
            counter.bytes_recv,
            counter.bytes_sent,
        )

        results.append({
            "name": interface_name,
            "interface": interface_name,
            "received_bytes": counter.bytes_recv,
            "sent_bytes": counter.bytes_sent,
            "bytes_recv": counter.bytes_recv,
            "bytes_sent": counter.bytes_sent,
            "download_kbps": round(download_rate, 2),
            "upload_kbps": round(upload_rate, 2),
            "rx": round(download_rate, 2),
            "tx": round(upload_rate, 2),
        })

    return results


# ============================================================
# NETWORK SELECTION
# ============================================================

def select_preferred_path(paths):
    """Prefer Wi-Fi, then USB tethering, then other online paths."""

    online_paths = [
        path
        for path in paths
        if (path.get("connectivity") or {}).get("connected")
    ]

    if not online_paths:
        return None

    def priority(path):
        interface_type = str(path.get("type", "")).lower()
        interface_name = str(
            path.get("interface", path.get("name", ""))
        ).lower()

        if interface_type == "wi-fi" or "wi-fi" in interface_name:
            return 0

        if (
            "usb tethering" in interface_type
            or "usb" in interface_name
            or interface_name.endswith(" 2")
        ):
            return 1

        return 2

    return sorted(online_paths, key=priority)[0]


# ============================================================
# FAILOVER MONITORING
# ============================================================

def refresh_failover():
    """Test all network paths and update the shared status."""

    global failover_state

    try:
        discovered = check_all_network_paths()

        if isinstance(discovered, dict):
            paths = discovered.get("paths", [])
            checked_at = discovered.get("checked_at") or current_time()
        else:
            paths = discovered
            checked_at = current_time()

        if not isinstance(paths, list):
            paths = []

        selected = select_preferred_path(paths)

        old_ip = failover_state["active_source_ip"]

        if selected:
            new_ip = (
                selected.get("source_ip")
                or selected.get("ip")
            )

            new_interface = (
                selected.get("interface")
                or selected.get("name")
            )

            if old_ip != new_ip:
                failover_state["previous_source_ip"] = old_ip

                if old_ip is None:
                    event = (
                        f"Active path selected: "
                        f"{new_interface} ({new_ip})"
                    )
                else:
                    event = (
                        f"Preferred path changed to "
                        f"{new_interface} ({new_ip})"
                    )
            else:
                event = (
                    f"Active path remains "
                    f"{new_interface} ({new_ip})"
                )

            failover_state["active_source_ip"] = new_ip
            failover_state["active_interface"] = new_interface
            failover_state["last_event"] = event

        else:
            if old_ip is not None:
                failover_state["previous_source_ip"] = old_ip

            failover_state["active_source_ip"] = None
            failover_state["active_interface"] = None
            failover_state["last_event"] = (
                "No tested network path currently has internet access."
            )

        failover_state["last_checked"] = checked_at

        # IMPORTANT: Save connectivity results so the frontend
        # can display online status and latency.
        failover_state["network_results"] = {
            "paths": paths,
            "checked_at": checked_at,
        }

    except Exception as exc:
        failover_state["last_checked"] = current_time()
        failover_state["last_event"] = (
            f"Network check failed: {exc}"
        )

    return dict(failover_state)


async def failover_monitor():
    failover_state["monitor_running"] = True

    try:
        while True:
            try:
                await asyncio.to_thread(refresh_failover)
            except Exception as exc:
                failover_state["last_event"] = (
                    f"Monitor error: {exc}"
                )

            await asyncio.sleep(MONITOR_INTERVAL)

    except asyncio.CancelledError:
        raise

    finally:
        failover_state["monitor_running"] = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    monitor_task = asyncio.create_task(failover_monitor())

    try:
        yield
    finally:
        monitor_task.cancel()

        try:
            await monitor_task
        except asyncio.CancelledError:
            pass


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="NetFusion",
    description="Network monitoring and failover dashboard",
    version="0.5.0",
    lifespan=lifespan,
)


# ============================================================
# STATIC FILES
# ============================================================

# Mount the directories only when they exist.
# Mount CSS and JS at the exact URLs used by index.html.

if STATIC_DIR.is_dir():
    app.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )

if CSS_DIR.is_dir():
    app.mount(
        "/css",
        StaticFiles(directory=str(CSS_DIR)),
        name="css",
    )

if JS_DIR.is_dir():
    app.mount(
        "/js",
        StaticFiles(directory=str(JS_DIR)),
        name="js",
    )


# ============================================================
# HOME PAGE
# ============================================================

@app.get("/")
def home():
    index_file = STATIC_DIR / "index.html"

    if index_file.is_file():
        return FileResponse(index_file)

    return {
        "application": "NetFusion",
        "version": "0.5.0",
        "error": "static/index.html was not found",
        "expected_path": str(index_file),
    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "application": "NetFusion",
        "version": "0.5.0",
        "monitor_running": failover_state["monitor_running"],
        "timestamp": current_time(),
    }


# ============================================================
# INTERFACES API
# ============================================================

@app.get("/interfaces")
def interfaces():
    return get_interfaces()


# ============================================================
# TRAFFIC API
# ============================================================

@app.get("/traffic")
def traffic():
    return get_traffic()


# ============================================================
# FAILOVER API
# ============================================================

@app.get("/failover/status")
def failover_status():
    return dict(failover_state)


@app.get("/failover/refresh")
def failover_refresh_get():
    return refresh_failover()


@app.post("/failover/refresh")
def failover_refresh_post():
    return refresh_failover()


# ============================================================
# CONNECTION TEST API
# ============================================================

class MultipathTransferRequest(BaseModel):
    file_path: str
    host: str
    port: int = 9000
    source_ips: list[str] | None = None


def run_multipath_transfer(job_id, request):
    def update_progress(value):
        with multipath_lock:
            multipath_jobs[job_id].update(value)

    with multipath_lock:
        multipath_jobs[job_id]["status"] = "running"
        multipath_jobs[job_id]["started_at"] = current_time()
    try:
        result = send_multipath_file(
            request.file_path,
            request.host,
            request.port,
            request.source_ips,
            progress=update_progress,
        )
        with multipath_lock:
            multipath_jobs[job_id].update({
                "status": "completed",
                "completed_at": current_time(),
                "result": result,
            })
    except Exception as exc:
        with multipath_lock:
            multipath_jobs[job_id].update({
                "status": "failed",
                "completed_at": current_time(),
                "error": str(exc),
            })


@app.post("/multipath/transfers", status_code=202)
def start_multipath_transfer(request: MultipathTransferRequest):
    if not request.file_path.strip() or not request.host.strip():
        raise HTTPException(status_code=400, detail="File path and receiver address are required.")
    if not 1 <= request.port <= 65535:
        raise HTTPException(status_code=400, detail="Port must be between 1 and 65535.")
    job_id = str(uuid.uuid4())
    with multipath_lock:
        multipath_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "file_path": request.file_path,
            "host": request.host,
            "port": request.port,
            "created_at": current_time(),
        }
    threading.Thread(
        target=run_multipath_transfer,
        args=(job_id, request),
        daemon=True,
        name=f"netfusion-transfer-{job_id[:8]}",
    ).start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/multipath/transfers/{job_id}")
def multipath_transfer_status(job_id: str):
    with multipath_lock:
        job = multipath_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Transfer job not found.")
        return dict(job)


# ============================================================
# CONNECTION TEST API
# ============================================================

class ConnectionTestRequest(BaseModel):
    source_ip: str


@app.post("/test-connection")
def test_connection(request: ConnectionTestRequest):
    source_ip = request.source_ip

    started = time.perf_counter()

    try:
        with socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM,
        ) as sock:
            sock.settimeout(5)
            sock.bind((source_ip, 0))
            sock.connect(("1.1.1.1", 443))

        latency_ms = (
            time.perf_counter() - started
        ) * 1000

        return {
            "source_ip": source_ip,
            "connected": True,
            "latency_ms": round(latency_ms, 2),
            "message": "Internet connection successful",
        }

    except OSError as exc:
        return {
            "source_ip": source_ip,
            "connected": False,
            "latency_ms": None,
            "message": str(exc),
        }


# ============================================================
# SPEED TEST WORKER
# ============================================================

def run_speedtest(job_id):
    with speedtest_lock:
        speedtest_jobs[job_id]["status"] = "running"

    try:
        import speedtest

        tester = speedtest.Speedtest()
        tester.get_best_server()

        download_mbps = tester.download() / 1_000_000
        upload_mbps = tester.upload() / 1_000_000

        result = {
            "download_mbps": round(download_mbps, 2),
            "upload_mbps": round(upload_mbps, 2),
            "ping_ms": round(tester.results.ping, 2),
            "server": tester.results.server.get("name"),
            "server_country": tester.results.server.get("country"),
        }

        with speedtest_lock:
            speedtest_jobs[job_id]["status"] = "completed"
            speedtest_jobs[job_id]["result"] = result
            speedtest_jobs[job_id]["finished_at"] = current_time()

    except Exception as exc:
        with speedtest_lock:
            speedtest_jobs[job_id]["status"] = "failed"
            speedtest_jobs[job_id]["error"] = str(exc)
            speedtest_jobs[job_id]["finished_at"] = current_time()


# ============================================================
# SPEED TEST API
# ============================================================

@app.post("/speedtests")
def start_speedtest():
    job_id = str(uuid.uuid4())

    with speedtest_lock:
        speedtest_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "created_at": current_time(),
            "result": None,
            "error": None,
        }

    worker = threading.Thread(
        target=run_speedtest,
        args=(job_id,),
        daemon=True,
    )

    worker.start()

    return {
        "job_id": job_id,
        "status": "queued",
    }


@app.get("/speedtests")
def list_speedtests():
    with speedtest_lock:
        return list(speedtest_jobs.values())


@app.get("/speedtests/{job_id}")
def get_speedtest(job_id: str):
    with speedtest_lock:
        job = speedtest_jobs.get(job_id)

        if job is None:
            raise HTTPException(
                status_code=404,
                detail="Speed test job not found",
            )

        return dict(job)


# ============================================================
# DIRECT EXECUTION
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
