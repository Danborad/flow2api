(function (root) {
    const PROJECT_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

    function getFlowProjectFromTabUrl(rawUrl) {
        try {
            const url = new URL(String(rawUrl || ""));
            const isNewFlow = url.hostname === "flow.google.com";
            const isLegacyFlow = url.hostname === "labs.google";
            if (!isNewFlow && !isLegacyFlow) return "";

            const pathMatch = url.pathname.match(/\/(?:fx\/tools\/flow\/)?projects?\/([^/?#]+)/i);
            const queryProjectId = (
                url.searchParams.get("projectId")
                || url.searchParams.get("project_id")
                || ""
            );
            const hashMatch = url.hash.match(/(?:projects?\/|projectId=)([0-9a-f-]{36})/i);
            const projectId = decodeURIComponent(
                (pathMatch && pathMatch[1])
                || queryProjectId
                || (hashMatch && hashMatch[1])
                || ""
            ).trim();
            return PROJECT_ID_PATTERN.test(projectId) ? projectId.toLowerCase() : "";
        } catch (error) {
            return "";
        }
    }

    function normalizeProjectName(rawTitle) {
        return String(rawTitle || "")
            .replace(/\s*[-|]\s*Google Flow.*$/i, "")
            .trim()
            .slice(0, 80);
    }

    function selectCurrentFlowProject(tabs) {
        const candidates = (Array.isArray(tabs) ? tabs : [])
            .flatMap((tab) => {
                const urls = [
                    tab && tab.pendingUrl,
                    tab && tab.url,
                    ...((tab && Array.isArray(tab.discoveredUrls)) ? tab.discoveredUrls : []),
                ];
                const projectId = urls.map(getFlowProjectFromTabUrl).find(Boolean) || "";
                return [{
                    projectId,
                    projectName: normalizeProjectName(tab && tab.title),
                    active: Boolean(tab && tab.active),
                    lastAccessed: Number((tab && tab.lastAccessed) || 0),
                }];
            })
            .filter((candidate) => candidate.projectId)
            .sort((left, right) => (
                Number(right.active) - Number(left.active)
                || right.lastAccessed - left.lastAccessed
            ));

        return candidates[0] || null;
    }

    function buildCreateProjectEnvelope(projectName) {
        const title = String(projectName || "").trim();
        const request = ["projects/*", [null, [title]], [null, 22]];
        return [[["jHPbke", JSON.stringify(request), null, "generic"]]];
    }

    function extractCreatedProjectId(responseText) {
        const matches = String(responseText || "").match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi);
        return matches && matches.length ? matches[0].toLowerCase() : "";
    }

    const api = {
        getFlowProjectFromTabUrl,
        selectCurrentFlowProject,
        buildCreateProjectEnvelope,
        extractCreatedProjectId,
    };
    root.FlowProjectUrl = api;
    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    }
})(typeof globalThis !== "undefined" ? globalThis : this);
