import json
import subprocess
import unittest
from pathlib import Path


POLICY_SCRIPT = Path(__file__).parent.parent / "extension" / "connection_policy.js"


def run_policy(expression: str):
    script = (
        f"const policy = require({json.dumps(str(POLICY_SCRIPT))});"
        f"console.log(JSON.stringify({expression}));"
    )
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class ExtensionConnectionPolicyTests(unittest.TestCase):
    def test_reconnects_only_when_socket_is_not_open_or_connecting(self):
        states = [None, 0, 1, 2, 3]
        result = run_policy(
            f"{json.dumps(states)}.map(policy.shouldReconnectWebSocket)"
        )

        self.assertEqual(result, [True, False, False, True, True])

    def test_keepalive_alarm_uses_chrome_minimum_period(self):
        result = run_policy("policy.KEEPALIVE_PERIOD_MINUTES")

        self.assertEqual(result, 0.5)

    def test_connection_start_is_blocked_while_another_start_is_pending(self):
        states = [None, 0, 1, 2, 3]
        result = run_policy(
            f"{json.dumps(states)}.map(state => ["
            "policy.shouldStartWebSocketConnection(state, false),"
            "policy.shouldStartWebSocketConnection(state, true)])"
        )

        self.assertEqual(
            result,
            [
                [True, False],
                [False, False],
                [False, False],
                [True, False],
                [True, False],
            ],
        )

    def test_only_current_socket_events_are_authoritative(self):
        result = run_policy(
            "(() => { const active = {}; const stale = {}; return ["
            "policy.isCurrentSocket(active, active),"
            "policy.isCurrentSocket(active, stale)]; })()"
        )

        self.assertEqual(result, [True, False])

    def test_captcha_response_is_bound_to_request_socket(self):
        background_path = Path(__file__).parent.parent / "extension" / "background.js"
        source = background_path.read_text(encoding="utf-8")

        self.assertIn("handleGetToken(data, socket)", source)
        self.assertIn("async function handleGetToken(data, responseSocket)", source)
        self.assertIn("responseSocket.send(JSON.stringify({", source)

    def test_server_sync_respects_disabled_auto_import(self):
        background_path = Path(__file__).parent.parent / "extension" / "background.js"
        source = background_path.read_text(encoding="utf-8")

        sync_block_start = source.index('if (data.type === "sync_account")')
        sync_block_end = source.index('if (data.type === "get_token")', sync_block_start)
        sync_block = source[sync_block_start:sync_block_end]

        self.assertIn("autoImportEnabled", sync_block)
        self.assertIn("server_sync_ignored", sync_block)
        self.assertLess(sync_block.index("autoImportEnabled"), sync_block.index("importCurrentAccount"))

    def test_heartbeat_repairs_stale_disconnected_status(self):
        background_path = Path(__file__).parent.parent / "extension" / "background.js"
        source = background_path.read_text(encoding="utf-8")

        heartbeat_start = source.index("heartbeatInterval = setInterval")
        heartbeat_end = source.index("}, 20000);", heartbeat_start)
        heartbeat_block = source[heartbeat_start:heartbeat_end]

        self.assertIn('connectionStatus: "connected"', heartbeat_block)
        self.assertIn("connectionError: \"\"", heartbeat_block)


if __name__ == "__main__":
    unittest.main()
