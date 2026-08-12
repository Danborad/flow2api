let ws = null;
let reconnectTimeout = null;
let heartbeatInterval = null;
let accountImportInProgress = false;
const EXTENSION_VERSION = chrome.runtime.getManifest().version;

console.log(`[Flow2API] Captcha Worker v${EXTENSION_VERSION} loaded`);

const ACCOUNT_IMPORT_ALARM = "flow2api-auto-import-account";
const LABS_SESSION_COOKIE = "__Secure-next-auth.session-token";
const GOOGLE_COOKIE_NAMES = [
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "__Secure-1PSID",
    "__Secure-3PSID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
    "__Secure-1PSIDTS",
    "__Secure-3PSIDTS",
    "__Secure-1PSIDCC",
    "__Secure-3PSIDCC"
];
const GOOGLE_AUTH_COOKIE_GROUPS = [
    ["SID", "SAPISID"],
    ["__Secure-1PSID", "__Secure-1PAPISID"],
    ["__Secure-3PSID", "__Secure-3PAPISID"]
];

const DEFAULT_SETTINGS = {
    serverUrl: "ws://127.0.0.1:8000/captcha_ws",
    apiKey: "",
    instanceId: "",
    routeKey: "",
    clientLabel: "",
    refreshIntervalMinutes: "120",
    autoImportEnabled: true,
    autoImportIntervalMinutes: "30"
};

function createInstanceId() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
        return globalThis.crypto.randomUUID();
    }
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}

function ensureInstanceSettings(stored) {
    const instanceId = stored.instanceId || createInstanceId();
    const shortId = instanceId.replace(/-/g, "").slice(0, 12);
    const routeKey = stored.instanceId ? (stored.routeKey || `flow-${shortId}`) : `flow-${shortId}`;
    const clientLabel = stored.instanceId ? (stored.clientLabel || `chrome-${shortId}`) : `chrome-${shortId}`;
    const settings = { instanceId, routeKey, clientLabel };
    if (!stored.instanceId || !stored.routeKey || !stored.clientLabel) {
        chrome.storage.local.set(settings);
    }
    return settings;
}

function getSettings() {
    return new Promise((resolve) => {
        chrome.storage.local.get(DEFAULT_SETTINGS, (stored) => {
            const instanceSettings = ensureInstanceSettings(stored);
            resolve({
                serverUrl: (stored.serverUrl || DEFAULT_SETTINGS.serverUrl).trim(),
                apiKey: (stored.apiKey || DEFAULT_SETTINGS.apiKey).trim(),
                instanceId: instanceSettings.instanceId,
                routeKey: instanceSettings.routeKey.trim(),
                clientLabel: instanceSettings.clientLabel.trim(),
                refreshIntervalMinutes: String(stored.refreshIntervalMinutes || DEFAULT_SETTINGS.refreshIntervalMinutes).trim(),
                autoImportEnabled: stored.autoImportEnabled !== false,
                autoImportIntervalMinutes: String(stored.autoImportIntervalMinutes || DEFAULT_SETTINGS.autoImportIntervalMinutes).trim()
            });
        });
    });
}

function getBackendBaseUrl(serverUrl) {
    const url = new URL(serverUrl || DEFAULT_SETTINGS.serverUrl);
    if (url.protocol === "ws:") {
        url.protocol = "http:";
    } else if (url.protocol === "wss:") {
        url.protocol = "https:";
    } else {
        throw new Error("WebSocket URL must start with ws:// or wss://");
    }
    url.pathname = "";
    url.search = "";
    url.hash = "";
    return url.toString().replace(/\/$/, "");
}

function getCookie(details) {
    return new Promise((resolve, reject) => {
        chrome.cookies.get(details, (cookie) => {
            if (chrome.runtime.lastError) {
                reject(new Error(chrome.runtime.lastError.message));
                return;
            }
            resolve(cookie || null);
        });
    });
}

function getCookies(details) {
    return new Promise((resolve, reject) => {
        chrome.cookies.getAll(details, (cookies) => {
            if (chrome.runtime.lastError) {
                reject(new Error(chrome.runtime.lastError.message));
                return;
            }
            resolve(cookies || []);
        });
    });
}

