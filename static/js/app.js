// ========================================================
// NETFUSION DASHBOARD
// ========================================================

const PAGES = [
    "Overview",
    "Connections",
    "Speed Tests",
    "Traffic Analytics",
    "Network Health",
    "Bonding Lab",
    "Settings"
];

let page = "Overview";
let ifs = [];
let sel = "all";
let pollT = null;
let failover = null;
let bondingForm = { filePath: "", host: "", port: "9000", sourceIPs: [] };
let transferState = null;
let transferPollActive = false;

const $ = selector => document.querySelector(selector);
const view = $("#view");


// ========================================================
// DISPLAY HELPERS
// ========================================================

const fm = value => {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return "—";
    }

    return Number(value).toFixed(2);
};


const esc = value =>
    String(value ?? "").replace(/[&<>"']/g, character => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    })[character]);


function stat(label, value, colour = "", unit = "") {
    return `
        <div class="card">
            <h3>${esc(label)}</h3>
            <div class="big ${colour}">
                ${value}
                <small>${esc(unit)}</small>
            </div>
        </div>
    `;
}


function statusPill(online, label) {
    return `
        <span class="pill ${online ? "ok" : "bad"}">
            <i></i>${esc(label)}
        </span>
    `;
}


// ========================================================
// NAVIGATION
// ========================================================

$("#nav").innerHTML = PAGES.map(name =>
    `<button data-p="${esc(name)}">${esc(name)}</button>`
).join("");


$("#nav").onclick = event => {
    const button = event.target.closest("button[data-p]");

    if (!button) return;

    go(button.dataset.p);
    $("#side").classList.remove("open");
};


$("#menu").onclick = () =>
    $("#side").classList.toggle("open");


$("#refresh").onclick = async () => {
    try {
        await refreshFailover();
    } catch (error) {
        console.error("Immediate network refresh failed:", error);
    }

    await tick();
};


function go(nextPage) {
    page = nextPage;

    $("#title").textContent = page;

    document.querySelectorAll("#nav button").forEach(button => {
        button.classList.toggle(
            "on",
            button.dataset.p === page
        );
    });

    render();
}


// ========================================================
// NETWORK INTERFACE CARDS
// ========================================================

function ifCard(iface) {
    const interfaceIPs = iface.ips.length
        ? iface.ips.map(esc).join(", ")
        : "No IPv4 address";

    const isActive =
        failover &&
        failover.active_source_ip &&
        iface.ips.includes(failover.active_source_ip);

    const connectivity = iface.internetOnline;

    return `
        <div class="card ${iface.up ? "up" : "down"}">

            <div class="row">
                <b>${esc(iface.name)}</b>
                ${statusPill(
                    iface.up,
                    iface.up ? "Interface Up" : "Interface Down"
                )}
            </div>

            <div class="row">
                <span>Interface type</span>
                <span class="tag">${esc(iface.type)}</span>
            </div>

            <div class="row">
                <span>IPv4 address</span>
                <span>${interfaceIPs}</span>
            </div>

            <div class="row">
                <span>Internet connectivity</span>
                ${
                    connectivity === true
                        ? statusPill(true, "Online")
                        : connectivity === false
                            ? statusPill(false, "Unverified")
                            : '<span class="muted">Checking…</span>'
                }
            </div>

            <div class="row">
                <span>Connection time</span>
                <span>
                    ${
                        iface.connectionTimeMs == null
                            ? "—"
                            : `${fm(iface.connectionTimeMs)} ms`
                    }
                </span>
            </div>

            <div class="row">
                <span>Reported link speed</span>
                <span>
                    ${
                        iface.speed
                            ? `${fm(iface.speed)} Mbps`
                            : "Not reported"
                    }
                </span>
            </div>

            <div class="row">
                <span>Download traffic</span>
                <span class="dl">${fm(iface.rx)} Mbps</span>
            </div>

            <div class="row">
                <span>Upload traffic</span>
                <span class="ul">${fm(iface.tx)} Mbps</span>
            </div>

            ${
                isActive
                    ? '<div class="note">Currently selected by NetFusion</div>'
                    : ""
            }

        </div>
    `;
}


// ========================================================
// OVERVIEW
// ========================================================

function overviewPage() {
    const up = ifs.filter(iface => iface.up);

    const download = up.some(iface => iface.rx !== null)
        ? up.reduce((total, iface) => total + (iface.rx || 0), 0)
        : null;

    const upload = up.some(iface => iface.tx !== null)
        ? up.reduce((total, iface) => total + (iface.tx || 0), 0)
        : null;

    const activeName = failover?.active_interface || "Not selected";

    const activeIP = failover?.active_source_ip || "—";

    view.innerHTML = `
        <div class="grid">
            ${stat("Detected interfaces", ifs.length)}
            ${stat("Interfaces up", up.length, "dl")}
            ${stat("Observed download", fm(download), "dl", "Mbps")}
            ${stat("Observed upload", fm(upload), "ul", "Mbps")}
        </div>

        <div class="card">
            <h2>Automatic Network Manager</h2>

            <div class="row">
                <span>Active interface</span>
                <b>${esc(activeName)}</b>
            </div>

            <div class="row">
                <span>Active local IP</span>
                <b>${esc(activeIP)}</b>
            </div>

            <div class="row">
                <span>Background monitor</span>
                ${
                    failover?.monitor_running
                        ? statusPill(true, "Running")
                        : statusPill(false, "Not running")
                }
            </div>

            <div class="row">
                <span>Last network check</span>
                <span>${esc(
                    failover?.last_checked
                        ? new Date(
                            typeof failover.last_checked === "number"
                                ? (failover.last_checked < 1e12
                                    ? failover.last_checked * 1000
                                    : failover.last_checked)
                                : failover.last_checked
                        ).toLocaleTimeString()
                        : "Waiting for first check"
                )}</span>
            </div>

            <div class="row">
                <span>Last event</span>
                <span>${esc(failover?.last_event || "No events yet")}</span>
            </div>

            <button class="pri" id="forceRefresh">
                Check Connections Now
            </button>

            <p class="muted">
                Network traffic shown here is observed interface traffic,
                not guaranteed internet throughput or bonded throughput.
            </p>
        </div>

        <h2>Detected Connections</h2>

        <div class="grid">
            ${
                ifs.map(ifCard).join("") ||
                '<div class="card">Discovering network interfaces…</div>'
            }
        </div>
    `;

    $("#forceRefresh").onclick = async event => {
        const button = event.currentTarget;

        button.disabled = true;
        button.textContent = "Checking…";

        try {
            await refreshFailover();
        } catch (error) {
            console.error(error);
        }

        await tick();

        if (button.isConnected) {
            button.disabled = false;
            button.textContent = "Check Connections Now";
        }
    };
}


// ========================================================
// CONNECTIONS PAGE
// ========================================================

function connectionsPage() {
    view.innerHTML = `
        <div class="card">
            <h2>Discovered Network Interfaces</h2>
            <p class="muted">
                Addresses are discovered from Windows network interfaces.
                They are not hardcoded.
            </p>
        </div>

        <div class="grid">
            ${
                ifs.map(ifCard).join("") ||
                '<div class="card">No interfaces found.</div>'
            }
        </div>
    `;
}


// ========================================================
// SPEED TEST PAGE
// ========================================================

async function speedPage() {
    if (pollT) {
        clearInterval(pollT);
        pollT = null;
    }

    const usable = ifs.filter(
        iface => iface.up && iface.ips.length
    );

    view.innerHTML = `
        <div class="card">
            <h3>Run a Speed Test</h3>

            <p class="muted">
                Select an interface to test that connection independently.
            </p>

            <select id="tsel">
                ${
                    usable.map(iface => `
                        <option value="${esc(iface.ips[0])}">
                            ${esc(iface.name)} (${esc(iface.ips[0])})
                        </option>
                    `).join("") ||
                    '<option value="">No interface with an IPv4 address</option>'
                }
            </select>

            <button class="pri" id="startSpeed" ${usable.length ? "" : "disabled"}>
                Start Test
            </button>

            <div id="run" class="muted" style="margin-top:12px"></div>
        </div>

        <div class="card">
            <h3>Previous Results</h3>
            <div id="hist" class="muted">Loading…</div>
        </div>
    `;

    $("#startSpeed").onclick = async () => {
        const button = $("#startSpeed");
        const output = $("#run");

        button.disabled = true;
        output.textContent = "Starting speed test…";

        try {
            const job = await startTest($("#tsel").value);
            const id = job.job_id ?? job.id;

            pollT = setInterval(async () => {
                try {
                    const result = await getTest(id);
                    const status = (result.status || "").toLowerCase();

                    if (["completed", "done", "finished", "success"].includes(status)) {
                        clearInterval(pollT);
                        pollT = null;

                        output.innerHTML = speedResult(
                            result.result || result
                        );

                        button.disabled = false;
                        loadHist();
                    } else if (["failed", "error"].includes(status)) {
                        clearInterval(pollT);
                        pollT = null;

                        output.textContent =
                            "Test failed: " +
                            (result.error || "Unknown error");

                        button.disabled = false;
                    } else {
                        output.textContent =
                            `Speed test status: ${status || "running"}…`;
                    }
                } catch (error) {
                    clearInterval(pollT);
                    pollT = null;

                    output.textContent = error.message;
                    button.disabled = false;
                }
            }, 1500);
        } catch (error) {
            output.textContent = error.message;
            button.disabled = false;
        }
    };

    await loadHist();
}


function speedResult(result) {
    return `
        <span class="dl">
            ↓ ${fm(num(result.download_mbps ?? result.download))} Mbps
        </span>
        ·
        <span class="ul">
            ↑ ${fm(num(result.upload_mbps ?? result.upload))} Mbps
        </span>
        ·
        ${fm(num(result.latency_ms ?? result.ping ?? result.latency))} ms
    `;
}


async function loadHist() {
    const element = $("#hist");

    if (!element) return;

    try {
        const tests = await listTests();

        element.innerHTML = tests.length
            ? `
                <table>
                    <tr>
                        <th>Interface</th>
                        <th>Status</th>
                        <th>Result</th>
                    </tr>
                    ${tests.map(test => `
                        <tr>
                            <td>${esc(test.interface || test.source_ip || "—")}</td>
                            <td>${esc(test.status || "—")}</td>
                            <td>
                                ${
                                    test.status === "completed"
                                        ? speedResult(test.result || test)
                                        : esc(test.error || test.status || "Pending")
                                }
                            </td>
                        </tr>
                    `).join("")}
                </table>
            `
            : "No speed tests yet.";
    } catch (error) {
        element.textContent = error.message;
    }
}


// ========================================================
// TRAFFIC ANALYTICS
// ========================================================

function trafficPage() {
    view.innerHTML = `
        <div class="card">
            <div class="row">
                <b>Live Interface Traffic</b>

                <select id="isel">
                    <option value="all">All active interfaces</option>
                    ${
                        ifs.map(iface => `
                            <option value="${esc(iface.name)}"
                                ${iface.name === sel ? "selected" : ""}>
                                ${esc(iface.name)}
                            </option>
                        `).join("")
                    }
                </select>
            </div>

            <div class="chartbox">
                <canvas id="cv"></canvas>
            </div>

            <div id="empty" class="muted">
                Collecting traffic samples…
            </div>
        </div>
    `;

    $("#isel").onchange = event => {
        sel = event.target.value;
        resetHist();
    };

    mountChart($("#cv"));
}


// ========================================================
// NETWORK HEALTH
// ========================================================

function healthPage() {
    view.innerHTML = `
        <div class="card">
            <h2>Network Health</h2>

            <div class="row">
                <span>Backend</span>
                ${statusPill(true, "Online")}
            </div>

            <div class="row">
                <span>Background monitor</span>
                ${
                    failover?.monitor_running
                        ? statusPill(true, "Running")
                        : statusPill(false, "Stopped")
                }
            </div>

            <div class="row">
                <span>Active connection</span>
                <span>${esc(failover?.active_interface || "None")}</span>
            </div>

            <div class="row">
                <span>Active IP</span>
                <span>${esc(failover?.active_source_ip || "—")}</span>
            </div>

            <div class="row">
                <span>Last event</span>
                <span>${esc(failover?.last_event || "None")}</span>
            </div>

            <button class="pri" id="healthRefresh">
                Run Connectivity Check
            </button>
        </div>
    `;

    $("#healthRefresh").onclick = async () => {
        try {
            await refreshFailover();
        } catch (error) {
            alert(error.message);
        }

        await tick();
    };
}


// ========================================================
// BONDING LAB
// ========================================================

function bondingPage() {
    const candidates = ifs.filter(iface => iface.up && iface.ips.length);
    const defaults = candidates.filter(iface => ["Wi-Fi", "USB Tethering"].includes(iface.type));
    if (!bondingForm.sourceIPs.length && defaults.length) {
        bondingForm.sourceIPs = defaults.flatMap(iface => iface.ips);
    }
    const activePaths = candidates.flatMap(iface => iface.ips.map(ip => ({
        name: iface.name, type: iface.type, ip
    })));
    const currentJob = transferState;
    const result = currentJob?.result;
    view.innerHTML = `
        <div class="card">
            <h2>Bandwidth Bonding Lab</h2>
            <p>Choose a file and send its chunks over multiple active interfaces to a NetFusion receiver.</p>
            <div class="row"><label for="bondFile">File path on this laptop</label><input id="bondFile" type="text" placeholder="C:\\path\\to\\file.zip" value="${esc(bondingForm.filePath)}" style="width:min(62%,520px)"></div>
            <div class="row"><label for="bondHost">Receiver address</label><input id="bondHost" type="text" placeholder="Receiver IPv4 or hostname" value="${esc(bondingForm.host)}" style="width:min(62%,520px)"></div>
            <div class="row"><label for="bondPort">UDP port</label><input id="bondPort" type="number" min="1" max="65535" value="${esc(bondingForm.port)}" style="width:150px"></div>
            <h3>Transfer paths</h3>
            ${activePaths.length ? activePaths.map(path => `
                <label class="row" style="justify-content:flex-start;gap:12px">
                    <input type="checkbox" data-source-ip="${esc(path.ip)}" ${bondingForm.sourceIPs.includes(path.ip) ? "checked" : ""}>
                    <span>${esc(path.name)} · ${esc(path.type)} · ${esc(path.ip)}</span>
                    <span>${ifs.find(iface => iface.name === path.name)?.internetOnline === true ? "Internet online" : "Link active"}</span>
                </label>`).join("") : '<p class="muted">No active IPv4 paths detected.</p>'}
            <button class="pri" id="startMultipath" ${currentJob?.status === "running" || currentJob?.status === "queued" ? "disabled" : ""}>Start reliable transfer</button>
            <div id="transferStatus" class="note" style="margin-top:14px">
                ${currentJob ? transferSummary(currentJob) : "Receiver command: python -m tunnel.multipath receive --host 0.0.0.0 --port 9000 --output-dir received"}
            </div>
            ${result ? `<div class="card"><h3>Verified transfer</h3><div class="row"><span>SHA-256</span><code>${esc(result.sha256)}</code></div>${result.paths.map(path => `<div class="row"><span>${esc(path.interface)} · ${esc(path.source_ip)}</span><span>${esc(path.chunks_sent)} datagrams sent</span></div>`).join("")}<div class="row"><span>Delivered</span><b>${fm(result.bytes_sent / 1048576)} MiB · ${fm(result.throughput_mbps)} Mbps</b></div><div class="row"><span>Retries</span><b>${esc(result.retransmissions)}</b></div></div>` : ""}
            <p class="note">Each chunk is acknowledged and retried if needed; the receiver verifies the complete file checksum. This sends only to the receiver you specify. The receiver has no authentication or encryption yet, so use a trusted network and do not expose it publicly. NetFusion does not change Windows routing or firewall settings.</p>
        </div>
    `;

    $("#bondFile").oninput = event => bondingForm.filePath = event.target.value;
    $("#bondHost").oninput = event => bondingForm.host = event.target.value;
    $("#bondPort").oninput = event => bondingForm.port = event.target.value;
    view.querySelectorAll("[data-source-ip]").forEach(input => {
        input.onchange = () => {
            const ip = input.dataset.sourceIp;
            bondingForm.sourceIPs = input.checked
                ? [...new Set([...bondingForm.sourceIPs, ip])]
                : bondingForm.sourceIPs.filter(value => value !== ip);
        };
    });
    $("#startMultipath").onclick = startTransferFromDashboard;
}

function transferSummary(job) {
    if (job.status === "failed") return `Transfer failed: ${esc(job.error || "Unknown error")}`;
    if (job.status === "completed") return "Receiver confirmed the complete file checksum.";
    const progress = job.total_chunks
        ? ` · ${job.acknowledged_chunks || 0}/${job.total_chunks} chunks acknowledged`
        : "";
    return `Transfer ${esc(job.status)}${progress}`;
}

async function startTransferFromDashboard() {
    if (!bondingForm.filePath.trim() || !bondingForm.host.trim()) {
        alert("Enter a file path and the receiver address.");
        return;
    }
    if (!bondingForm.sourceIPs.length) {
        alert("Select at least one active transfer path.");
        return;
    }
    try {
        const started = await startMultipathTransfer({
            file_path: bondingForm.filePath.trim(),
            host: bondingForm.host.trim(),
            port: Number(bondingForm.port),
            source_ips: bondingForm.sourceIPs
        });
        transferState = { job_id: started.job_id, status: started.status };
        render();
        pollTransfer(started.job_id);
    } catch (error) {
        transferState = { status: "failed", error: error.message };
        render();
    }
}

async function pollTransfer(jobId) {
    if (transferPollActive) return;
    transferPollActive = true;
    try {
        while (transferState?.job_id === jobId && ["queued", "running"].includes(transferState.status)) {
            await new Promise(resolve => setTimeout(resolve, 700));
            transferState = await getMultipathTransfer(jobId);
            if (page === "Bonding Lab") render();
        }
    } catch (error) {
        transferState = { job_id: jobId, status: "failed", error: error.message };
        if (page === "Bonding Lab") render();
    } finally {
        transferPollActive = false;
    }
}


// ========================================================
// SETTINGS
// ========================================================

function settingsPage() {
    view.innerHTML = `
        <div class="card">
            <h2>Settings</h2>

            <label>
                API base URL
                <br>
                <input id="base" size="36" value="${esc(CFG.base)}">
            </label>

            <br><br>

            <label>
                Refresh interval (milliseconds)
                <br>
                <input
                    id="iv"
                    type="number"
                    min="1000"
                    value="${CFG.interval}"
                >
            </label>

            <br><br>

            <button class="pri" id="save">Save Settings</button>
        </div>
    `;

    $("#save").onclick = () => {
        const interval = Math.max(
            1000,
            Number($("#iv").value) || 3000
        );

        localStorage.setItem(
            "nf_cfg",
            JSON.stringify({
                base: $("#base").value.trim(),
                interval
            })
        );

        location.reload();
    };
}


// ========================================================
// PAGE RENDERING
// ========================================================

function render() {
    if (pollT) {
        clearInterval(pollT);
        pollT = null;
    }

    if (page === "Overview") {
        overviewPage();
    } else if (page === "Connections") {
        connectionsPage();
    } else if (page === "Speed Tests") {
        speedPage();
    } else if (page === "Traffic Analytics") {
        trafficPage();
    } else if (page === "Network Health") {
        healthPage();
    } else if (page === "Bonding Lab") {
        bondingPage();
    } else if (page === "Settings") {
        settingsPage();
    }
}


// ========================================================
// DASHBOARD STATUS
// ========================================================

function setStatus(online, message = "") {
    const element = $("#status");

    element.className = "pill " + (online ? "ok" : "bad");

    const label = element.querySelector("span");

    if (label) {
        label.textContent = online
            ? "Backend online"
            : "Backend offline";
    }

    $("#banner").hidden = online;

    if (!online) {
        $("#banner").textContent =
            `Cannot reach ${CFG.base}: ${message}. Retrying automatically.`;
    }
}


// ========================================================
// AUTOMATIC REFRESH
// ========================================================

let ticking = false;


async function tick() {
    if (ticking) return;

    ticking = true;

    try {
        ifs = await fetchState();

        // Normalize IPv4 addresses for every interface. The API may return
        // `ip` or `source_ip` without an `ips` array; page renderers use ips.
        ifs = (Array.isArray(ifs) ? ifs : []).map(iface => {
            const ips = Array.isArray(iface.ips)
                ? iface.ips
                : [iface.ip || iface.source_ip].filter(Boolean);

            return {
                ...iface,
                ips,
                up: Boolean(iface.up ?? iface.is_up),
                rx: iface.rx ?? iface.download_kbps ?? null,
                tx: iface.tx ?? iface.upload_kbps ?? null
            };
        });

        // The backend runs its own background connectivity checks.
        failover = await getFailoverStatus();

        // Apply the latest per-path connectivity results.
        const results = failover.network_results?.paths || [];

        ifs.forEach(iface => {
            const result = results.find(
                item => item.interface === iface.name
            );

            iface.internetOnline =
                result?.connectivity?.connected ?? null;

            iface.connectionTimeMs =
                result?.connectivity?.latency_ms ?? null;
        });

        setStatus(true);

        $("#upd").textContent =
            "Updated " + new Date().toLocaleTimeString();

        pushSample(ifs, sel);

        if (page === "Traffic Analytics" && chart) {
            updateChart();

            const empty = $("#empty");

            if (empty) {
                empty.hidden = hist.labels.length > 0;
            }
        } else if (
            [
                "Overview",
                "Connections",
                "Network Health",
                "Bonding Lab"
            ].includes(page)
        ) {
            render();
        }
    } catch (error) {
        setStatus(false, error.message);
    } finally {
        ticking = false;
    }
}


// ========================================================
// START
// ========================================================

go("Overview");

tick();

setInterval(tick, CFG.interval);
