"""Google Flow frontend batchexecute RPC transport."""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse

from curl_cffi import requests

from ..core.logger import debug_logger
from .flow_cookies import FlowCookieJar


class FlowFrontendTransport:
    """Manages bootstrap HTML parsing, dynamic XSRF/bl/f.sid, and batchexecute RPC execution."""

    def __init__(self):
        # 缓存 bootstrap 信息：cache_key -> (bl, f_sid, xsrf_at, expire_ts)
        self._bootstrap_cache: Dict[str, Tuple[str, str, str, float]] = {}
        self._lock = asyncio.Lock()

    async def get_bootstrap_data(
        self,
        cookie_jar: FlowCookieJar,
        project_id: Optional[str] = None,
        media_id: Optional[str] = None,
        proxy: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Tuple[str, str, str, str]:
        """获取目标页面的 (bl, f_sid, xsrf_at, source_path)。"""
        now = time.time()
        if project_id and media_id:
            source_path = f"/project/{project_id}/edit/{media_id}"
            target_url = f"https://flow.google.com{source_path}"
        elif project_id:
            source_path = f"/project/{project_id}"
            target_url = f"https://flow.google.com{source_path}"
        else:
            source_path = "/projects"
            target_url = "https://flow.google.com/projects"

        cache_key = f"{source_path}:{cookie_jar.export_storage()[:32]}"
        if not force_refresh:
            cached = self._bootstrap_cache.get(cache_key)
            if cached and cached[3] > now:
                return cached[0], cached[1], cached[2], source_path

        async with self._lock:
            if not force_refresh:
                cached = self._bootstrap_cache.get(cache_key)
                if cached and cached[3] > now:
                    return cached[0], cached[1], cached[2], source_path

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

            # 更新响应中的 Set-Cookie
            set_cookies = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else (resp.headers.get("set-cookie") or [])
            if set_cookies:
                cookie_jar.apply_set_cookie(set_cookies, target_url)

            final_url = resp.url or target_url
            if resp.status_code != 200 or "accounts.google.com" in final_url:
                raise RuntimeError(f"加载 Flow 页面失败 (HTTP {resp.status_code}, 最终地址: {final_url})")

            html = resp.text

            m_bl = re.search(r'boq_labs-ai-sandbox-frontend_[A-Za-z0-9_.-]+', html)
            m_sid = re.search(r'"FdrFJe"\s*:\s*"(-?[0-9]+)"', html)
            m_at = re.search(r'"SNlM0e"\s*:\s*"([^"\\]+)"', html)

            if not m_at:
                # 尝试从 WIZ_global_data 查找
                m_at = re.search(r'\["SNlM0e",\s*"([^"\\]+)"', html)

            if not m_at:
                raise RuntimeError("未能从 Flow 页面中提取到动态 XSRF Token (SNlM0e)，账号可能已失效")

            xsrf_val = m_at.group(1)
            sid_val = m_sid.group(1) if m_sid else ""
            bl_val = m_bl.group(0) if m_bl else "boq_labs-ai-sandbox-frontend_20261021.00_p0"

            self._bootstrap_cache[cache_key] = (bl_val, sid_val, xsrf_val, now + 600)
            return bl_val, sid_val, xsrf_val, source_path

    async def execute_rpc(
        self,
        cookie_jar: FlowCookieJar,
        rpc_id: str,
        argument: Any,
        project_id: Optional[str] = None,
        media_id: Optional[str] = None,
        proxy: Optional[str] = None,
        timeout: int = 60,
    ) -> Tuple[Any, str]:
        """执行 Flow batchexecute RPC，返回 (解包后的业务payload, 轮换后的最新Cookie快照)。"""
        bl, sid, xsrf_at, source_path = await self.get_bootstrap_data(
            cookie_jar, project_id=project_id, media_id=media_id, proxy=proxy
        )

        for attempt in range(2):
            reqid = random.randint(100000, 999999)
            url = (
                f"https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute"
                f"?rpcids={urllib.parse.quote(rpc_id)}"
                f"&source-path={urllib.parse.quote(source_path)}"
                f"&bl={urllib.parse.quote(bl)}"
                f"&f.sid={urllib.parse.quote(sid)}"
                f"&hl=zh-CN"
                f"&_reqid={reqid}"
                f"&rt=c"
            )

            headers = {
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Cookie": cookie_jar.get_cookie_header_for_url(url),
                "Origin": "https://flow.google.com",
                "Referer": "https://flow.google.com/",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "x-same-domain": "1",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            }

            f_req = json.dumps(
                [[[rpc_id, json.dumps(argument, separators=(",", ":")), None, "generic"]]],
                separators=(",", ":"),
            )

            body_data = urllib.parse.urlencode({
                "f.req": f_req,
                "at": xsrf_at,
            })

            loop = asyncio.get_event_loop()
            kwargs = {
                "data": body_data,
                "headers": headers,
                "timeout": timeout,
                "impersonate": "chrome124",
            }
            if proxy:
                kwargs["proxies"] = {"http": proxy, "https": proxy}

            def _post():
                return requests.post(url, **kwargs)

            resp = await loop.run_in_executor(None, _post)

            # 更新 Set-Cookie 轮换
            set_cookies = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else (resp.headers.get("set-cookie") or [])
            if set_cookies:
                cookie_jar.apply_set_cookie(set_cookies, url)

            if resp.status_code == 401:
                raise RuntimeError("Flow RPC 返回 HTTP 401: Google 账号 Cookie 已失效，请在浏览器中重新登录并同步")
            if resp.status_code != 200:
                raise RuntimeError(f"Flow RPC HTTP {resp.status_code}: {resp.text[:200]}")

            resp_text = resp.text
            payload, new_xsrf, has_er, er_detail = self._parse_framed_response(resp_text, rpc_id)

            if new_xsrf and attempt == 0:
                debug_logger.log_info(f"[RPC] 检测到新 XSRF Token，重试当前 RPC: {rpc_id}")
                xsrf_at = new_xsrf
                continue

            if has_er:
                if "401" in er_detail or "UNAUTHENTICATED" in er_detail:
                    raise RuntimeError("Flow RPC 返回 401 鉴权失败: Google 账号 Cookie 已失效")
                raise RuntimeError(f"Flow RPC 执行错误: {er_detail}")

            if payload is None:
                raise RuntimeError(f"Flow RPC {rpc_id} 未能从响应帧中解析出有效数据 (resp={resp_text[:300]})")

            return payload, cookie_jar.export_storage()

        raise RuntimeError(f"Flow RPC {rpc_id} 重试后依然失败")

    def _parse_framed_response(self, text: str, rpc_id: str) -> Tuple[Optional[Any], Optional[str], bool, str]:
        """解析 batchexecute framed JSON 文本。"""
        new_xsrf = None
        has_er = False
        er_detail = ""
        payload = None

        for line in text.splitlines():
            line = line.strip()
            if not line or not line.startswith("["):
                continue
            try:
                frame = json.loads(line)
            except Exception:
                continue

            if not isinstance(frame, list):
                continue

            for item in frame:
                if not isinstance(item, list) or not item:
                    continue
                tag = str(item[0])

                if tag == "xsrf" and len(item) > 1:
                    new_xsrf = str(item[1])

                elif tag == "er":
                    has_er = True
                    er_detail = json.dumps(item, ensure_ascii=False)

                elif tag == "wrb.fr" and len(item) > 2:
                    resp_rpc_id = str(item[1])
                    if resp_rpc_id == rpc_id or not payload:
                        raw_inner = item[2]
                        if isinstance(raw_inner, str):
                            try:
                                payload = json.loads(raw_inner)
                            except Exception:
                                payload = raw_inner
                        else:
                            payload = raw_inner

        return payload, new_xsrf, has_er, er_detail


flow_frontend_transport = FlowFrontendFrontendTransport = FlowFrontendTransport()