async function getLabsSessionToken() {
    const urls = [
        "https://labs.google/fx",
        "https://labs.google/fx/tools/flow",
        "https://labs.google/"
    ];
    for (const url of urls) {
        const cookie = await getCookie({ url, name: LABS_SESSION_COOKIE });
        if (cookie && cookie.value) return cookie.value;
    }
    return "";
}

async function refreshLabsSessionCookie() {
    let tabId = null;
    try {
        const tab = await chrome.tabs.create({ url: "https://labs.google/fx/tools/flow", active: false });
        tabId = tab.id;
        if (tabId) {
            await waitForTabReady(tabId);
            await sleep(2500);
        }
    } catch (e) {
        console.warn("[Flow2API] Failed to refresh Labs session tab", e);
    } finally {
        if (tabId) {
            try {
                await chrome.tabs.remove(tabId);
            } catch (e) {
                console.warn("[Flow2API] Failed to close Labs session refresh tab", e);
            }
        }
    }
}

async function getGoogleCookies() {
    const cookieMap = new Map();
    const cookieQueries = [
        { domain: "google.com" },
        { url: "https://accounts.google.com/" },
        { url: "https://www.google.com/" },
        { url: "https://google.com/" },
        { url: "https://ogs.google.com/" },
        { url: "https://labs.google/" }
    ];

    for (const query of cookieQueries) {
        const cookies = await getCookies(query);
        for (const cookie of cookies) {
            if (!GOOGLE_COOKIE_NAMES.includes(cookie.name) || !cookie.value) continue;
            const existing = cookieMap.get(cookie.name);
            const existingExpiry = existing && existing.expirationDate ? existing.expirationDate : 0;
            const nextExpiry = cookie.expirationDate || 0;
            if (!existing || nextExpiry >= existingExpiry) {
                cookieMap.set(cookie.name, {
                    name: cookie.name,
                    value: cookie.value,
                    domain: cookie.domain || "",
                    path: cookie.path || "/",
                    expirationDate: cookie.expirationDate || null
                });
            }
        }
    }

    return Array.from(cookieMap.values());
}

