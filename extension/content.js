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
    if (message.type === "input_flow_prompt") {
        inputFlowPrompt(message.prompt)
            .then(res => sendResponse({ status: "success", result: res }))
            .catch(err => sendResponse({ status: "error", error: err.message || String(err) }));
        return true;
    }
    if (message.type === "wait_flow_image") {
        waitFlowImage(message.initialCleanPaths, message.initialCount, message.timeout_seconds || 90)
            .then(res => sendResponse({ status: "success", result: res }))
            .catch(err => sendResponse({ status: "error", error: err.message || String(err) }));
        return true;
    }
    if (message.type === "execute_flow_generate") {
        console.log("[Flow2API] 收到原生生成任务:", message.prompt);
        executeFlowGeneration(message)
            .then(res => sendResponse({ status: "success", result: res }))
            .catch(err => sendResponse({ status: "error", error: err.message || String(err) }));
        return true;
    }
});

// 提取当前页面已有的图片路径列表
function getCurrentImagePaths() {
    return Array.from(document.querySelectorAll('img[src*="flow-content.google/image/"]')).map(i => i.src);
}

// 步骤 1: 定位输入框并填充提示词
async function inputFlowPrompt(prompt) {
    prompt = (prompt || "").trim();
    if (!prompt) throw new Error("提示词不能为空");

    showFloatingBanner(`⚡ Flow2API: 正在输入: ${prompt.slice(0, 20)}...`, 4000);

    const initialImgs = getCurrentImagePaths();
    const initialCleanPaths = initialImgs.map(s => s.split('?')[0]);
    const initialCount = initialImgs.length;

    let inputEl = null;
    const findDeadline = Date.now() + 15000;
    while (Date.now() < findDeadline) {
        inputEl = document.querySelector('flow-prompt-box .ProseMirror') ||
                  document.querySelector('.ProseMirror') ||
                  document.querySelector('flow-prompt-box div[contenteditable="true"]') ||
                  document.querySelector('flow-prompt-box [contenteditable="true"]') ||
                  document.querySelector('div[contenteditable="true"][role="textbox"]') ||
                  document.querySelector('div[contenteditable="true"]') ||
                  document.querySelector('flow-prompt-box textarea') ||
                  document.querySelector('textarea');
        if (inputEl) break;
        await new Promise(r => setTimeout(r, 200));
    }

    if (!inputEl) throw new Error("未能定位到 Flow 提示词输入框");

    inputEl.focus();
    if (inputEl.isContentEditable || inputEl.getAttribute('contenteditable') === 'true') {
        const sel = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(inputEl);
        sel.removeAllRanges();
        sel.addRange(range);
        document.execCommand('delete', false, null);

        inputEl.dispatchEvent(new InputEvent('beforeinput', { inputType: 'insertText', data: prompt, bubbles: true, cancelable: true }));
        document.execCommand('insertText', false, prompt);
        inputEl.dispatchEvent(new InputEvent('input', { inputType: 'insertText', data: prompt, bubbles: true }));
    } else {
        inputEl.value = prompt;
        inputEl.dispatchEvent(new Event('input', { bubbles: true }));
        inputEl.dispatchEvent(new Event('change', { bubbles: true }));
    }

    return { success: true, initialCleanPaths, initialCount };
}

// 步骤 2: 轮询等待新生成的图片
async function waitFlowImage(initialCleanPaths, initialCount, timeoutSeconds) {
    const existingCleanPaths = new Set(initialCleanPaths || []);
    initialCount = typeof initialCount === 'number' ? initialCount : existingCleanPaths.size;
    const waitDeadline = Date.now() + (timeoutSeconds || 90) * 1000;

    console.log("[Flow2API] 开始监听生成结果，初始图片数:", initialCount);

    while (Date.now() < waitDeadline) {
        await new Promise(r => setTimeout(r, 500));
        const currentImgs = Array.from(document.querySelectorAll('img[src*="flow-content.google/image/"]')).map(i => i.src);

        // 策略 A: 纯净路径比对 (忽略签名参数差异，优先命中排在最前的新图)
        for (const src of currentImgs) {
            const cleanPath = src.split('?')[0];
            if (!existingCleanPaths.has(cleanPath)) {
                console.log("[Flow2API] 成功捕获到新生成的图片 (纯净路径比对):", src);
                showFloatingBanner("✅ Flow2API: 图片生成成功！", 3000);
                return { success: true, url: src };
            }
        }

        // 策略 B: 图片总数增长检测 (提取新增的首项)
        if (currentImgs.length > initialCount) {
            const cand = currentImgs[0];
            if (cand) {
                console.log("[Flow2API] 成功通过数量增长捕获到新图片:", cand);
                showFloatingBanner("✅ Flow2API: 图片生成成功！", 3000);
                return { success: true, url: cand };
            }
        }
    }

    throw new Error(`等待 Flow 生成超时 (${timeoutSeconds}s)`);
}

