importScripts("project_url.js");
importScripts("connection_policy.js");
importScripts("recaptcha_policy.js");

let ws = null;
let connectPromise = null;
let connectionEpoch = 0;
let reconnectTimeout = null;
let heartbeatInterval = null;
let accountImportInProgress = false;
let mintTabId = null;
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
const KEEPALIVE_ALARM = "flow2api-websocket-keepalive";
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

async function getCurrentFlowProject() {
    const tabs = await new Promise((resolve, reject) => {
        chrome.tabs.query({}, (matchedTabs) => {
            if (chrome.runtime.lastError) {
                reject(new Error(chrome.runtime.lastError.message));
                return;
            }
            resolve(matchedTabs || []);
        });
    });
    const flowTabs = tabs.filter((tab) => {
        return [tab.url, tab.pendingUrl].some((rawUrl) => {
            try {
                const hostname = new URL(String(rawUrl || "")).hostname;
                return hostname === "flow.google.com" || hostname === "labs.google";
            } catch (error) {
                return false;
            }
        });
    });

    let currentProject = FlowProjectUrl.selectCurrentFlowProject(flowTabs);
    if (currentProject) return { project: currentProject, flowTabs };

    for (const tab of flowTabs) {
        if (!tab.id) continue;
        try {
            const results = await chrome.scripting.executeScript({
                target: { tabId: tab.id },
                func: () => ({
                    currentUrl: location.href,
                    title: document.title,
                    links: Array.from(document.querySelectorAll('a[href*="/project"]'))
                        .map((anchor) => anchor.href)
                        .filter(Boolean)
                        .slice(0, 50),
                }),
            });
            const page = results && results[0] && results[0].result;
            if (!page) continue;
            tab.url = page.currentUrl || tab.url;
            tab.title = page.title || tab.title;
            tab.discoveredUrls = page.links || [];
        } catch (error) {
            // Restricted or still-loading tabs are ignored; other Flow tabs may work.
        }
    }

    currentProject = FlowProjectUrl.selectCurrentFlowProject(flowTabs);
    return { project: currentProject, flowTabs };
}

