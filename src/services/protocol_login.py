"""Protocol login & session verification for flow.google.com."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from curl_cffi import requests

from ..core.logger import debug_logger
from .browser_cookie_utils import validate_flow_cookie_storage
from .flow_cookies import FlowCookieJar


class ProtocolLoginer:
    """Online session verifier for flow.google.com Cookie-first authentication."""

    async def login(
        self,
        raw_cookies: str,
        proxy: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """验证 Google Cookies 并从 flow.google.com 提取真实登录态与邮箱。"""
        try:
            # 1. 结构化校验必需 Cookie 组
            validated_json = validate_flow_cookie_storage(raw_cookies)
            cookie_jar = FlowCookieJar(validated_json)

            # 2. 发起 GET https://flow.google.com/projects 验证在线登录态
            target_url = "https://flow.google.com/projects"
            headers = {
                "Cookie": cookie_jar.get_cookie_header_for_url(target_url),
                "Referer": "https://flow.google.com/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            }

            loop = asyncio.get_event_loop()
            kwargs = {
                "headers": headers,
                "timeout": 15,
                "impersonate": "chrome124",
                "allow_redirects": True,
            }
            if proxy:
                kwargs["proxies"] = {"http": proxy, "https": proxy}

            def _fetch():
                return requests.get(target_url, **kwargs)

            resp = await loop.run_in_executor(None, _fetch)

            # 3. 校验重定向目标必须留在 flow.google.com
            final_url = resp.url or target_url
            final_host = urlparse(final_url).hostname or ""
            if "accounts.google.com" in final_host or final_host != "flow.google.com":
                return {
                    "success": False,
                    "error": f"登录态已失效，页面重定向至: {final_url}",
                }

            if resp.status_code != 200:
                return {
                    "success": False,
                    "error": f"Flow 页面响应异常: HTTP {resp.status_code}",
                }

            html = resp.text

            # 4. 校验当前前端 marker
            if "boq_labs-ai-sandbox-frontend_" not in html and "AiSandboxAngularFrontend" not in html:
                return {
                    "success": False,
                    "error": "页面未包含 Flow 前端标识，可能未正确进入项目列表",
                }

            # 5. 提取已登录邮箱 (优先 oPEP7c)
            detected_email = ""
            m_email = re.search(r'"oPEP7c"\s*:\s*"([^"\\]*@[^"\\]+)"', html)
            if m_email:
                detected_email = m_email.group(1).strip().lower()
            else:
                m_general = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', html)
                if m_general:
                    detected_email = m_general.group(0).strip().lower()

            if not detected_email and email:
                detected_email = email.strip().lower()

            # 6. 处理响应 Set-Cookie 并导出最新快照
            set_cookies = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else (resp.headers.get("set-cookie") or [])
            if set_cookies:
                cookie_jar.apply_set_cookie(set_cookies, final_url)

            refreshed_snapshot = cookie_jar.export_storage()
            st_token = "frontend-" + hashlib.sha256(refreshed_snapshot.encode("utf-8")).hexdigest()[:32]

            debug_logger.log_info(f"[PROTOCOL_LOGIN] 登录态在线验证成功: email={detected_email}")

            return {
                "success": True,
                "email": detected_email,
                "session_token": st_token,
                "google_cookies": refreshed_snapshot,
            }

        except Exception as e:
            debug_logger.log_warning(f"[PROTOCOL_LOGIN] 在线验证失败: {e}")
            return {
                "success": False,
                "error": str(e),
            }


protocol_loginer = ProtocolLoginer()
