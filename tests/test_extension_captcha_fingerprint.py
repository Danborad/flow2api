import asyncio
import json
import unittest

from src.services.browser_captcha_extension import ExtensionCaptchaService


class FakeWebSocket:
    def __init__(self):
        self.query_params = {
            "route_key": "route-1",
            "client_label": "browser-1",
            "extension_version": "test",
        }
        self.sent = []

    async def accept(self):
        return None

    async def close(self, code=1000):
        return None

    async def send_text(self, value):
        self.sent.append(value)


class FakeDatabase:
    async def get_token(self, token_id):
        return type("Token", (), {"extension_route_key": "route-1"})()


class ExtensionCaptchaFingerprintTests(unittest.IsolatedAsyncioTestCase):
    async def test_bundle_returns_browser_fingerprint_from_owner_connection(self):
        service = ExtensionCaptchaService(FakeDatabase())
        websocket = FakeWebSocket()
        await service.connect(websocket)

        task = asyncio.create_task(service.get_token_bundle(
            project_id="project-1",
            action="IMAGE_GENERATION",
            timeout=2,
            token_id=1,
        ))
        await asyncio.sleep(0)
        request_id = next(iter(service.pending_requests))
        await service.handle_message(websocket, json.dumps({
            "req_id": request_id,
            "status": "success",
            "token": "captcha-token",
            "fingerprint": {
                "user_agent": "Mozilla/5.0 Chrome/150.0.0.0 Safari/537.36",
                "accept_language": "en-US,en;q=0.9",
                "sec_ch_ua": '"Chromium";v="150"',
                "sec_ch_ua_mobile": "?0",
                "sec_ch_ua_platform": '"Linux"',
                "origin": "https://flow.google.com",
                "referer": "https://flow.google.com/about"
            }
        }))

        result = await task

        self.assertEqual(result["token"], "captcha-token")
        self.assertEqual(result["fingerprint"]["sec_ch_ua_platform"], '"Linux"')
        self.assertEqual(result["fingerprint"]["origin"], "https://flow.google.com")


if __name__ == "__main__":
    unittest.main()
