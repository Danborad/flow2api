import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.api import admin


class PluginTokenSyncStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_update_clears_previous_at_refresh_failure(self):
        existing = SimpleNamespace(id=1, is_active=True)
        original_db = admin.db
        original_token_manager = admin.token_manager
        try:
            admin.db = SimpleNamespace(
                get_plugin_config=AsyncMock(return_value=SimpleNamespace(
                    connection_token="connection-token",
                    auto_enable_on_update=True,
                )),
                get_token_by_email=AsyncMock(return_value=existing),
                update_token=AsyncMock(),
            )
            admin.token_manager = SimpleNamespace(
                flow_client=SimpleNamespace(
                    st_to_at=AsyncMock(return_value={
                        "access_token": "new-at",
                        "expires": "2099-01-01T00:00:00Z",
                        "user": {"email": "tester@example.com"},
                    }),
                    get_credits=AsyncMock(return_value={"credits": 100}),
                ),
                update_token=AsyncMock(),
            )

            result = await admin.plugin_update_token(
                {"session_token": "new-st"},
                authorization="Bearer connection-token",
            )

            self.assertTrue(result["success"])
            update_kwargs = admin.token_manager.update_token.await_args.kwargs
            self.assertEqual(update_kwargs["last_st_refresh_result"], "success")
            self.assertIsNotNone(update_kwargs["last_st_refresh_at"])
        finally:
            admin.db = original_db
            admin.token_manager = original_token_manager

    async def test_invalid_access_token_does_not_overwrite_existing_token(self):
        existing = SimpleNamespace(id=1, is_active=True)
        original_db = admin.db
        original_token_manager = admin.token_manager
        try:
            admin.db = SimpleNamespace(
                get_plugin_config=AsyncMock(return_value=SimpleNamespace(
                    connection_token="connection-token",
                    auto_enable_on_update=True,
                )),
                get_token_by_email=AsyncMock(return_value=existing),
                update_token=AsyncMock(),
            )
            admin.token_manager = SimpleNamespace(
                flow_client=SimpleNamespace(
                    st_to_at=AsyncMock(return_value={
                        "access_token": "revoked-at",
                        "expires": "2099-01-01T00:00:00Z",
                        "user": {"email": "tester@example.com"},
                    }),
                    get_credits=AsyncMock(side_effect=RuntimeError("HTTP 401 UNAUTHENTICATED")),
                ),
                update_token=AsyncMock(),
            )

            with self.assertRaises(admin.HTTPException) as error:
                await admin.plugin_update_token(
                    {"session_token": "st-with-revoked-at"},
                    authorization="Bearer connection-token",
                )

            self.assertEqual(error.exception.status_code, 400)
            self.assertIn("Access Token validation failed", error.exception.detail)
            admin.token_manager.update_token.assert_not_awaited()
        finally:
            admin.db = original_db
            admin.token_manager = original_token_manager


if __name__ == "__main__":
    unittest.main()
