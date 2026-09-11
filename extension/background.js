let ws = null;
let reconnectTimeout = null;
let heartbeatInterval = null;
let accountImportInProgress = false;
const EXTENSION_VERSION = chrome.runtime.getManifest().version;

console.log(`[Flow2API] Captcha Worker v${EXTENSION_VERSION} loaded`);

function logExtensionEvent(event, details = {}) {
    const safeDetails = {};
    for (const [key, value] of Object.entries(details || {})) {
        if (["token", "session_token", "google_cookies", "apiKey", "cookie"].includes(key)) continue;
        safeDetails[key] = typeof value === "string" ? value.slice(0, 300) : value;
    }
    const entry = { time: new Date().toISOString(), event, details: safeDetails };
    console.log(`[Flow2API] ${event}`, safeDetails);
    chrome.storage.local.get({ extensionLogs: [] }, (stored) => {
        const logs = Array.isArray(stored.extensionLogs) ? stored.extensionLogs : [];
        chrome.storage.local.set({ extensionLogs: [entry, ...logs].slice(0, 100) });
    });
}

function setConnectionStatus(status, error = "") {
    chrome.storage.local.set({
        connectionStatus: status,
        connectionError: error || "",
        connectionLastChangedAt: new Date().toISOString(),
        connectionVersion: EXTENSION_VERSION,
    });
    logExtensionEvent("connection_status", { status, error });
}

const ACCOUNT_IMPORT_ALARM = "flow2api-auto-import-account";
const LABS_SESSION_COOKIE = "__Secure-next-auth.session-token";
const FLOW_HOME_URL = "https://flow.google.com/";
const FLOW_PROJECT_URL = "https://flow.google.com/project/";
const SESSION_COOKIE_BASE_NAMES = [
    "__Secure-next-auth.session-token",
    "next-auth.session-token",
    "__Host-next-auth.session-token",
    "__Secure-authjs.session-token",
    "authjs.session-token",
    "__Host-authjs.session-token",
];
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
    const queries = [
        { domain: "labs.google" },
        { domain: ".labs.google" },
        { domain: "flow.google.com" },
        { domain: ".flow.google.com" },
        { url: "https://labs.google/" },
        { url: "https://labs.google/fx" },
        { url: "https://labs.google/fx/tools/flow" },
        { url: "https://flow.google.com/" },
        { url: "https://flow.google.com/project/" }
    ];
    const cookiesByBaseName = new Map();
    for (const query of queries) {
        const cookies = await getCookies(query);
        for (const cookie of cookies) {
            const baseName = SESSION_COOKIE_BASE_NAMES.find((name) => (
                cookie.name === name || cookie.name.startsWith(`${name}.`)
            ));
            if (!baseName || !cookie.value) continue;
            const candidates = cookiesByBaseName.get(baseName) || [];
            if (!candidates.some(c => c.name === cookie.name && c.value === cookie.value)) {
                candidates.push(cookie);
            }
            cookiesByBaseName.set(baseName, candidates);
        }
    }
    const candidates = Array.from(cookiesByBaseName.entries()).map(([baseName, cookies]) => {
        const sorted = cookies.sort((left, right) => {
            const leftIndex = left.name === baseName ? -1 : Number(left.name.split(".").pop());
            const rightIndex = right.name === baseName ? -1 : Number(right.name.split(".").pop());
            return leftIndex - rightIndex;
        });
        return { baseName, value: sorted.map(cookie => cookie.value).join("") };
    });
    logExtensionEvent("session_cookie_candidates", {
        names: candidates.map(candidate => candidate.baseName),
        count: candidates.length,
    });
    const preferred = candidates.find(candidate => candidate.baseName === LABS_SESSION_COOKIE) || candidates[0];
    if (preferred && preferred.value) return preferred.value;
    return "";
}

async function isLabsSessionValid() {
    try {
        const res = await fetch("https://labs.google/fx/api/auth/session");
        if (!res.ok) return false;
        const data = await res.json();
        if (!data || !data.user) return false;
        if (data.expires && new Date(data.expires) <= new Date()) {
            return false;
        }
        return true;
    } catch (e) {
        return false;
    }
}

