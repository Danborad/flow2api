(function (root) {
    const MINT_PAGE_URL = "https://flow.google.com/about";

    function getEnterpriseScriptUrl(siteKey) {
        return `https://www.google.com/recaptcha/enterprise.js?trustedtypes=true&render=${encodeURIComponent(siteKey)}`;
    }

    const api = { MINT_PAGE_URL, getEnterpriseScriptUrl };
    root.FlowRecaptchaPolicy = api;
    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    }
})(typeof globalThis !== "undefined" ? globalThis : this);
