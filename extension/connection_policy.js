(function (root) {
    const KEEPALIVE_PERIOD_MINUTES = 0.5;

    function shouldReconnectWebSocket(readyState) {
        return readyState !== 0 && readyState !== 1;
    }

    function shouldStartWebSocketConnection(readyState, startPending) {
        return !startPending && shouldReconnectWebSocket(readyState);
    }

    function isCurrentSocket(currentSocket, eventSocket) {
        return currentSocket === eventSocket;
    }

    const api = {
        KEEPALIVE_PERIOD_MINUTES,
        shouldReconnectWebSocket,
        shouldStartWebSocketConnection,
        isCurrentSocket,
    };
    root.FlowConnectionPolicy = api;
    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    }
})(typeof globalThis !== "undefined" ? globalThis : this);