async function importCurrentAccount(reason = "manual") {
    if (accountImportInProgress) {
        console.log("[Flow2API] Account import already in progress, skipping", reason);
        return { skipped: true, reason: "in_progress" };
    }
    accountImportInProgress = true;
    const settings = await getSettings();
    try {
        if (!settings.apiKey) throw new Error("Flow2API API Key is empty");

    await refreshLabsSessionCookie();
        const sessionToken = await getLabsSessionToken();
        if (!sessionToken) {
            throw new Error("Labs Session Token not found. Open https://labs.google/fx/tools/flow in this Chrome profile first.");
        }

        const googleCookies = await getGoogleCookies();
        const foundNames = new Set(googleCookies.map(cookie => cookie.name));
    const hasUsableCookieGroup = GOOGLE_AUTH_COOKIE_GROUPS.some(group => group.every(name => foundNames.has(name)));
        if (!hasUsableCookieGroup) {
            throw new Error(`Google login cookies are incomplete. Found: ${Array.from(foundNames).join(", ") || "none"}. Open accounts.google.com and labs.google in this Chrome profile, then import again.`);
        }

        const baseUrl = getBackendBaseUrl(settings.serverUrl);
        const response = await fetch(`${baseUrl}/api/plugin/import-current-account`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${settings.apiKey}`
        },
        body: JSON.stringify({
            session_token: sessionToken,
            google_cookies: JSON.stringify(googleCookies),
            extension_route_key: settings.routeKey,
            refresh_interval_minutes: parseInt(settings.refreshIntervalMinutes, 10) || 120
        })
    });
        const payload = await response.json().catch(() => null);
        if (!response.ok || !payload || payload.success !== true) {
            const detail = payload && (payload.detail || payload.message);
            throw new Error(detail || `Import failed HTTP ${response.status}`);
        }

        chrome.storage.local.set({
        lastAutoImportAt: new Date().toISOString(),
        lastAutoImportStatus: "success",
        lastAutoImportMessage: `${reason}: ${payload.email || "unknown"}`
    });
        console.log("[Flow2API] Account import success", reason, payload);
        return payload;
    } finally {
        accountImportInProgress = false;
    }
}

async function runScheduledAccountImport() {
    const settings = await getSettings();
    if (!settings.autoImportEnabled) return;
    try {
        await importCurrentAccount("auto");
    } catch (e) {
        console.warn("[Flow2API] Auto account import failed", e);
        chrome.storage.local.set({
            lastAutoImportAt: new Date().toISOString(),
            lastAutoImportStatus: "error",
            lastAutoImportMessage: e.message || String(e)
        });
    }
}

async function configureAccountImportAlarm() {
    const settings = await getSettings();
    await chrome.alarms.clear(ACCOUNT_IMPORT_ALARM);
    if (!settings.autoImportEnabled) return;
    const interval = Math.max(5, parseInt(settings.autoImportIntervalMinutes, 10) || 30);
    chrome.alarms.create(ACCOUNT_IMPORT_ALARM, {
        delayInMinutes: 1,
        periodInMinutes: interval
    });
    console.log("[Flow2API] Auto account import alarm configured", interval, "minutes");
}

function closeSocket() {
    if (heartbeatInterval) clearInterval(heartbeatInterval);
    heartbeatInterval = null;
    if (reconnectTimeout) clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
    if (ws) {
        try {
            ws.close();
        } catch (e) {
            console.log("[Flow2API] Close socket error", e);
        }
        ws = null;
    }
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function waitForTabReady(tabId, timeoutMs = 12000) {
    return new Promise((resolve) => {
        let settled = false;
        const finish = () => {
            if (settled) return;
            settled = true;
            chrome.tabs.onUpdated.removeListener(onUpdated);
            clearTimeout(timer);
            resolve();
        };
        const onUpdated = (updatedTabId, changeInfo) => {
            if (updatedTabId === tabId && changeInfo.status === "complete") {
                finish();
            }
        };
        const timer = setTimeout(finish, timeoutMs);

        chrome.tabs.onUpdated.addListener(onUpdated);
        chrome.tabs.get(tabId, (tab) => {
            if (chrome.runtime.lastError) {
                finish();
                return;
            }
            if (tab && tab.status === "complete") {
                finish();
            }
        });
    });
}

async function connectWS() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

    const settings = await getSettings();
    const url = new URL(settings.serverUrl || DEFAULT_SETTINGS.serverUrl);
    if (settings.apiKey) {
        url.searchParams.set("key", settings.apiKey);
    }
    if (settings.routeKey) {
        url.searchParams.set("route_key", settings.routeKey);
    }
    if (settings.clientLabel) {
        url.searchParams.set("client_label", settings.clientLabel);
    }
    url.searchParams.set("extension_version", EXTENSION_VERSION);

    const socket = new WebSocket(url.toString());
    ws = socket;

    socket.onopen = () => {
        console.log("[Flow2API] Background connected to WebSocket", url.toString());
        socket.send(JSON.stringify({
            type: "register",
            route_key: settings.routeKey,
            client_label: settings.clientLabel,
            extension_version: EXTENSION_VERSION
        }));
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        heartbeatInterval = setInterval(() => {
            if (socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ type: "ping" }));
            }
        }, 20000);
    };

    let tokenQueue = Promise.resolve();

    socket.onmessage = async (event) => {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (e) {
            return;
        }

        if (data.type === "register_ack") {
            console.log("[Flow2API] Registered route key:", data.route_key || "(empty)");
            return;
        }

        if (data.type === "sync_account") {
            importCurrentAccount(`server:${data.reason || "token_error"}`).catch(error => {
                console.warn("[Flow2API] Immediate account sync failed", error);
            });
            return;
        }

        if (data.type === "get_token") {
            tokenQueue = tokenQueue.then(() => handleGetToken(data)).catch(err => {
                console.error("[Flow2API] Queue Error:", err);
            });
        }
    };

    socket.onclose = () => {
        console.log("[Flow2API] WebSocket Closed. Reconnecting in 2s...");
        if (ws === socket) {
            ws = null;
        }
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        if (reconnectTimeout) clearTimeout(reconnectTimeout);
        reconnectTimeout = setTimeout(connectWS, 2000);
    };

    socket.onerror = (e) => {
        console.log("[Flow2API] WebSocket Error", e);
    };
}

async function handleGetToken(data) {
    let newTabId = null;
    try {
        const existingTabs = await chrome.tabs.query({
            url: [
                "https://labs.google/fx/tools/flow*",
                "https://labs.google/fx/vi/tools/flow*",
                "https://labs.google/fx/*/tools/flow*",
                "https://labs.google/fx/projects/*",
                "https://labs.google/fx/vi/projects/*"
            ]
        });
        const targetTab = existingTabs.find(tab => tab.id) || await chrome.tabs.create({
            url: "https://labs.google/fx/tools/flow",
            active: false
        });
        newTabId = existingTabs.length ? null : targetTab.id;

        await waitForTabReady(targetTab.id);
        await sleep(newTabId ? 2000 : 500);

        let successResponse = null;
        let lastErrorMsg = "No response from tab.";
        const scriptTimeoutMs = data.action === "VIDEO_GENERATION" ? 120000 : 60000;

        try {
            const results = await chrome.scripting.executeScript({
                target: { tabId: targetTab.id },
                world: "MAIN",
                func: async (action, timeoutMs) => {
                    return new Promise((resolve, reject) => {
                        let settled = false;
                        const finish = (fn, value) => {
                            if (settled) return;
                            settled = true;
                            fn(value);
                        };
                        try {
                            function run() {
                                grecaptcha.enterprise.ready(function() {
                                    grecaptcha.enterprise.execute("6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV", { action: action })
                                        .then(token => finish(resolve, token))
                                        .catch(err => finish(reject, err.message || "reCAPTCHA evaluation failed internally"));
                                });
                            }

                            if (typeof grecaptcha !== "undefined" && grecaptcha.enterprise) {
                                run();
                            } else {
                                const s = document.createElement("script");
                                s.src = "https://www.google.com/recaptcha/enterprise.js?render=6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV";
                                s.onload = run;
                                s.onerror = () => finish(reject, "Failed to load enterprise.js via network");
                                document.head.appendChild(s);
                            }

                            setTimeout(() => finish(reject, "Timeout generating reCAPTCHA locally"), timeoutMs);
                        } catch (e) {
                            finish(reject, e.message);
                        }
                    });
                },
                args: [data.action || "IMAGE_GENERATION", scriptTimeoutMs]
            });

            if (results && results[0] && results[0].result) {
                successResponse = { status: "success", token: results[0].result };
            }
        } catch (e) {
            lastErrorMsg = e.message || "Script execution failed";
        }

        if (successResponse) {
            if (!ws || ws.readyState !== WebSocket.OPEN) {
                throw new Error("WebSocket is not connected");
            }
            ws.send(JSON.stringify({
                req_id: data.req_id,
                status: successResponse.status,
                token: successResponse.token
            }));
        } else {
            ws.send(JSON.stringify({
                req_id: data.req_id,
                status: "error",
                error: "Extension script failed: " + lastErrorMsg
            }));
        }
    } catch (err) {
        ws.send(JSON.stringify({
            req_id: data.req_id,
            status: "error",
            error: err.message
        }));
    } finally {
        if (newTabId) {
            try {
                await chrome.tabs.remove(newTabId);
                console.log("[Flow2API] Closed temporary token tab.");
            } catch (e) {
                console.log("[Flow2API] Error closing tab:", e);
            }
        }
    }
}

chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local") return;
    if (changes.routeKey || changes.serverUrl || changes.apiKey || changes.clientLabel) {
        console.log("[Flow2API] Extension settings changed, reconnecting WebSocket...");
        closeSocket();
        connectWS();
    }
    if (changes.autoImportEnabled || changes.autoImportIntervalMinutes || changes.refreshIntervalMinutes || changes.routeKey || changes.serverUrl || changes.apiKey) {
        configureAccountImportAlarm();
    }
});

chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === ACCOUNT_IMPORT_ALARM) {
        runScheduledAccountImport();
    }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || message.type !== "flow2api_import_current_account") return false;
    importCurrentAccount("manual")
        .then(payload => sendResponse({ success: true, payload }))
        .catch(error => sendResponse({ success: false, error: error.message || String(error) }));
    return true;
});

connectWS();
configureAccountImportAlarm();
runScheduledAccountImport();
