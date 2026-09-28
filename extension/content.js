console.log("[Flow2API] Captcha Worker injected.");

function getRecaptchaToken(action, timeoutMs = 25000) {
    return new Promise((resolve, reject) => {
        const reqId = Date.now() + Math.random().toString();
        const script = document.createElement("script");
        script.textContent = `
            (async () => {
                const siteKey = '6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV';
                const deadline = Date.now() + 15000;
                while (!(window.grecaptcha && window.grecaptcha.enterprise) && Date.now() < deadline) {
                    await new Promise(r => setTimeout(r, 250));
                }
                if (!(window.grecaptcha && window.grecaptcha.enterprise)) {
                    window.postMessage({type: 'reCAPTCHA_error', reqId: '${reqId}', error: 'grecaptcha.enterprise is not loaded by Flow'}, '*');
                    return;
                }
                try {
                    await new Promise(r => setTimeout(r, 4000));
                    grecaptcha.enterprise.ready(() => {
                        grecaptcha.enterprise.execute(siteKey, {action: '${action}'})
                            .then(token => {
                                let pid = '';
                                const m = location.pathname.match(/\\/project\\/([0-9a-fA-F-]+)/);
                                if (m) pid = m[1];
                                window.postMessage({
                                    type: 'reCAPTCHA_result',
                                    reqId: '${reqId}',
                                    token: token,
                                    userAgent: navigator.userAgent,
                                    origin: location.origin,
                                    href: location.href,
                                    projectId: pid
                                }, '*');
                            })
                            .catch(err => window.postMessage({type: 'reCAPTCHA_error', reqId: '${reqId}', error: err.message || String(err)}, '*'));
                    });
                } catch (e) {
                    window.postMessage({type: 'reCAPTCHA_error', reqId: '${reqId}', error: e.message || String(e)}, '*');
                }
            })();
        `;
        
        const listener = (event) => {
            if (event.source !== window || !event.data) return;
            if (event.data.reqId === reqId) {
                window.removeEventListener("message", listener);
                script.remove();
                if (event.data.type === 'reCAPTCHA_result') {
                    resolve(event.data);
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
            reject(new Error("Timeout generating reCAPTCHA via content script"));
        }, timeoutMs);
    });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "get_token") {
        getRecaptchaToken(message.action || "IMAGE_GENERATION")
            .then(data => sendResponse({status: "success", ...data}))
            .catch(err => sendResponse({status: "error", error: err.message}));
        return true; 
    }
});
