const CFG = {
    base: window.location.origin,
    interval: 3000,
    paths: {
        interfaces: "/interfaces",
        traffic: "/traffic",
        speedtests: "/speedtests",
        failoverStatus: "/failover/status",
        failoverRefresh: "/failover/refresh"
    },
    sourceField: "source_ip"
};

async function api(path, options = {}) {
    const response = await fetch(`${CFG.base}${path}`, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {})
        }
    });

    if (!response.ok) {
        throw new Error(`API error ${response.status}: ${path}`);
    }

    return response.json();
}

function num(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function list(value) {
    if (Array.isArray(value)) return value;
    if (Array.isArray(value?.interfaces)) return value.interfaces;
    if (Array.isArray(value?.paths)) return value.paths;
    if (Array.isArray(value?.results)) return value.results;
    return [];
}

function normIface(item) {
    return {
        ...item,
        name: item.name || item.interface || "Unknown",
        interface: item.interface || item.name || "Unknown",
        type: item.type || "Other",
        ip: item.ip || item.source_ip || "",
        source_ip: item.source_ip || item.ip || "",
        up: Boolean(item.up ?? item.is_up ?? item.connected),
        rx: item.rx ?? item.download_kbps ?? null,
        tx: item.tx ?? item.upload_kbps ?? null
    };
}

async function fetchState() {
    const [interfaces, traffic] = await Promise.all([
        api(CFG.paths.interfaces),
        api("/traffic")
    ]);

    const trafficByName = new Map(
        list(traffic).map(item => [
            item.name || item.interface,
            item
        ])
    );

    return list(interfaces).map(item => {
        const iface = normIface(item);
        const trafficItem = trafficByName.get(iface.name);

        return {
            ...iface,
            rx: trafficItem?.rx ?? trafficItem?.download_kbps ?? null,
            tx: trafficItem?.tx ?? trafficItem?.upload_kbps ?? null
        };
    });
}

async function getFailoverStatus() {
    return api(CFG.paths.failoverStatus);
}

async function refreshFailover() {
    return api(CFG.paths.failoverRefresh, {
        method: "POST"
    });
}

async function startTest(sourceIp) {
    return api("/test-connection", {
        method: "POST",
        body: JSON.stringify({
            source_ip: sourceIp
        })
    });
}

async function getTest(jobId) {
    return api(`/speedtests/${encodeURIComponent(jobId)}`);
}

async function listTests() {
    return api(CFG.paths.speedtests);
}

async function startMultipathTransfer(payload) {
    return api("/multipath/transfers", {
        method: "POST",
        body: JSON.stringify(payload)
    });
}

async function getMultipathTransfer(jobId) {
    return api(`/multipath/transfers/${encodeURIComponent(jobId)}`);
}
