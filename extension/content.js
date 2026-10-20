console.log("[Flow2API] Captcha & Auto-Login Worker v1.1.38 injected.");

// ==================== 1. reCAPTCHA Enterprise Token 获取 ====================
function getRecaptchaToken(action) {
    return new Promise((resolve, reject) => {
        const reqId = Date.now() + Math.random().toString();
        const script = document.createElement("script");
        script.textContent = `
            try {
                function runCaptcha() {
                    grecaptcha.enterprise.ready(function() {
                        grecaptcha.enterprise.execute('6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV', {action: '${action}'})
                            .then(token => window.postMessage({type: 'reCAPTCHA_result', reqId: '${reqId}', token: token}, '*'))
                            .catch(err => window.postMessage({type: 'reCAPTCHA_error', reqId: '${reqId}', error: err.message}, '*'));
                    });
                }
                
                if (typeof grecaptcha !== "undefined" && grecaptcha.enterprise) {
                    runCaptcha();
                } else {
                    window.postMessage({type: 'reCAPTCHA_error', reqId: '${reqId}', error: 'grecaptcha.enterprise is not loaded by Flow'}, '*');
                }
            } catch (e) {
                window.postMessage({type: 'reCAPTCHA_error', reqId: '${reqId}', error: e.message}, '*');
            }
        `;
        
        const listener = (event) => {
            if (event.source !== window || !event.data) return;
            if (event.data.reqId === reqId) {
                window.removeEventListener("message", listener);
                script.remove();
                if (event.data.type === 'reCAPTCHA_result') {
                    resolve(event.data.token);
                } else {
                    reject(new Error(event.data.error || "Unknown reCAPTCHA Error"));
                }
            }
        };
        window.addEventListener("message", listener);
        document.documentElement.appendChild(script);
        
        setTimeout(() => {
            window.removeEventListener("message", listener);
            script.remove();
            reject(new Error("Timeout generating reCAPTCHA"));
        }, 15000);
    });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "get_token") {
        console.log("[Flow2API] Generating token for action: " + message.action);
        getRecaptchaToken(message.action)
            .then(token => sendResponse({status: "success", token: token}))
            .catch(err => sendResponse({status: "error", error: err.message}));
        return true; 
    }
});

// ==================== 2. 通用交互辅助函数 ====================
function safeClick(element) {
    if (!element) return;
    try {
        element.focus();
        const mouseOpts = { bubbles: true, cancelable: true, view: window };
        element.dispatchEvent(new MouseEvent('mousedown', mouseOpts));
        element.dispatchEvent(new MouseEvent('mouseup', mouseOpts));
        element.dispatchEvent(new MouseEvent('click', mouseOpts));
        if (typeof element.click === 'function') {
            element.click();
        }
    } catch (e) {
        console.warn("[Flow2API] safeClick error:", e);
    }
}

function showFloatingBanner(text, duration = 3000) {
    try {
        const id = "flow2api-auto-login-banner";
        let banner = document.getElementById(id);
        if (!banner) {
            banner = document.createElement("div");
            banner.id = id;
            banner.style.cssText = "position:fixed;top:18px;left:50%;transform:translateX(-50%);z-index:999999;background:rgba(15,23,42,0.92);color:#fff;padding:8px 18px;border-radius:999px;font-size:12px;font-family:-apple-system,sans-serif;box-shadow:0 10px 25px rgba(0,0,0,0.3);backdrop-filter:blur(8px);display:flex;align-items:center;gap:8px;pointer-events:none;transition:all 0.3s ease;";
            document.body.appendChild(banner);
        }
        banner.innerHTML = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#10b981;"></span><span>${text}</span>`;
        banner.style.opacity = "1";
        setTimeout(() => {
            if (banner) {
                banner.style.opacity = "0";
                setTimeout(() => banner && banner.remove(), 400);
            }
        }, duration);
    } catch (e) {}
}

