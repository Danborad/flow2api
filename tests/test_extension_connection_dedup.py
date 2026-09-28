import asyncio
import unittest

from src.services.browser_captcha_extension import ExtensionCaptchaService


class FakeWebSocket:
    def __init__(self, route_key: str, client_label: str):
        self.query_params = {
            "route_key": route_key,
            "client_label": client_label,
            "extension_version": "test",
        }
        self.accepted = False
        self.closed_code = None

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000):
        self.closed_code = code


class ExtensionConnectionDedupTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_connection_replaces_existing_same_route(self):
        service = ExtensionCaptchaService()
        first = FakeWebSocket("same-route", "first")
        second = FakeWebSocket("same-route", "second")

        await service.connect(first)
        await service.connect(second)

        self.assertEqual(first.closed_code, 1000)
        self.assertIsNone(second.closed_code)
        self.assertEqual(len(service.active_connections), 1)
        self.assertIs(service.active_connections[0].websocket, second)

    async def test_replacing_route_fails_requests_owned_by_old_connection(self):
        service = ExtensionCaptchaService()
        first = FakeWebSocket("same-route", "first")
        second = FakeWebSocket("same-route", "second")
        await service.connect(first)

        future = asyncio.get_running_loop().create_future()
        service.pending_requests["request-1"] = (future, first)

        await service.connect(second)

        with self.assertRaisesRegex(RuntimeError, "reconnected"):
            await future
        self.assertNotIn("request-1", service.pending_requests)


if __name__ == "__main__":
    unittest.main()