async function createFlowProject() {
    let tab = (await chrome.tabs.query({ url: ["https://flow.google.com/*"] }))[0] || null;
    let createdTabId = null;
    if (!tab) {
        tab = await chrome.tabs.create({ url: FLOW_HOME_URL, active: true });
        createdTabId = tab.id;
    } else if (tab.id) {
        await chrome.tabs.update(tab.id, { active: true });
    }
    if (!tab || !tab.id) throw new Error("无法打开 flow.google.com");

    await waitForTabReady(tab.id, 30000);
    await sleep(2500);

    const now = new Date();
    const projectName = `Flow2API ${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")} ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    const envelope = FlowProjectUrl.buildCreateProjectEnvelope(projectName);
    const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: async (requestEnvelope) => {
            const wiz = globalThis.WIZ_global_data || {};
            let at = String(wiz.SNlM0e || "").trim();
            let sid = String(wiz.FdrFJe || "").trim();
            let bl = String(wiz.cfb2h || "").trim();

            for (const value of Object.values(wiz)) {
                if (!sid && typeof value === "string" && /^-?\d{15,20}$/.test(value)) sid = value;
                if (!at && typeof value === "string" && /^AIQ-[A-Za-z0-9_-]+:\d+$/.test(value)) at = value;
                if (!bl && typeof value === "string" && /^(?:boq[_-]|boq-)/.test(value)) bl = value;
            }

            if (!sid || !bl) {
                try {
                    const entries = performance.getEntriesByType("resource")
                        .filter((entry) => String(entry.name || "").includes("batchexecute"));
                    if (entries.length) {
                        const url = new URL(entries[entries.length - 1].name);
                        sid ||= url.searchParams.get("f.sid") || "";
                        bl ||= url.searchParams.get("bl") || "";
                    }
                } catch (error) {
                    // WIZ_global_data remains the primary source.
                }
            }

            if (!at) return { ok: false, error: "Flow 页面认证参数不可用，请确认已登录并刷新 flow.google.com" };
            const reqid = Math.floor(Math.random() * 900000) + 100000;
            const hl = (document.documentElement.lang || navigator.language || "en").split("-")[0];
            const url = `/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=jHPbke&source-path=%2F&bl=${encodeURIComponent(bl)}&f.sid=${encodeURIComponent(sid)}&hl=${encodeURIComponent(hl)}&_reqid=${reqid}&rt=c`;
            const response = await fetch(url, {
                method: "POST",
                credentials: "include",
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    "X-Same-Domain": "1",
                },
                body: new URLSearchParams({
                    "f.req": JSON.stringify(requestEnvelope),
                    at,
                }),
            });
            const responseText = await response.text();
            return {
                ok: response.ok,
                status: response.status,
                responseText,
                href: location.href,
            };
        },
        args: [envelope],
    });

    const result = results && results[0] && results[0].result;
    if (!result || !result.ok) {
        const detail = result && (result.error || `HTTP ${result.status}: ${String(result.responseText || "").slice(0, 300)}`);
        throw new Error(`自动创建 Flow 项目失败：${detail || "页面未返回结果"}`);
    }
    const projectId = FlowProjectUrl.extractCreatedProjectId(result.responseText);
    if (!projectId) {
        throw new Error(`自动创建 Flow 项目失败：响应中没有项目 ID (${String(result.responseText || "").slice(0, 300)})`);
    }

    const projectUrl = `${FLOW_PROJECT_URL}${encodeURIComponent(projectId)}`;
    await chrome.tabs.update(tab.id, { url: projectUrl, active: true });
    await waitForTabReady(tab.id, 30000);
    return {
        project: { projectId, projectName },
        flowTabs: [{ id: tab.id, url: projectUrl, title: projectName }],
        createdTabId,
    };
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
        "https://labs.google/",
        "https://flow.google.com/",
        "https://flow.google.com/project/"
    ];

    // 1. 优先使用 url 查询（最精准，可直接匹配 Host-only 与 Domain Cookie）
    for (const url of urls) {
        for (const baseName of SESSION_COOKIE_BASE_NAMES) {
            try {
                const cookie = await getCookie({ url, name: baseName });
                if (cookie && cookie.value && cookie.value.length > 30) {
                    logExtensionEvent("session_cookie_found", { url, name: baseName, len: cookie.value.length });
                    return cookie.value;
                }
            } catch (e) {}
        }
    }

    // 2. 按 Cookie 名称全局匹配
    for (const baseName of SESSION_COOKIE_BASE_NAMES) {
        try {
            const cookies = await getCookies({ name: baseName });
            if (cookies && cookies.length > 0) {
                // 优先取 labs.google 的
                const labsCookie = cookies.find(c => (c.domain || "").includes("labs.google") && c.value && c.value.length > 30);
                if (labsCookie) {
                    logExtensionEvent("session_cookie_found", { domain: labsCookie.domain, name: baseName, len: labsCookie.value.length });
                    return labsCookie.value;
                }
                const anyCookie = cookies.find(c => c.value && c.value.length > 30);
                if (anyCookie) {
                    logExtensionEvent("session_cookie_found", { domain: anyCookie.domain, name: baseName, len: anyCookie.value.length });
                    return anyCookie.value;
                }
            }
        } catch (e) {}
    }

    // 3. 检查是否有分片 Cookie (__Secure-next-auth.session-token.0, .1...)
    try {
        const allCookies = await getCookies({});
        const chunks = allCookies
            .filter(c => c.name.startsWith("__Secure-next-auth.session-token.") && c.value)
            .sort((a, b) => (Number(a.name.split(".").pop()) || 0) - (Number(b.name.split(".").pop()) || 0));
        if (chunks.length > 0) {
            const fullVal = chunks.map(c => c.value).join("");
            if (fullVal.length > 30) {
                logExtensionEvent("session_cookie_found", { type: "chunked", len: fullVal.length });
                return fullVal;
            }
        }

        for (const c of allCookies) {
            if (SESSION_COOKIE_BASE_NAMES.includes(c.name) && c.value && c.value.length > 30) {
                logExtensionEvent("session_cookie_found", { domain: c.domain, name: c.name, len: c.value.length });
                return c.value;
            }
        }
    } catch (e) {}

    return "";
}

async function clearLabsSessionCookies() {
    logExtensionEvent("clear_expired_session_cookies");
    const urls = [
        "https://labs.google/",
        "https://labs.google/fx",
        "https://labs.google/fx/tools/flow",
        "https://flow.google.com/"
    ];
    for (const url of urls) {
        for (const name of SESSION_COOKIE_BASE_NAMES) {
            try {
                await new Promise(r => chrome.cookies.remove({ url, name }, r));
            } catch (e) {}
            for (let i = 0; i < 10; i++) {
                try {
                    await new Promise(r => chrome.cookies.remove({ url, name: `${name}.${i}` }, r));
                } catch (e) {}
            }
        }
    }
}

async function refreshLabsSessionCookie(force = false) {
    if (!force) {
        let token = await getLabsSessionToken();
        if (token) return token;
    }

    logExtensionEvent("auto_open_flow_for_session", { force });
    if (force) {
        await clearLabsSessionCookies();
    }

    let tabId = null;
    let freshToken = "";
    try {
        const tab = await chrome.tabs.create({ url: "https://labs.google/fx", active: true });
        tabId = tab.id;
        await waitForTabReady(tabId);
        await sleep(2000);

        // 尝试自动点击登录授权按钮
        try {
            await chrome.scripting.executeScript({
                target: { tabId },
                func: () => {
                    const directBtn = document.getElementById("sign-in-now-button");
                    if (directBtn) {
                        directBtn.click();
                        return "clicked_direct";
                    }
                    const btn = Array.from(document.querySelectorAll("button, a")).find(
                        el => el.innerText && (el.innerText.includes("Sign in") || el.innerText.includes("登录"))
                    );
                    if (btn) {
                        btn.click();
                        setTimeout(() => {
                            const modalBtn = document.getElementById("sign-in-now-button");
                            if (modalBtn) modalBtn.click();
                        }, 500);
                        return "clicked_dialog";
                    }
                    return "not_found";
                }
            });
        } catch (scriptErr) {
            console.warn("[Flow2API] Auto-signin click failed:", scriptErr);
        }

        // 轮询等待 Session Cookie 写入（最多等 15 秒）
        const deadline = Date.now() + 15000;
        while (Date.now() < deadline) {
            await sleep(1000);
            freshToken = await getLabsSessionToken();
            if (freshToken) {
                logExtensionEvent("auto_open_flow_session_success", { len: freshToken.length });
                break;
            }
        }
    } catch (e) {
        logExtensionEvent("auto_open_flow_session_failed", { error: e.message });
        console.warn("[Flow2API] Failed to auto-open flow for session:", e);
    } finally {
        if (tabId) {
            try {
                await chrome.tabs.remove(tabId);
                logExtensionEvent("auto_open_flow_session_tab_closed");
            } catch (e) {}
        }
    }

    return freshToken || "";
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
            // 自动打开前台授权页面，Google 已登录时通常秒级完成自动登录
            logExtensionEvent("session_token_missing_opening_auth_page");
            chrome.tabs.create({ url: "https://labs.google/fx", active: true });
            throw new Error("正在自动打开 Google 授权页面（https://labs.google/fx）。页面加载完成后会自动写入登录凭据，请稍候几秒后再次点击导入。");
        }

        const googleCookies = await getGoogleCookies();
        const foundNames = new Set(googleCookies.map(cookie => cookie.name));
        const hasUsableCookieGroup = GOOGLE_AUTH_COOKIE_GROUPS.some(group => group.every(name => foundNames.has(name)));
        if (!hasUsableCookieGroup) {
            throw new Error(`Google login cookies are incomplete. Found: ${Array.from(foundNames).join(", ") || "none"}. Open accounts.google.com and labs.google in this Chrome profile, then import again.`);
        }

        let projectLookup = await getCurrentFlowProject();
        if (!projectLookup.project) {
            logExtensionEvent("flow_project_missing_creating_new");
            projectLookup = await createFlowProject();
        }
        const currentProject = projectLookup.project;

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
                project_id: currentProject ? currentProject.projectId : null,
                project_name: currentProject ? currentProject.projectName : null,
                extension_route_key: settings.routeKey,
                refresh_interval_minutes: parseInt(settings.refreshIntervalMinutes, 10) || 120
            })
        });
        let payload = await response.json().catch(() => null);

        // 如果后端报告 Session Token 已过期或无效，自动开启页面换取新 Token 并自动重试
        if (!response.ok && payload && (
            String(payload.detail || "").includes("过期") ||
            String(payload.detail || "").includes("失效") ||
            String(payload.detail || "").includes("access_token")
        )) {
            console.log("[Flow2API] Backend reported token expired. Automatically opening auth page and retrying...");
            logExtensionEvent("token_expired_auto_refresh_retry", { detail: payload.detail });

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
                        project_id: currentProject ? currentProject.projectId : null,
                        project_name: currentProject ? currentProject.projectName : null,
                        extension_route_key: settings.routeKey,
                        refresh_interval_minutes: parseInt(settings.refreshIntervalMinutes, 10) || 120
                    })
                });
                payload = await response.json().catch(() => null);
            }
        }

        if (!response.ok || !payload || payload.success !== true) {
            const detail = payload && (payload.detail || payload.message);
            const visibleFlowUrls = projectLookup.flowTabs
                .flatMap((tab) => [tab.url, tab.pendingUrl])
                .filter(Boolean)
                .slice(0, 5);
            const lookupDetail = !currentProject
                ? ` 扩展看到的 Flow 页面：${visibleFlowUrls.join(" | ") || "无"}`
                : "";
            throw new Error(`${detail || `Import failed HTTP ${response.status}`}${lookupDetail}`);
        }

        chrome.storage.local.set({
            lastAutoImportAt: new Date().toISOString(),
            lastAutoImportStatus: "success",
            lastAutoImportMessage: `${reason}: ${payload.email || "unknown"}`,
            lastImportExpires: payload.expires || "",
            lastImportEmail: payload.email || "unknown"
        });
        logExtensionEvent("account_import_success", {
            reason,
            email: payload.email || "unknown",
            added: payload.added || 0,
            updated: payload.updated || 0,
            expires: payload.expires || "",
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
        logExtensionEvent("account_import_failed", { reason: "auto", error: e.message || String(e) });
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

async function configureKeepaliveAlarm() {
    await chrome.alarms.clear(KEEPALIVE_ALARM);
    chrome.alarms.create(KEEPALIVE_ALARM, {
        delayInMinutes: FlowConnectionPolicy.KEEPALIVE_PERIOD_MINUTES,
        periodInMinutes: FlowConnectionPolicy.KEEPALIVE_PERIOD_MINUTES,
    });
}

async function ensureWebSocketConnected() {
    const readyState = ws ? ws.readyState : null;
    if (!FlowConnectionPolicy.shouldReconnectWebSocket(readyState)) {
        if (readyState === WebSocket.OPEN) {
            try {
                ws.send(JSON.stringify({ type: "ping" }));
            } catch (error) {
                closeSocket();
                await connectWS();
            }
        }
        return;
    }
    closeSocket();
    await connectWS();
}

function closeSocket() {
    connectionEpoch += 1;
    connectPromise = null;
    if (heartbeatInterval) clearInterval(heartbeatInterval);
    heartbeatInterval = null;
    if (reconnectTimeout) clearTimeout(reconnectTimeout);
    reconnectTimeout = null;
    const socket = ws;
    ws = null;
    if (socket) {
        try {
            socket.close();
        } catch (e) {
            console.log("[Flow2API] Close socket error", e);
        }
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

function getTab(tabId) {
    return new Promise((resolve) => {
        if (tabId == null) {
            resolve(null);
            return;
        }
        chrome.tabs.get(tabId, (tab) => {
            resolve(chrome.runtime.lastError ? null : (tab || null));
        });
    });
}

async function ensureMintTab() {
    if (mintTabId == null) {
        const stored = await chrome.storage.local.get(["mintTabId"]);
        mintTabId = stored.mintTabId || null;
    }

    let tab = await getTab(mintTabId);
    if (tab && tab.discarded) {
        try {
            await chrome.tabs.reload(tab.id);
            await waitForTabReady(tab.id, 30000);
            tab = await getTab(tab.id);
        } catch (error) {
            tab = null;
        }
    }

    if (tab && String(tab.url || "").startsWith("https://flow.google.com/")) {
        return tab.id;
    }

    if (tab) {
        try {
            await chrome.tabs.update(tab.id, {
                url: FlowRecaptchaPolicy.MINT_PAGE_URL,
                active: false,
            });
            await waitForTabReady(tab.id, 30000);
            await sleep(1000);
            const updated = await getTab(tab.id);
            if (updated && String(updated.url || "").startsWith("https://flow.google.com/")) {
                return updated.id;
            }
        } catch (error) {
            // Create a fresh tab below.
        }
    }

    const created = await chrome.tabs.create({
        url: FlowRecaptchaPolicy.MINT_PAGE_URL,
        active: false,
    });
    if (!created || !created.id) throw new Error("无法创建 Flow 验证码页面");
    mintTabId = created.id;
    await chrome.storage.local.set({ mintTabId });
    await waitForTabReady(mintTabId, 30000);
    await sleep(1000);
    return mintTabId;
}

async function dropMintTab(reason) {
    const tabId = mintTabId;
    mintTabId = null;
    await chrome.storage.local.remove("mintTabId");
    if (tabId == null) return;
    try {
        await chrome.tabs.remove(tabId);
        logExtensionEvent("mint_tab_dropped", { reason });
    } catch (error) {
        // The tab may already be closed.
    }
}

async function mintRecaptchaToken(tabId, action, timeoutMs) {
    const siteKey = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV";
    const scriptUrl = FlowRecaptchaPolicy.getEnterpriseScriptUrl(siteKey);
    const results = await chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: async (siteKeyArg, actionArg, timeoutArg, scriptUrlArg) => {
            const context = () => ({
                href: location.href,
                grecaptcha: Boolean(window.grecaptcha && window.grecaptcha.enterprise),
                realExecute: Boolean(window.__flow2apiRealRecaptchaExecute),
                fingerprint: {
                    user_agent: navigator.userAgent || "",
                    accept_language: Array.isArray(navigator.languages) && navigator.languages.length
                        ? navigator.languages.map((language, index) => index === 0 ? language : `${language};q=${Math.max(0.1, 1 - (index * 0.1)).toFixed(1)}`).join(",")
                        : (navigator.language || ""),
                    language: navigator.language || "",
                    sec_ch_ua: navigator.userAgentData && Array.isArray(navigator.userAgentData.brands)
                        ? navigator.userAgentData.brands.map(item => `"${item.brand}";v="${item.version}"`).join(", ")
                        : "",
                    sec_ch_ua_mobile: navigator.userAgentData && navigator.userAgentData.mobile ? "?1" : "?0",
                    sec_ch_ua_platform: navigator.userAgentData && navigator.userAgentData.platform
                        ? `"${navigator.userAgentData.platform}"`
                        : (navigator.platform && navigator.platform.toLowerCase().includes("linux") ? '"Linux"' : ""),
                    origin: location.origin,
                    referer: location.href,
                },
            });
            const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));

            try {
                if (!(window.grecaptcha && window.grecaptcha.enterprise)) {
                    let trustedScriptUrl = scriptUrlArg;
                    if (window.trustedTypes && window.trustedTypes.createPolicy) {
                        let policy = window.__flow2apiTrustedTypesPolicy;
                        if (!policy) {
                            policy = window.trustedTypes.createPolicy("flow2api-recaptcha", {
                                createScriptURL: value => value,
                            });
                            Object.defineProperty(window, "__flow2apiTrustedTypesPolicy", {
                                value: policy,
                                configurable: true,
                            });
                        }
                        trustedScriptUrl = policy.createScriptURL(scriptUrlArg);
                    }

                    await new Promise((resolve, reject) => {
                        const script = document.createElement("script");
                        const noncedScript = document.querySelector("script[nonce]");
                        if (noncedScript && noncedScript.nonce) script.nonce = noncedScript.nonce;
                        script.src = trustedScriptUrl;
                        script.onload = resolve;
                        script.onerror = () => reject(new Error("enterprise.js 加载失败，可能被 CSP 拦截"));
                        (document.head || document.documentElement).appendChild(script);
                    });

                    const loadDeadline = Date.now() + Math.min(timeoutArg, 15000);
                    while (!(window.grecaptcha && window.grecaptcha.enterprise) && Date.now() < loadDeadline) {
                        await wait(200);
                    }
                }

                const enterprise = window.grecaptcha && window.grecaptcha.enterprise;
                if (!enterprise) throw new Error("grecaptcha.enterprise 未加载");
                await Promise.race([
                    new Promise(resolve => enterprise.ready(resolve)),
                    new Promise((_, reject) => setTimeout(() => reject(new Error("enterprise.ready timeout")), timeoutArg)),
                ]);

                const realExecute = window.__flow2apiRealRecaptchaExecute;
                const tokenPromise = realExecute
                    ? realExecute(siteKeyArg, { action: actionArg })
                    : enterprise.execute(siteKeyArg, { action: actionArg });
                const token = await Promise.race([
                    tokenPromise,
                    new Promise((_, reject) => setTimeout(() => reject(new Error("enterprise.execute timeout")), timeoutArg)),
                ]);
                if (!token) throw new Error("empty reCAPTCHA token");
                return { ok: true, token, ...context() };
            } catch (error) {
                return {
                    ok: false,
                    error: error && error.message ? error.message : String(error),
                    ...context(),
                };
            }
        },
        args: [siteKey, action, timeoutMs, scriptUrl],
    });
    return results && results[0] ? results[0].result : null;
}

function connectWS() {
    const readyState = ws ? ws.readyState : null;
    if (!FlowConnectionPolicy.shouldStartWebSocketConnection(readyState, Boolean(connectPromise))) {
        return connectPromise || Promise.resolve();
    }

    const epoch = ++connectionEpoch;
    const pending = (async () => {
        const settings = await getSettings();
        if (epoch !== connectionEpoch) return;

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
        if (epoch !== connectionEpoch) {
            socket.close();
            return;
        }
        ws = socket;

        socket.onopen = () => {
            if (!FlowConnectionPolicy.isCurrentSocket(ws, socket)) {
                socket.close();
                return;
            }
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
                if (
                    FlowConnectionPolicy.isCurrentSocket(ws, socket)
                    && socket.readyState === WebSocket.OPEN
                ) {
                    chrome.storage.local.set({
                        connectionStatus: "connected",
                        connectionError: "",
                        connectionVersion: EXTENSION_VERSION,
                    });
                    socket.send(JSON.stringify({ type: "ping" }));
                }
            }, 20000);
        };

        let tokenQueue = Promise.resolve();

        socket.onmessage = async (event) => {
            if (!FlowConnectionPolicy.isCurrentSocket(ws, socket)) return;
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
                const syncSettings = await getSettings();
                if (!syncSettings.autoImportEnabled) {
                    logExtensionEvent("server_sync_ignored", {
                        reason: data.reason || "token_error",
                        cause: "auto_import_disabled",
                    });
                    return;
                }
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
                tokenQueue = tokenQueue.then(() => handleGetToken(data, socket)).catch(err => {
                    logExtensionEvent("captcha_queue_failed", { error: err.message || String(err) });
                    console.error("[Flow2API] Queue Error:", err);
                });
            }
        };

        socket.onclose = () => {
            if (!FlowConnectionPolicy.isCurrentSocket(ws, socket)) return;
            const status = socket.code === 1008 ? "auth_failed" : "disconnected";
            setConnectionStatus(
                status,
                socket.code === 1008 ? "服务器拒绝了连接，请检查 API Key。" : `连接已关闭（${socket.code || "未知"}）`
            );
            console.log("[Flow2API] WebSocket Closed. Reconnecting in 2s...");
            ws = null;
            if (heartbeatInterval) clearInterval(heartbeatInterval);
            if (reconnectTimeout) clearTimeout(reconnectTimeout);
            reconnectTimeout = setTimeout(connectWS, 2000);
        };

        socket.onerror = (e) => {
            if (!FlowConnectionPolicy.isCurrentSocket(ws, socket)) return;
            setConnectionStatus("error", "无法连接到 Flow2API 服务，请检查地址、端口和防火墙。");
            console.log("[Flow2API] WebSocket Error", e);
        };
    })();

    connectPromise = pending;
    pending.finally(() => {
        if (connectPromise === pending) connectPromise = null;
    });
    return pending;
}

async function handleGetToken(data, responseSocket) {
    try {
        logExtensionEvent("captcha_start", {
            action: data.action || "IMAGE_GENERATION",
            request_id: data.req_id ? String(data.req_id).slice(-12) : "",
        });
        let successResponse = null;
        let lastErrorMsg = "No response from mint tab.";
        const scriptTimeoutMs = data.action === "VIDEO_GENERATION" ? 120000 : 30000;
        for (let attempt = 1; attempt <= 2; attempt += 1) {
            try {
                if (attempt > 1) await dropMintTab(lastErrorMsg);
                const tabId = await ensureMintTab();
                const scriptResult = await mintRecaptchaToken(
                    tabId,
                    data.action || "IMAGE_GENERATION",
                    scriptTimeoutMs,
                );
                if (scriptResult && scriptResult.ok && scriptResult.token) {
                    successResponse = {
                        status: "success",
                        token: scriptResult.token,
                        fingerprint: scriptResult.fingerprint || null,
                    };
                    logExtensionEvent("captcha_mint_success", {
                        attempt,
                        href: scriptResult.href || "",
                        real_execute: Boolean(scriptResult.realExecute),
                    });
                    break;
                }
                lastErrorMsg = scriptResult
                    ? `${scriptResult.error || "empty result"} [href=${scriptResult.href || ""}, real=${scriptResult.realExecute ? "captured" : "none"}]`
                    : "empty executeScript result";
            } catch (error) {
                lastErrorMsg = error.message || String(error);
            }
            logExtensionEvent("captcha_mint_failed", {
                attempt,
                action: data.action || "IMAGE_GENERATION",
                request_id: data.req_id ? String(data.req_id).slice(-12) : "",
                error: lastErrorMsg,
            });
        }

        if (successResponse) {
            if (!responseSocket || responseSocket.readyState !== WebSocket.OPEN) {
                throw new Error("WebSocket is not connected");
            }
            responseSocket.send(JSON.stringify({
                req_id: data.req_id,
                status: successResponse.status,
                token: successResponse.token,
                fingerprint: successResponse.fingerprint,
            }));
            logExtensionEvent("captcha_success", {
                action: data.action || "IMAGE_GENERATION",
                request_id: data.req_id ? String(data.req_id).slice(-12) : "",
                token_length: successResponse.token ? successResponse.token.length : 0,
            });
        } else {
            if (!responseSocket || responseSocket.readyState !== WebSocket.OPEN) {
                throw new Error("WebSocket is not connected");
            }
            responseSocket.send(JSON.stringify({
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
        if (responseSocket && responseSocket.readyState === WebSocket.OPEN) {
            responseSocket.send(JSON.stringify({
                req_id: data.req_id,
                status: "error",
                error: err.message
            }));
        }
        logExtensionEvent("captcha_failed", {
            action: data.action || "IMAGE_GENERATION",
            request_id: data.req_id ? String(data.req_id).slice(-12) : "",
            error: err.message || String(err),
        });
    }
}

chrome.tabs.onRemoved.addListener((tabId) => {
    if (tabId === mintTabId) {
        mintTabId = null;
        chrome.storage.local.remove("mintTabId");
    }
});

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
    if (alarm.name === KEEPALIVE_ALARM) {
        ensureWebSocketConnected().catch(error => {
            logExtensionEvent("websocket_keepalive_failed", { error: error.message || String(error) });
        });
    }
});

chrome.runtime.onInstalled.addListener(() => {
    configureKeepaliveAlarm();
    ensureWebSocketConnected();
});

chrome.runtime.onStartup.addListener(() => {
    configureKeepaliveAlarm();
    ensureWebSocketConnected();
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message && message.type === "flow2api_reconnect") {
        closeSocket();
        connectWS()
            .then(() => sendResponse({ success: true }))
            .catch(error => sendResponse({ success: false, error: error.message }));
        return true;
    }
    if (message && message.type === "flow2api_open_oauth") {
        getOAuthSignInUrl().then(url => {
            const targetUrl = url || "https://labs.google/fx";
            chrome.tabs.create({ url: targetUrl, active: true });
            sendResponse({ success: true });
        }).catch(err => {
            chrome.tabs.create({ url: "https://labs.google/fx", active: true });
            sendResponse({ success: true });
        });
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
configureKeepaliveAlarm();
runScheduledAccountImport();
