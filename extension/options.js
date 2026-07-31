const DEFAULT_SETTINGS = {
  serverUrl: "ws://127.0.0.1:8000/captcha_ws",
  apiKey: "",
  routeKey: "flow-main",
  clientLabel: "chrome-flow-main",
  refreshIntervalMinutes: "120",
  autoImportEnabled: true,
  autoImportIntervalMinutes: "30",
  lastAutoImportAt: "",
  lastAutoImportStatus: "",
  lastAutoImportMessage: ""
};

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

const $ = (id) => document.getElementById(id);

function normalizeSettings(values) {
  return {
    serverUrl: (values.serverUrl || DEFAULT_SETTINGS.serverUrl).trim(),
    apiKey: (values.apiKey || DEFAULT_SETTINGS.apiKey).trim(),
    routeKey: (values.routeKey || DEFAULT_SETTINGS.routeKey).trim(),
    clientLabel: (values.clientLabel || DEFAULT_SETTINGS.clientLabel).trim(),
    refreshIntervalMinutes: String(values.refreshIntervalMinutes || DEFAULT_SETTINGS.refreshIntervalMinutes).trim(),
    autoImportEnabled: values.autoImportEnabled !== false,
    autoImportIntervalMinutes: String(values.autoImportIntervalMinutes || DEFAULT_SETTINGS.autoImportIntervalMinutes).trim()
  };
}

function renderAutoImportStatus(stored) {
  const el = $("lastAutoImportStatus");
  if (!el) return;
  const at = stored.lastAutoImportAt || "";
  const status = stored.lastAutoImportStatus || "";
  const message = stored.lastAutoImportMessage || "";
  if (!at) {
    el.textContent = "自动导入状态：尚未运行";
    return;
  }
  const time = new Date(at).toLocaleString("zh-CN", { hour12: false });
  el.textContent = `自动导入状态：${status === "success" ? "成功" : "失败"}，${time}${message ? `，${message}` : ""}`;
}

function setStatus(message, isError = false) {
  const status = $("status");
  status.textContent = message;
  status.style.color = isError ? "#b91c1c" : "#065f46";
}

function isValidWsUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "ws:" || url.protocol === "wss:";
  } catch (e) {
    return false;
  }
}

function loadSettings() {
  chrome.storage.local.get(DEFAULT_SETTINGS, (stored) => {
    const settings = normalizeSettings(stored);
    $("serverUrl").value = settings.serverUrl;
    $("apiKey").value = settings.apiKey;
    $("routeKey").value = settings.routeKey;
    $("clientLabel").value = settings.clientLabel;
    $("refreshIntervalMinutes").value = settings.refreshIntervalMinutes;
    $("autoImportEnabled").checked = settings.autoImportEnabled;
    $("autoImportIntervalMinutes").value = settings.autoImportIntervalMinutes;
    renderAutoImportStatus(stored);
  });
}

function saveSettings() {
  const settings = normalizeSettings({
    serverUrl: $("serverUrl").value,
    apiKey: $("apiKey").value,
    routeKey: $("routeKey").value,
    clientLabel: $("clientLabel").value,
    refreshIntervalMinutes: $("refreshIntervalMinutes").value,
    autoImportEnabled: $("autoImportEnabled").checked,
    autoImportIntervalMinutes: $("autoImportIntervalMinutes").value
  });

  if (!isValidWsUrl(settings.serverUrl)) {
    setStatus("WebSocket URL 必须以 ws:// 或 wss:// 开头。", true);
    return;
  }
  if (!settings.apiKey) {
    setStatus("请填写 Flow2API API Key。", true);
    return;
  }

  chrome.storage.local.set(settings, () => {
    if (chrome.runtime.lastError) {
      setStatus(`保存失败：${chrome.runtime.lastError.message}`, true);
      return;
    }
    setStatus("已保存，后台连接会自动重连。");
  });
}

function getBackendBaseUrl(serverUrl) {
  const url = new URL(serverUrl || DEFAULT_SETTINGS.serverUrl);
  if (url.protocol === "ws:") {
    url.protocol = "http:";
  } else if (url.protocol === "wss:") {
    url.protocol = "https:";
  } else {
    throw new Error("WebSocket URL 必须以 ws:// 或 wss:// 开头。");
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
    if (cookie && cookie.value) {
      return cookie.value;
    }
  }
  return "";
}

async function getGoogleCookies() {
  const cookieMap = new Map();
  const urls = [
    "https://accounts.google.com/",
    "https://www.google.com/",
    "https://google.com/",
    "https://ogs.google.com/"
  ];

  for (const url of urls) {
    const cookies = await getCookies({ url });
    for (const cookie of cookies) {
      if (!GOOGLE_COOKIE_NAMES.includes(cookie.name) || !cookie.value) {
        continue;
      }
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

async function loginAdmin(baseUrl, username, password) {
  const response = await fetch(`${baseUrl}/api/admin/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (e) {
    payload = null;
  }
  if (!response.ok || !payload || !payload.token) {
    const detail = payload && (payload.detail || payload.message);
    throw new Error(detail || `后台登录失败 HTTP ${response.status}`);
  }
  return payload.token;
}

async function importCurrentAccount() {
  const settings = normalizeSettings({
    serverUrl: $("serverUrl").value,
    apiKey: $("apiKey").value,
    routeKey: $("routeKey").value,
    clientLabel: $("clientLabel").value,
    refreshIntervalMinutes: $("refreshIntervalMinutes").value,
    autoImportEnabled: $("autoImportEnabled").checked,
    autoImportIntervalMinutes: $("autoImportIntervalMinutes").value
  });
  const importBtn = $("importBtn");

  if (!isValidWsUrl(settings.serverUrl)) {
    setStatus("WebSocket URL 必须以 ws:// 或 wss:// 开头。", true);
    return;
  }
  importBtn.disabled = true;
  setStatus("正在导入当前浏览器账号...");

  try {
    await new Promise((resolve, reject) => {
      chrome.storage.local.set(settings, () => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        resolve();
      });
    });
    const response = await chrome.runtime.sendMessage({ type: "flow2api_import_current_account" });
    if (!response || response.success !== true) {
      throw new Error((response && response.error) || "插件后台没有返回导入结果");
    }
    const payload = response.payload || {};
    setStatus(`导入完成，新增 ${payload.added || 0}，更新 ${payload.updated || 0}，账号 ${payload.email || "未知"}`);
    chrome.storage.local.get(DEFAULT_SETTINGS, renderAutoImportStatus);
  } catch (e) {
    setStatus(`导入失败：${e.message || e}`, true);
  } finally {
    importBtn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  $("saveBtn").addEventListener("click", saveSettings);
  $("importBtn").addEventListener("click", importCurrentAccount);
});
