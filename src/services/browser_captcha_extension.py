import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from fastapi import WebSocket

from ..core.logger import debug_logger


@dataclass
class ExtensionConnection:
    websocket: WebSocket
    route_key: str = ""
    client_label: str = ""
    connected_at: float = field(default_factory=time.time)


class ExtensionCaptchaService:
    _instance: Optional["ExtensionCaptchaService"] = None
    _lock = asyncio.Lock()

    def __init__(self, db=None):
        self.db = db
        self.active_connections: list[ExtensionConnection] = []
        self.pending_requests: dict[str, tuple[asyncio.Future, WebSocket]] = {}
        self._sync_notifications_at: dict[int, float] = {}

    @classmethod
    async def get_instance(cls, db=None) -> "ExtensionCaptchaService":
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db=db)
        elif db is not None and cls._instance.db is None:
            cls._instance.db = db
        return cls._instance

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        conn = ExtensionConnection(
            websocket=websocket,
            route_key=(websocket.query_params.get("route_key") or "").strip(),
            client_label=(websocket.query_params.get("client_label") or "").strip(),
        )
        self.active_connections.append(conn)
        debug_logger.log_info(
            f"[Extension Captcha] Client connected. Total: {len(self.active_connections)}, "
            f"route_key={conn.route_key or '-'}, label={conn.client_label or '-'}"
        )

    def disconnect(self, websocket: WebSocket):
        for conn in list(self.active_connections):
            if conn.websocket is websocket:
                self.active_connections.remove(conn)
                debug_logger.log_info(
                    f"[Extension Captcha] Client disconnected. Total: {len(self.active_connections)}, "
                    f"route_key={conn.route_key or '-'}, label={conn.client_label or '-'}"
                )
                return

    def _find_connection(self, websocket: WebSocket) -> Optional[ExtensionConnection]:
        for conn in self.active_connections:
            if conn.websocket is websocket:
                return conn
        return None

    def _select_connection(self, route_key: str) -> Optional[ExtensionConnection]:
        normalized_key = (route_key or "").strip()
        if normalized_key:
            for conn in self.active_connections:
                if conn.route_key == normalized_key:
                    return conn
            return None
        # Empty token routes are only allowed to use an empty extension route.
        # A keyed route such as "9223" belongs to a specific browser/account
        # and must never be borrowed by another token just because it is the
        # only extension online.
        for conn in self.active_connections:
            if not conn.route_key:
                return conn
        return None

    def _describe_routes(self) -> str:
        labels = []
        for conn in self.active_connections:
            label = conn.route_key or "(empty)"
            if conn.client_label:
                label = f"{label}:{conn.client_label}"
            labels.append(label)
        return ", ".join(labels)

    def describe_routes(self) -> str:
        return self._describe_routes()

    async def _send_ack(self, websocket: WebSocket, payload: Dict[str, Any]):
        try:
            await websocket.send_text(json.dumps(payload))
        except Exception:
            pass

    async def _resolve_route_key(self, token_id: Optional[int]) -> str:
        if not token_id or not self.db:
            return ""
        try:
            token = await self.db.get_token(token_id)
            if token and token.extension_route_key:
                return token.extension_route_key.strip()
        except Exception as e:
            debug_logger.log_warning(f"[Extension Captcha] Failed to resolve route key for token {token_id}: {e}")
        return ""

    def _has_connection_for_route_key(self, route_key: str) -> bool:
        return self._select_connection(route_key) is not None

    async def has_connection_for_token(self, token_id: Optional[int]) -> tuple[bool, str]:
        route_key = await self._resolve_route_key(token_id)
        return self._has_connection_for_route_key(route_key), route_key

    async def request_account_sync(self, token_id: int, reason: str = "token_error") -> bool:
        """Ask the browser bound to a token to refresh and re-import its account."""
        now = time.time()
        last_sent = self._sync_notifications_at.get(token_id, 0.0)
        if now - last_sent < 60:
            return False

        route_key = await self._resolve_route_key(token_id)
        conn = self._select_connection(route_key)
        if conn is None:
            debug_logger.log_warning(
                f"[Extension Captcha] Cannot request account sync for token_id={token_id}; "
                f"route_key='{route_key}', available={self._describe_routes() or 'none'}"
            )
            return False

        try:
            await conn.websocket.send_text(json.dumps({
                "type": "sync_account",
                "token_id": token_id,
                "reason": reason,
                "route_key": route_key,
            }))
            self._sync_notifications_at[token_id] = now
            debug_logger.log_info(
                f"[Extension Captcha] Requested immediate account sync for token_id={token_id}, "
                f"route_key={route_key or '-'}, reason={reason}"
            )
            return True
        except Exception as e:
            debug_logger.log_warning(f"[Extension Captcha] Failed to request account sync for token {token_id}: {e}")
            return False

    async def handle_message(self, websocket: WebSocket, data: str):
        try:
            payload = json.loads(data)
            message_type = payload.get("type")

            if message_type == "register":
                conn = self._find_connection(websocket)
                if conn:
                    conn.route_key = (payload.get("route_key") or conn.route_key or "").strip()
                    conn.client_label = (payload.get("client_label") or conn.client_label or "").strip()
                    debug_logger.log_info(
                        f"[Extension Captcha] Client registered route_key={conn.route_key or '-'}, "
                        f"label={conn.client_label or '-'}"
                    )
                    await self._send_ack(
                        websocket,
                        {
                            "type": "register_ack",
                            "route_key": conn.route_key,
                            "client_label": conn.client_label,
                        },
                    )
                return

            req_id = payload.get("req_id")
            if req_id and req_id in self.pending_requests:
                future, owner_websocket = self.pending_requests[req_id]
                if websocket is not owner_websocket:
                    debug_logger.log_warning(f"[Extension Captcha] Ignoring response from non-owner connection: {req_id}")
                    return
                if not future.done():
                    future.set_result(payload)
        except Exception as e:
            debug_logger.log_error(f"[Extension Captcha] Error handling message: {e}")

    async def get_token(
        self,
        project_id: str,
        action: str = "IMAGE_GENERATION",
        timeout: int = 20,
        token_id: Optional[int] = None,
    ) -> Optional[str]:
        if not self.active_connections:
            debug_logger.log_warning("[Extension Captcha] No active extension connections available.")
            raise RuntimeError("Chrome Extension not connected or Google Labs tab not open.")

        route_key = await self._resolve_route_key(token_id)
        conn = self._select_connection(route_key)
        if conn is None:
            available = self._describe_routes() or "none"
            raise RuntimeError(
                f"No Chrome Extension connection matches token_id={token_id} route_key='{route_key}'. "
                f"Available route keys: {available}"
            )

        req_id = f"req_{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = (future, conn.websocket)

        request_data = {
            "type": "get_token",
            "req_id": req_id,
            "action": action,
            "project_id": project_id,
            "route_key": route_key,
        }

        try:
            debug_logger.log_info(
                f"[Extension Captcha] Dispatching token request via route_key={route_key or '-'}, "
                f"label={conn.client_label or '-'}, project_id={project_id}, action={action}"
            )
            await conn.websocket.send_text(json.dumps(request_data))
            result = await asyncio.wait_for(future, timeout=timeout)

            if result.get("status") == "success":
                return result.get("token")

            error_msg = result.get("error")
            debug_logger.log_error(f"[Extension Captcha] Error from extension: {error_msg}")
            return None

        except asyncio.TimeoutError:
            debug_logger.log_error(f"[Extension Captcha] Timeout waiting for token (req_id: {req_id})")
            return None
        except Exception as e:
            debug_logger.log_error(f"[Extension Captcha] Communication error: {e}")
            return None
        finally:
            self.pending_requests.pop(req_id, None)

    async def report_flow_error(self, project_id: str, error_reason: str, error_message: str = ""):
        _ = project_id, error_message
        debug_logger.log_warning(f"[Extension Captcha] Flow error reported (ignoring): {error_reason}")