// ==================== 3. Google Labs / FX 页面自动登录 ====================
function runLabsAutoLogin() {
    const hostname = location.hostname;
    if (!hostname.includes("labs.google") && !hostname.includes("flow.google")) return;

    let hasClickedLogin = false;

    function checkLoginStatus() {
        // 1. 检查是否存在用户已登录头像
        const avatar = document.querySelector(
            'header img[src*="googleusercontent"], ' +
            'header button[aria-label*="Google 账号"], ' +
            'header button[aria-label*="Google Account"], ' +
            'button:has(img[src*="googleusercontent"])'
        );

        if (avatar) {
            // 已登录：提取当前登录的账号信息并存储
            const label = avatar.getAttribute("aria-label") || avatar.parentElement?.getAttribute("aria-label") || "";
            const emailMatch = label.match(/[\w\.-]+@[\w\.-]+\.\w+/);
            if (emailMatch) {
                const currentEmail = emailMatch[0].toLowerCase();
                chrome.storage.local.set({ lastLoggedInEmail: currentEmail, lastImportEmail: currentEmail });
            }

            // 如果刚完成登录跳转，通知 background 立即触发 token 自动导入
            if (sessionStorage.getItem("flow2api_pending_auto_import")) {
                sessionStorage.removeItem("flow2api_pending_auto_import");
                chrome.storage.local.set({ autoLoginInProgress: false });
                showFloatingBanner("✅ Flow2API: 登录成功，已自动同步最新凭据");
                chrome.runtime.sendMessage({ type: "auto_login_completed" });
            }
            return;
        }

        // 2. 如果已在执行中或已点过登录，避免重复点击
        if (hasClickedLogin) return;

        // 3. 查找右上角“使用 Google 账号登录”按钮
        const loginBtn = document.querySelector('button[aria-haspopup="dialog"]') ||
            Array.from(document.querySelectorAll('header button, header a')).find(el => {
                const txt = (el.innerText || "").trim();
                return txt.includes("使用 Google 账号登录") ||
                       txt.includes("Sign in with Google") ||
                       txt.includes("Sign in") ||
                       txt.includes("登录") ||
                       el.querySelector('[aria-label="Google 徽标"]');
            });

        if (loginBtn) {
            hasClickedLogin = true;
            console.log("[Flow2API] 检测到未登录状态，自动点击右上角登录按钮");
            showFloatingBanner("⚡ Flow2API: 检测到登录已过期，正在自动重新登录...");
            
            // 标记登录开始并记录时间戳（60秒内有效）
            sessionStorage.setItem("flow2api_pending_auto_import", "1");
            chrome.storage.local.set({ autoLoginInProgress: true, autoLoginTime: Date.now() });

            safeClick(loginBtn);

            // 监听弹出对话框 (Radix Dialog: role="dialog" 或 data-state="open")
            const observer = new MutationObserver(() => {
                const dialog = document.querySelector('[role="dialog"], [data-state="open"]');
                if (dialog) {
                    // 在弹窗中寻找居中的“登录”按钮
                    const modalLoginBtn = Array.from(dialog.querySelectorAll('button, a')).find(btn => {
                        const t = (btn.innerText || "").trim();
                        return t.includes("登录") || t.includes("Sign in") || btn.querySelector('[aria-label="Google 徽标"]');
                    }) || dialog.querySelector('button');

                    if (modalLoginBtn) {
                        observer.disconnect();
                        console.log("[Flow2API] 检测到登录授权弹窗，自动确认登录");
                        setTimeout(() => {
                            safeClick(modalLoginBtn);
                        }, 350);
                    }
                }
            });

            observer.observe(document.body, { childList: true, subtree: true });
            setTimeout(() => observer.disconnect(), 10000);
        }
    }

    // 页面加载或动态渲染时检测
    setTimeout(checkLoginStatus, 1000);
    setTimeout(checkLoginStatus, 2500);
    setTimeout(checkLoginStatus, 5000);
}

