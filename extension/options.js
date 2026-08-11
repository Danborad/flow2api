const DEFAULT_SETTINGS = {
  serverUrl: "ws://127.0.0.1:8000/captcha_ws",
  apiKey: "",
  instanceId: "",
  routeKey: "",
  clientLabel: "",
  refreshIntervalMinutes: "120",
  autoImportEnabled: true,
  autoImportIntervalMinutes: "30",
  lastAutoImportAt: "",
  lastAutoImportStatus: "",
  lastAutoImportMessage: ""
};

const $ = (id) => document.getElementById(id);

function createInstanceId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}

function ensureInstanceSettings(stored) {
  const instanceId = stored.instanceId || createInstanceId();
  const shortId = instanceId.replace(/-/g, "").slice(0, 12);
  const settings = {
    instanceId,
    routeKey: stored.routeKey && stored.instanceId ? stored.routeKey : `flow-${shortId}`,
    clientLabel: stored.clientLabel && stored.instanceId ? stored.clientLabel : `chrome-${shortId}`
  };
  if (!stored.instanceId || !stored.routeKey || !stored.clientLabel) {
    chrome.storage.local.set(settings);
  }
  return settings;
}

function normalizeSettings(values) {
  return {
    serverUrl: (values.serverUrl || DEFAULT_SETTINGS.serverUrl).trim(),
    apiKey: (values.apiKey || DEFAULT_SETTINGS.apiKey).trim(),
    instanceId: (values.instanceId || DEFAULT_SETTINGS.instanceId).trim(),
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
    ensureInstanceSettings(stored);
    const settings = normalizeSettings(stored);
    $("serverUrl").value = settings.serverUrl;
    $("apiKey").value = settings.apiKey;
    $("refreshIntervalMinutes").value = settings.refreshIntervalMinutes;
    $("autoImportEnabled").checked = settings.autoImportEnabled;
    $("autoImportIntervalMinutes").value = settings.autoImportIntervalMinutes;
    renderAutoImportStatus(stored);
  });
}

function saveSettings() {
  chrome.storage.local.get(DEFAULT_SETTINGS, (stored) => {
    const instanceSettings = ensureInstanceSettings(stored);
    const settings = normalizeSettings({
      ...instanceSettings,
      serverUrl: $("serverUrl").value,
      apiKey: $("apiKey").value,
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
  });
}

async function importCurrentAccount() {
  const importBtn = $("importBtn");
  importBtn.disabled = true;
  setStatus("正在导入当前浏览器账号...");

  try {
    const stored = await new Promise((resolve) => chrome.storage.local.get(DEFAULT_SETTINGS, resolve));
    const instanceSettings = ensureInstanceSettings(stored);
    const settings = normalizeSettings({
      ...stored,
      ...instanceSettings,
      serverUrl: $("serverUrl").value,
      apiKey: $("apiKey").value,
      refreshIntervalMinutes: $("refreshIntervalMinutes").value,
      autoImportEnabled: $("autoImportEnabled").checked,
      autoImportIntervalMinutes: $("autoImportIntervalMinutes").value
    });
    if (!isValidWsUrl(settings.serverUrl)) {
      throw new Error("WebSocket URL 必须以 ws:// 或 wss:// 开头。");
    }
    if (!settings.apiKey) {
      throw new Error("请填写 Flow2API API Key。");
    }
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
  $("extensionVersion").textContent = `v${chrome.runtime.getManifest().version}`;
  loadSettings();
  $("saveBtn").addEventListener("click", saveSettings);
  $("importBtn").addEventListener("click", importCurrentAccount);
});
