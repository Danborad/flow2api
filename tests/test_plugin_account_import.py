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
        flow_client = SimpleNamespace(
            st_to_at=AsyncMock(return_value={
                "access_token": "at-test",
                "expires": "2099-01-01T00:00:00Z",
                "user": {"email": "tester@example.com"},
            }),
            get_credits=AsyncMock(return_value={"credits": 100}),
        )
        token_manager = SimpleNamespace(
            flow_client=flow_client,
            get_all_tokens=AsyncMock(return_value=[]),
            add_token=AsyncMock(),
        )
        routes.generation_handler = SimpleNamespace(token_manager=token_manager)

        request = routes.PluginAccountImportRequest(
            session_token="st-test",
            google_cookies="[]",
        )

        with self.assertRaises(HTTPException) as error:
            await routes.import_current_browser_account(request, api_key="test")

        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("Flow 项目页", error.exception.detail)
        token_manager.add_token.assert_not_awaited()

    async def test_new_account_uses_browser_project_id(self):
        flow_client = SimpleNamespace(
            st_to_at=AsyncMock(return_value={
                "access_token": "at-test",
                "expires": "2099-01-01T00:00:00Z",
                "user": {"email": "tester@example.com", "name": "Tester"},
            }),
            get_credits=AsyncMock(return_value={"credits": 100}),
        )
        db = SimpleNamespace(update_token=AsyncMock())
        new_token = SimpleNamespace(id=7)
        token_manager = SimpleNamespace(
            flow_client=flow_client,
            db=db,
            get_all_tokens=AsyncMock(return_value=[]),
            add_token=AsyncMock(return_value=new_token),
        )
        routes.generation_handler = SimpleNamespace(token_manager=token_manager)

        request = routes.PluginAccountImportRequest(
            session_token="st-test",
            google_cookies="[]",
            project_id="12345678-1234-1234-1234-123456789abc",
            project_name="Current Flow project",
        )

        with patch("src.services.webhook_service.get_webhook_service"):
            result = await routes.import_current_browser_account(request, api_key="test")

        self.assertTrue(result["success"])
        token_manager.add_token.assert_awaited_once()
        add_kwargs = token_manager.add_token.await_args.kwargs
        self.assertEqual(add_kwargs["project_id"], "12345678-1234-1234-1234-123456789abc")
        self.assertEqual(add_kwargs["project_name"], "Current Flow project")

    async def test_invalid_access_token_is_rejected_before_account_update(self):
        flow_client = SimpleNamespace(
            st_to_at=AsyncMock(return_value={
                "access_token": "revoked-at",
                "expires": "2099-01-01T00:00:00Z",
                "user": {"email": "tester@example.com"},
            }),
            get_credits=AsyncMock(side_effect=RuntimeError("HTTP 401 UNAUTHENTICATED")),
        )
        existing = SimpleNamespace(id=7, email="tester@example.com")
        token_manager = SimpleNamespace(
            flow_client=flow_client,
            get_all_tokens=AsyncMock(return_value=[existing]),
            update_token=AsyncMock(),
        )
        routes.generation_handler = SimpleNamespace(token_manager=token_manager)

        request = routes.PluginAccountImportRequest(
            session_token="st-with-revoked-at",
            google_cookies="[]",
        )

        with self.assertRaises(HTTPException) as error:
            await routes.import_current_browser_account(request, api_key="test")

        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("Access Token 验证失败", error.exception.detail)
        token_manager.update_token.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