// ==================== 4. Google 账号选择页（多账号自动识别与选择） ====================
function runGoogleAccountChooser() {
    if (location.hostname !== "accounts.google.com") return;

    chrome.storage.local.get(
        ["autoLoginInProgress", "autoLoginTime", "targetLoginEmail", "lastImportEmail", "lastLoggedInEmail"],
        (stored) => {
            const now = Date.now();
            const isInProgress = stored.autoLoginInProgress && (now - (stored.autoLoginTime || 0)) < 90000;
            const pageText = document.body ? document.body.innerText || "" : "";
            const isInstaCloudOrLabs = pageText.includes("InstaCloud") || pageText.includes("labs.google") || pageText.includes("Flow");

            // 安全守卫：仅在确实是为 Flow2API / Google Labs 登录，且当前处于“选择账号”页面时生效
            if (!isInProgress && !isInstaCloudOrLabs) return;

            const isChooserPage = pageText.includes("选择账号") ||
                                  pageText.includes("Choose an account") ||
                                  document.querySelector('[data-identifier], [data-email], li[data-authuser]');

            if (!isChooserPage) return;

            // 确定目标账号邮箱（优先用户设置的 targetLoginEmail，其次上次导入的 lastImportEmail / lastLoggedInEmail）
            const targetEmail = (stored.targetLoginEmail || stored.lastImportEmail || stored.lastLoggedInEmail || "").trim().toLowerCase();
            console.log("[Flow2API] 进入 Google 多账号选择页，目标账号:", targetEmail || "未指定");

            // 为页面上所有账号项绑定点击监听，方便用户若手动改选时自动记住最新账号
            attachAccountClickRecorder();

            if (!targetEmail) {
                console.log("[Flow2API] 暂未记录上次登录邮箱，等待用户点击选择");
                return;
            }

            // 寻找匹配该邮箱的账号卡片
            function tryClickTargetAccount() {
                const accountEl = findAccountElement(targetEmail);
                if (accountEl) {
                    console.log(`[Flow2API] 匹配到目标账号 [${targetEmail}]，等候片刻后自动点击`);
                    showFloatingBanner(`⚡ Flow2API: 正在自动选择上次绑定的账号 [${targetEmail}]...`);

                    // 稍作等待（约 900ms），确保页面组件水合完成、事件监听就绪
                    setTimeout(() => {
                        safeClick(accountEl);
                    }, 900);
                    return true;
                }
                return false;
            }

            if (!tryClickTargetAccount()) {
                // 若列表仍在异步渲染，监听并在 500ms 内重试
                const observer = new MutationObserver(() => {
                    if (tryClickTargetAccount()) {
                        observer.disconnect();
                    }
                });
                observer.observe(document.body, { childList: true, subtree: true });
                setTimeout(() => observer.disconnect(), 6000);
            }
        }
    );
}

// 辅助：从 DOM 中精准匹配对应 Email 的账号元素
function findAccountElement(email) {
    if (!email) return null;
    const cleanEmail = email.toLowerCase().trim();

    // 策略 1：通过属性匹配 (data-identifier / data-email)
    const attrMatch = document.querySelector(`[data-identifier*="${cleanEmail}" i], [data-email*="${cleanEmail}" i]`);
    if (attrMatch) {
        return attrMatch.closest('li, div[role="link"], div[role="button"], button, div[jsaction]') || attrMatch;
    }

    // 策略 2：叶子节点纯文本严格匹配
    const leafNodes = Array.from(document.querySelectorAll('div, span, p, b')).filter(el => {
        return el.children.length === 0 && (el.textContent || "").trim().toLowerCase() === cleanEmail;
    });
    if (leafNodes.length > 0) {
        return leafNodes[0].closest('li, div[role="link"], div[role="button"], button, div[jsaction]') || leafNodes[0];
    }

    // 策略 3：包含匹配
    const subNodes = Array.from(document.querySelectorAll('div, span, p, b')).filter(el => {
        return el.children.length === 0 && (el.textContent || "").toLowerCase().includes(cleanEmail);
    });
    if (subNodes.length > 0) {
        return subNodes[0].closest('li, div[role="link"], div[role="button"], button, div[jsaction]') || subNodes[0];
    }

    return null;
}

// 辅助：监听用户在该页面上的手动点击，自动记录选中的账号
function attachAccountClickRecorder() {
    document.addEventListener("click", (e) => {
        try {
            const target = e.target;
            const container = target.closest('li, div[role="link"], div[role="button"], button');
            if (container) {
                const text = container.innerText || "";
                const emailMatch = text.match(/[\w\.-]+@[\w\.-]+\.\w+/);
                if (emailMatch) {
                    const pickedEmail = emailMatch[0].toLowerCase();
                    console.log("[Flow2API] 记录用户选中的 Google 账号:", pickedEmail);
                    chrome.storage.local.set({ lastImportEmail: pickedEmail, lastLoggedInEmail: pickedEmail });
                }
            }
        } catch (err) {}
    }, { capture: true, passive: true });
}

// ==================== 5. 初始化执行 ====================
if (location.hostname.includes("labs.google") || location.hostname.includes("flow.google")) {
    runLabsAutoLogin();
} else if (location.hostname === "accounts.google.com") {
    runGoogleAccountChooser();
}
