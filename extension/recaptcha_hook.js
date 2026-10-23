(() => {
    // 拦截并剥离 Google Flow 前端 kUa 埋下的 extension_hijack_detected 毒丸
    if (!window.__origObjectAssign) {
        window.__origObjectAssign = Object.assign;
        Object.assign = function(target, ...sources) {
            const cleanSources = sources.map(s => (s && typeof s === "object" && s.action === "extension_hijack_detected") ? {} : s);
            return window.__origObjectAssign.call(this, target, ...cleanSources);
        };
    }

    const slot = "__flow2apiRealRecaptchaExecute";
    if (Object.prototype.hasOwnProperty.call(window, slot)) return;

    let realExecute = null;
    Object.defineProperty(window, slot, {
        configurable: false,
        enumerable: false,
        get() {
            return realExecute;
        },
    });

    const hookExecute = (enterprise) => {
        if (!enterprise || typeof enterprise !== "object") return;
        const descriptor = Object.getOwnPropertyDescriptor(enterprise, "execute");
        if (descriptor && !descriptor.configurable) return;

        let current = descriptor && "value" in descriptor
            ? descriptor.value
            : descriptor && descriptor.get
                ? descriptor.get.call(enterprise)
                : undefined;
        if (typeof current === "function" && !realExecute) {
            realExecute = current.bind(enterprise);
        }

        Object.defineProperty(enterprise, "execute", {
            configurable: true,
            enumerable: true,
            get() {
                return current;
            },
            set(value) {
                if (typeof value === "function" && !realExecute) {
                    realExecute = value.bind(enterprise);
                }
                current = value;
            },
        });
    };

    const hookEnterprise = (grecaptcha) => {
        if (!grecaptcha || typeof grecaptcha !== "object") return;
        const descriptor = Object.getOwnPropertyDescriptor(grecaptcha, "enterprise");
        if (descriptor && !descriptor.configurable) return;

        let current = descriptor && "value" in descriptor ? descriptor.value : undefined;
        if (current) hookExecute(current);
        Object.defineProperty(grecaptcha, "enterprise", {
            configurable: true,
            enumerable: true,
            get() {
                return current;
            },
            set(value) {
                current = value;
                hookExecute(value);
            },
        });
    };

    const descriptor = Object.getOwnPropertyDescriptor(window, "grecaptcha");
    let current = descriptor && "value" in descriptor ? descriptor.value : undefined;
    if (current) hookEnterprise(current);
    Object.defineProperty(window, "grecaptcha", {
        configurable: true,
        enumerable: true,
        get() {
            return current;
        },
        set(value) {
            current = value;
            hookEnterprise(value);
        },
    });
})();