async function refreshLabsSessionCookie(force = false) {
    let token = await getLabsSessionToken();
    if (token && !force) {
        const valid = await isLabsSessionValid();
        if (valid) return token;
        console.log("[Flow2API] Existing Session Token is expired on Google, initiating auto refresh...");
        logExtensionEvent("session_token_expired_detected");
    }

    let tabId = null;
    try {
        logExtensionEvent("auto_login_labs_start");
        const tab = await chrome.tabs.create({ url: "https://labs.google/fx", active: false });
        tabId = tab.id;
        await waitForTabReady(tabId);
        await sleep(1500);

        try {
            await chrome.scripting.executeScript({
                target: { tabId },
                func: () => {
                    const directBtn = document.getElementById("sign-in-now-button");
                    if (directBtn) {
                        directBtn.click();
                        return "clicked_direct";
                    }
                    const openDialogBtn = Array.from(document.querySelectorAll("button, a")).find(
                        el => el.innerText && (el.innerText.includes("Sign in") || el.innerText.includes("登录"))
                    );
                    if (openDialogBtn) {
                        openDialogBtn.click();
                        setTimeout(() => {
                            const modalBtn = document.getElementById("sign-in-now-button");
                            if (modalBtn) modalBtn.click();
                        }, 500);
                        return "clicked_dialog_then_modal";
                    }
                    return "not_found";
                }
            });
        } catch (scriptErr) {
            console.warn("[Flow2API] Auto-signin script failed:", scriptErr);
        }

        const deadline = Date.now() + 10000;
        while (Date.now() < deadline) {
            await sleep(600);
            const valid = await isLabsSessionValid();
            if (valid) {
                token = await getLabsSessionToken();
                logExtensionEvent("auto_login_labs_success");
                break;
            }
        }
    } catch (e) {
        logExtensionEvent("auto_login_labs_failed", { error: e.message });
        console.warn("[Flow2API] Failed to refresh Labs session tab", e);
    } finally {
        if (tabId) {
            try {
                await chrome.tabs.remove(tabId);
            } catch (e) {
                // ignore
            }
        }
    }
    return token;
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
        logExtensionEvent("account_import_skipped", { reason, cause: "in_progress" });
        return { skipped: true, reason: "in_progress" };
    }
    accountImportInProgress = true;
    const settings = await getSettings();
    try {
        if (!settings.apiKey) throw new Error("Flow2API API Key is empty");

    await refreshLabsSessionCookie();
        let sessionToken = await getLabsSessionToken();
        if (!sessionToken) {
            sessionToken = await refreshLabsSessionCookie(true);
        }
        if (!sessionToken) {
            throw new Error("未能自动获取到 Flow/Labs 会话凭据。请确认当前浏览器已登录 Google 账号并能正常访问 https://flow.google.com/。");
        }

        const googleCookies = await getGoogleCookies();
        const foundNames = new Set(googleCookies.map(cookie => cookie.name));
        const hasUsableCookieGroup = GOOGLE_AUTH_COOKIE_GROUPS.some(group => group.every(name => foundNames.has(name)));
        if (!hasUsableCookieGroup) {
            throw new Error(`Google login cookies are incomplete. Found: ${Array.from(foundNames).join(", ") || "none"}. Open accounts.google.com and labs.google in this Chrome profile, then import again.`);
        }

        const baseUrl = getBackendBaseUrl(settings.serverUrl);
        let response = await fetch(`${baseUrl}/api/plugin/import-current-account`, {
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
        let payload = await response.json().catch(() => null);

        // 如果后端依然报告过期，自动强制刷新重试一次
        if (!response.ok && payload && String(payload.detail || "").includes("过期")) {
            console.log("[Flow2API] Backend reported expired session token, force refreshing and retrying...");
            logExtensionEvent("backend_reported_expired_retry");
            sessionToken = await refreshLabsSessionCookie(true);
            if (sessionToken) {
                response = await fetch(`${baseUrl}/api/plugin/import-current-account`, {
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
                payload = await response.json().catch(() => null);
            }
        }

        if (!response.ok || !payload || payload.success !== true) {
            const detail = payload && (payload.detail || payload.message);
            throw new Error(detail || `Import failed HTTP ${response.status}`);
        }

        chrome.storage.local.set({
            lastAutoImportAt: new Date().toISOString(),
            lastAutoImportStatus: "success",
            lastAutoImportMessage: `${reason}: ${payload.email || "unknown"}`
        });
        logExtensionEvent("account_import_success", {
            reason,
            email: payload.email || "unknown",
            added: payload.added || 0,
            updated: payload.updated || 0,
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
        logExtensionEvent("account_import_failed", { reason, error: e.message || String(e) });
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
    setConnectionStatus("connecting");
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
        setConnectionStatus("connected");
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
            logExtensionEvent("server_sync_request", { reason: data.reason || "token_error" });
            importCurrentAccount(`server:${data.reason || "token_error"}`).catch(error => {
                console.warn("[Flow2API] Immediate account sync failed", error);
            });
            return;
        }

        if (data.type === "get_token") {
            logExtensionEvent("captcha_request_received", {
                action: data.action || "IMAGE_GENERATION",
                request_id: data.req_id ? String(data.req_id).slice(-12) : "",
            });
            tokenQueue = tokenQueue.then(() => handleGetToken(data)).catch(err => {
                logExtensionEvent("captcha_queue_failed", { error: err.message || String(err) });
                console.error("[Flow2API] Queue Error:", err);
            });
        }
    };

    socket.onclose = () => {
        const status = socket.code === 1008 ? "auth_failed" : "disconnected";
        setConnectionStatus(
            status,
            socket.code === 1008 ? "服务器拒绝了连接，请检查 API Key。" : `连接已关闭（${socket.code || "未知"}）`
        );
        console.log("[Flow2API] WebSocket Closed. Reconnecting in 2s...");
        if (ws === socket) {
            ws = null;
        }
        if (heartbeatInterval) clearInterval(heartbeatInterval);
        if (reconnectTimeout) clearTimeout(reconnectTimeout);
        reconnectTimeout = setTimeout(connectWS, 2000);
    };

    socket.onerror = (e) => {
        setConnectionStatus("error", "无法连接到 Flow2API 服务，请检查地址、端口和防火墙。");
        console.log("[Flow2API] WebSocket Error", e);
    };
}

async function handleGetToken(data) {
    let newTabId = null;
    try {
        logExtensionEvent("captcha_start", {
            action: data.action || "IMAGE_GENERATION",
            request_id: data.req_id ? String(data.req_id).slice(-12) : "",
        });
        const projectId = String(data.project_id || "").trim();
        const projectUrl = projectId
            ? `https://flow.google.com/project/${encodeURIComponent(projectId)}`
            : "https://flow.google.com/";
        const existingTabs = await chrome.tabs.query({
            url: [
                "https://flow.google.com/*",
                "https://labs.google/fx/*"
            ]
        });
        const projectTab = projectId
            ? existingTabs.find(tab => tab.url && tab.url.includes(`/project/${projectId}`))
            : null;
        const anyProjectTab = existingTabs.find(tab => tab.url && (tab.url.includes("/project/") || tab.url.includes("/projects/")));
        const anyFlowTab = existingTabs.find(tab => tab.url && (tab.url.includes("flow.google.com") || tab.url.includes("labs.google")));

        let targetTab = projectTab || anyProjectTab || anyFlowTab;
        if (!targetTab) {
            targetTab = await chrome.tabs.create({
                url: projectUrl || FLOW_HOME_URL,
                active: false
            });
            newTabId = targetTab.id;
        }

        await waitForTabReady(targetTab.id);
        await sleep(newTabId ? 2500 : 300);

        logExtensionEvent("captcha_tab_selected", {
            action: data.action || "IMAGE_GENERATION",
            tab_id: targetTab.id,
            reused: !newTabId,
            url: targetTab.url || "",
            project_id: projectId,
        });

        let successResponse = null;
        let lastErrorMsg = "No response from tab.";
        const scriptTimeoutMs = data.action === "VIDEO_GENERATION" ? 120000 : 30000;
        const executeScriptTimeoutMs = scriptTimeoutMs + 5000;

        try {
            const executeScriptPromise = chrome.scripting.executeScript({
                target: { tabId: targetTab.id },
                world: "MAIN",
                func: async (action, timeoutMs) => {
                    const siteKey = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV";
                    const fail = (stage, error) => ({
                        ok: false,
                        stage,
                        error: String(error || "unknown error"),
                        href: location.href,
                    });
                    try {
                        if (!location.hostname.endsWith("labs.google") && location.hostname !== "flow.google.com") {
                            return fail("page_check", `unexpected page: ${location.href}`);
                        }

                        // 如果当前不在项目页且没有验证码环境，尝试自动从首页链接进入项目
                        if (!location.pathname.includes("/project") && !location.pathname.includes("/projects") && !(window.grecaptcha && window.grecaptcha.enterprise)) {
                            const link = document.querySelector('a[href*="/project/"]');
                            if (link && link.href) {
                                location.href = link.href;
                                return fail("navigating_to_project", `页面在首页，已触发自动跳转到项目页: ${link.href}，请稍候重试`);
                            }
                        }

                        // Flow 项目页面自身会加载 reCAPTCHA。
                        const captchaDeadline = Date.now() + Math.min(timeoutMs, 20000);
                        while (!(window.grecaptcha && window.grecaptcha.enterprise) && Date.now() < captchaDeadline) {
                            await new Promise(resolve => setTimeout(resolve, 250));
                        }
                        if (!(window.grecaptcha && window.grecaptcha.enterprise)) {
                            return fail("captcha_load", "grecaptcha.enterprise 未由 Flow 页面加载");
                        }

                        await Promise.race([
                            new Promise(resolve => window.grecaptcha.enterprise.ready(resolve)),
                            new Promise((_, reject) => setTimeout(() => reject(new Error("enterprise.ready timeout")), timeoutMs)),
                        ]);

                        const token = await Promise.race([
                            window.grecaptcha.enterprise.execute(siteKey, { action }),
                            new Promise((_, reject) => setTimeout(() => reject(new Error("enterprise.execute timeout")), timeoutMs)),
                        ]);
                        if (!token) return fail("captcha_execute", "empty reCAPTCHA token");
                        return { ok: true, token, href: location.href };
                    } catch (error) {
                        return fail("captcha_execute", error && error.message ? error.message : error);
                    }
                },
                args: [data.action || "IMAGE_GENERATION", scriptTimeoutMs]
            });
            const results = await Promise.race([
                executeScriptPromise,
                new Promise((_, reject) => setTimeout(
                    () => reject(new Error(`executeScript timeout after ${executeScriptTimeoutMs}ms`)),
                    executeScriptTimeoutMs
                )),
            ]);

            const scriptResult = results && results[0] ? results[0].result : null;
            if (scriptResult && scriptResult.ok && scriptResult.token) {
                successResponse = { status: "success", token: scriptResult.token };
            } else if (scriptResult) {
                lastErrorMsg = `${scriptResult.stage || "script"}: ${scriptResult.error || "empty result"}`;
                logExtensionEvent("captcha_page_failed", {
                    action: data.action || "IMAGE_GENERATION",
                    request_id: data.req_id ? String(data.req_id).slice(-12) : "",
                    stage: scriptResult.stage || "unknown",
                    error: scriptResult.error || "empty result",
                    href: scriptResult.href || "",
                });
            } else {
                lastErrorMsg = `empty executeScript result (count=${results ? results.length : 0})`;
                logExtensionEvent("captcha_page_failed", {
                    action: data.action || "IMAGE_GENERATION",
                    request_id: data.req_id ? String(data.req_id).slice(-12) : "",
                    stage: "execute_script",
                    error: lastErrorMsg,
                });
            }
        } catch (e) {
            lastErrorMsg = e.message || "Script execution failed";
            logExtensionEvent("captcha_script_exception", {
                action: data.action || "IMAGE_GENERATION",
                request_id: data.req_id ? String(data.req_id).slice(-12) : "",
                error: lastErrorMsg,
            });
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
            logExtensionEvent("captcha_success", {
                action: data.action || "IMAGE_GENERATION",
                request_id: data.req_id ? String(data.req_id).slice(-12) : "",
                token_length: successResponse.token ? successResponse.token.length : 0,
            });
        } else {
            ws.send(JSON.stringify({
                req_id: data.req_id,
                status: "error",
                error: "Extension script failed: " + lastErrorMsg
            }));
            logExtensionEvent("captcha_failed", {
                action: data.action || "IMAGE_GENERATION",
                request_id: data.req_id ? String(data.req_id).slice(-12) : "",
                error: lastErrorMsg,
            });
        }
    } catch (err) {
        ws.send(JSON.stringify({
            req_id: data.req_id,
            status: "error",
            error: err.message
        }));
        logExtensionEvent("captcha_failed", {
            action: data.action || "IMAGE_GENERATION",
            request_id: data.req_id ? String(data.req_id).slice(-12) : "",
            error: err.message || String(err),
        });
    } finally {
        if (newTabId) {
            try {
                await chrome.tabs.remove(newTabId);
                logExtensionEvent("temporary_flow_tab_closed");
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
    if (message && message.type === "flow2api_reconnect") {
        closeSocket();
        connectWS()
            .then(() => sendResponse({ success: true }))
            .catch(error => sendResponse({ success: false, error: error.message }));
        return true;
    }
    if (!message || message.type !== "flow2api_import_current_account") return false;
    importCurrentAccount("manual")
        .then(payload => sendResponse({ success: true, payload }))
        .catch(error => sendResponse({ success: false, error: error.message || String(error) }));
    return true;
});

connectWS();
configureAccountImportAlarm();
runScheduledAccountImport();