// ==================== 原生 Flow 页面驱动生成 ====================
async function executeFlowGeneration(data) {
    const prompt = (data.prompt || "").trim();
    if (!prompt) throw new Error("提示词不能为空");

    showFloatingBanner(`⚡ Flow2API: 正在执行原生生成: ${prompt.slice(0, 20)}...`, 5000);

    // 记录生成前的已有图片集合 (同时记录完整 URL 与纯净路径)
    const rawExisting = Array.from(document.querySelectorAll('img[src*="flow-content.google/image/"]')).map(i => i.src);
    const existingImages = new Set(rawExisting);
    const existingCleanPaths = new Set(rawExisting.map(s => s.split('?')[0]));
    const initialCount = rawExisting.length;

    // 1. 轮询查找输入框 (最多等待 15 秒，兼容 Angular 慢加载)
    let inputEl = null;
    const findDeadline = Date.now() + 15000;
    while (Date.now() < findDeadline) {
        inputEl = document.querySelector('flow-prompt-box .ProseMirror') ||
                  document.querySelector('.ProseMirror') ||
                  document.querySelector('flow-prompt-box div[contenteditable="true"]') ||
                  document.querySelector('flow-prompt-box [contenteditable="true"]') ||
                  document.querySelector('div[contenteditable="true"][role="textbox"]') ||
                  document.querySelector('div[contenteditable="true"]') ||
                  document.querySelector('flow-prompt-box textarea') ||
                  document.querySelector('textarea');
        if (inputEl) break;
        await new Promise(r => setTimeout(r, 250));
    }

    if (!inputEl) {
        throw new Error("未能定位到 Flow 提示词输入框 (等待 15s 超时)");
    }

    // 2. 聚焦并清空输入，模拟原生输入事件激活 ProseMirror
    try {
        inputEl.focus();
        if (inputEl.isContentEditable || inputEl.getAttribute('contenteditable') === 'true') {
            const sel = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(inputEl);
            sel.removeAllRanges();
            sel.addRange(range);
            document.execCommand('delete', false, null);

            // 模拟原生输入事件组合拳激活状态机
            inputEl.dispatchEvent(new InputEvent('beforeinput', { inputType: 'insertText', data: prompt, bubbles: true, cancelable: true }));
            document.execCommand('insertText', false, prompt);
            inputEl.dispatchEvent(new InputEvent('input', { inputType: 'insertText', data: prompt, bubbles: true }));
        } else {
            inputEl.value = prompt;
            inputEl.dispatchEvent(new Event('input', { bubbles: true }));
            inputEl.dispatchEvent(new Event('change', { bubbles: true }));
        }
    } catch (e) {
        console.warn("[Flow2API] 提示词填充异常，尝试兜底注入:", e);
        try { document.execCommand('insertText', false, prompt); } catch (_) {}
    }

    // 3. 等待生成按钮并确保启用状态 (最多等 8 秒)
    let genBtn = null;
    const btnDeadline = Date.now() + 8000;
    while (Date.now() < btnDeadline) {
        genBtn = document.querySelector('flow-prompt-box button.generate-icon-button') ||
                 document.querySelector('button.generate-icon-button') ||
                 document.querySelector('flow-prompt-box button[aria-label*="Generate" i]') ||
                 document.querySelector('flow-prompt-box button[aria-label*="生成" i]');
        if (genBtn && !genBtn.disabled) break;
        // 追加微输入空格激活
        inputEl.dispatchEvent(new InputEvent('beforeinput', { inputType: 'insertText', data: ' ', bubbles: true, cancelable: true }));
        document.execCommand('insertText', false, ' ');
        inputEl.dispatchEvent(new InputEvent('input', { inputType: 'insertText', data: ' ', bubbles: true }));
        await new Promise(r => setTimeout(r, 250));
    }

    if (genBtn && genBtn.disabled) {
        console.warn("[Flow2API] 强制解锁生成按钮 disabled 属性");
        genBtn.removeAttribute('disabled');
        genBtn.disabled = false;
        genBtn.classList.remove('mat-mdc-button-disabled');
    }

    if (!genBtn) {
        throw new Error("未能定位到生成按钮");
    }

    // 4. 触发提交生成 (物理回车 + 物理点击 + 原生 safeClick 组合拳)
    try {
        await new Promise(r => chrome.runtime.sendMessage({ type: "do_physical_enter" }, r));
    } catch (_) {}
    try {
        await new Promise(r => chrome.runtime.sendMessage({ type: "do_physical_click" }, r));
    } catch (_) {}
    safeClick(genBtn);
    console.log("[Flow2API] 已成功派发生成指令，开始监听生成结果...");

    // 5. 监听最新生成的图片出现 (最多等待 90 秒)
    const waitDeadline = Date.now() + 90000;
    while (Date.now() < waitDeadline) {
        await new Promise(r => setTimeout(r, 600));
        const currentImgs = Array.from(document.querySelectorAll('img[src*="flow-content.google/image/"]')).map(i => i.src);

        // 策略 A: 纯净路径比对 (忽略签名参数差异)
        for (const src of currentImgs) {
            const cleanPath = src.split('?')[0];
            if (!existingCleanPaths.has(cleanPath)) {
                console.log("[Flow2API] 成功捕获到新生成的图片 (纯净路径比对):", src);
                showFloatingBanner("✅ Flow2API: 图片生成成功！", 3000);
                return { success: true, url: src };
            }
        }

        // 策略 B: 图片总数增长检测 (提取新增的首项或末项)
        if (currentImgs.length > initialCount) {
            for (const cand of [currentImgs[0], currentImgs[currentImgs.length - 1]]) {
                if (cand && !existingImages.has(cand)) {
                    console.log("[Flow2API] 成功通过数量增长捕获到新图片:", cand);
                    showFloatingBanner("✅ Flow2API: 图片生成成功！", 3000);
                    return { success: true, url: cand };
                }
            }
        }

        // 策略 C: 检查背景样式
        const bgElements = Array.from(document.querySelectorAll('[style*="flow-content.google/image/"]'));
        for (const el of bgElements) {
            const m = el.style.backgroundImage.match(/url\(["']?(https:\/\/flow-content\.google\/image\/[^"'\)]+)["']?\)/);
            if (m && !existingCleanPaths.has(m[1].split('?')[0])) {
                console.log("[Flow2API] 成功从背景样式捕获到图片:", m[1]);
                showFloatingBanner("✅ Flow2API: 图片生成成功！", 3000);
                return { success: true, url: m[1] };
            }
        }
    }

    throw new Error("等待 Flow 生成超时 (90s)");
}

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

        // 3. 查找右上角“使用 Google 账号登录”按钮 / flow.google.com 的 Sign in 链接
        const loginBtn = document.querySelector('a[href*="ServiceLogin"], .gb_6a, button[aria-haspopup="dialog"]') ||
            Array.from(document.querySelectorAll('header button, header a, div.gb_Od a, a.gb_6a, .boqOnegoogleliteOgbOneGoogleBar a')).find(el => {
                const txt = (el.innerText || "").trim();
                const aria = (el.getAttribute("aria-label") || "").trim();
                return txt.includes("使用 Google 账号登录") ||
                       txt.includes("Sign in with Google") ||
                       txt.includes("Sign in") ||
                       txt.includes("登录") ||
                       aria.includes("Sign in") ||
                       aria.includes("登录") ||
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
