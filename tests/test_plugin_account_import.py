import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from src.api import routes


class PluginAccountImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_handler = routes.generation_handler

    async def asyncTearDown(self):
        routes.generation_handler = self.original_handler

    async def test_new_account_requires_browser_project_id(self):
        token_manager = SimpleNamespace(
            get_all_tokens=AsyncMock(return_value=[]),
            add_token=AsyncMock(),
        )
        routes.generation_handler = SimpleNamespace(token_manager=token_manager)

        valid_cookies = json.dumps([
            {"name": "OSID", "value": "osid-val", "domain": "flow.google.com", "path": "/", "secure": True},
            {"name": "SID", "value": "sid-val", "domain": ".google.com", "path": "/", "secure": True},
        ])

        request = routes.PluginAccountImportRequest(
            google_cookies=valid_cookies,
            email="tester@example.com",
            project_id=None,
        )

        with patch("src.services.protocol_login.protocol_loginer.login", AsyncMock(return_value={
            "success": True,
            "email": "tester@example.com",
            "google_cookies": valid_cookies,
        })):
            res = await routes.import_current_browser_account(request, api_key="test")
            self.assertFalse(res["success"])
            self.assertIn("Flow 项目 ID", res["message"])
        token_manager.add_token.assert_not_awaited()

    async def test_new_account_uses_browser_project_id(self):
        db = SimpleNamespace(
            update_token=AsyncMock(),
            update_token_if_cookie_matches=AsyncMock(return_value=True),
        )
        new_token = SimpleNamespace(id=7)
        flow_client = SimpleNamespace(
            get_credits_rpc=AsyncMock(return_value=({"credits": 100, "userPaygateTier": "PAYGATE_TIER_ONE"}, "[]"))
        )
        token_manager = SimpleNamespace(
            db=db,
            flow_client=flow_client,
            get_all_tokens=AsyncMock(return_value=[]),
            get_token=AsyncMock(return_value=new_token),
            add_token=AsyncMock(return_value=new_token),
        )
        routes.generation_handler = SimpleNamespace(token_manager=token_manager)

        valid_cookies = json.dumps([
            {"name": "OSID", "value": "osid-val", "domain": "flow.google.com", "path": "/", "secure": True},
            {"name": "SID", "value": "sid-val", "domain": ".google.com", "path": "/", "secure": True},
        ])

        request = routes.PluginAccountImportRequest(
            google_cookies=valid_cookies,
            email="tester@example.com",
            project_id="12345678-1234-1234-1234-123456789abc",
            project_name="Current Flow project",
        )

        with patch("src.services.protocol_login.protocol_loginer.login", AsyncMock(return_value={
            "success": True,
            "email": "tester@example.com",
            "google_cookies": valid_cookies,
        })):
            result = await routes.import_current_browser_account(request, api_key="test")

        self.assertTrue(result["success"])
        token_manager.add_token.assert_awaited_once()
        add_kwargs = token_manager.add_token.await_args.kwargs
        self.assertEqual(add_kwargs["project_id"], "12345678-1234-1234-1234-123456789abc")
        self.assertEqual(add_kwargs["project_name"], "Current Flow project")

    async def test_invalid_cookies_rejected(self):
        token_manager = SimpleNamespace(
            get_all_tokens=AsyncMock(return_value=[]),
            update_token=AsyncMock(),
        )
        routes.generation_handler = SimpleNamespace(token_manager=token_manager)

        request = routes.PluginAccountImportRequest(
            google_cookies="[]",
        )

        with patch("src.services.protocol_login.protocol_loginer.login", AsyncMock(return_value={
            "success": False,
            "error": "缺少 Flow 会话 Cookie",
        })):
            res = await routes.import_current_browser_account(request, api_key="test")
            self.assertFalse(res["success"])
            self.assertIn("缺少 Flow 会话 Cookie", res["message"])


if __name__ == "__main__":
    unittest.main()
