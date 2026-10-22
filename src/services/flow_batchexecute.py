"""Google Flow Native batchexecute protocol client.

Directly communicates with https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute
using standard Google session cookies and page WIZ tokens, fully decoupled from labs.google.
"""

import asyncio
from datetime import datetime, timezone
import json
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import uuid

from curl_cffi import requests

from ..core.logger import debug_logger


class FlowBatchExecuteClient:
    """Native Google Flow Boq RPC Client."""

    def __init__(self):
        # 缓存每个 project_id 的 WIZ 数据：(at, f_sid, bl, expire_timestamp)
        self._wiz_cache: Dict[str, Tuple[str, str, str, float]] = {}
        self._lock = asyncio.Lock()

    def _parse_cookies_str(self, raw_cookies: str) -> str:
        """格式化 Google cookies 为标准 Cookie 请求头字符串。"""
        if not raw_cookies:
            return ""
        raw_cookies = raw_cookies.strip()
        if raw_cookies.startswith("["):
            try:
                items = json.loads(raw_cookies)
                parts = []
                for item in items:
                    name = item.get("name")
                    val = item.get("value")
                    if name and val:
                        parts.append(f"{name}={val}")
                return "; ".join(parts)
            except Exception:
                pass
        return raw_cookies

    async def get_wiz_data(self, project_id: str, cookie_header: str) -> Tuple[str, str, str]:
        """从项目页面中动态提取 SNlM0e (at), FdrFJe (f.sid), cfb2h (bl)。"""
        now = time.time()
        cached = self._wiz_cache.get(project_id)
        if cached and cached[3] > now:
            return cached[0], cached[1], cached[2]

        async with self._lock:
            # 双重检查
            cached = self._wiz_cache.get(project_id)
            if cached and cached[3] > now:
                return cached[0], cached[1], cached[2]

            url = f"https://flow.google.com/project/{project_id}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Cookie": cookie_header,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }

            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(url, headers=headers, timeout=12)
            )

            if resp.status_code != 200:
                raise RuntimeError(f"加载 Flow 项目页失败: HTTP {resp.status_code}")

            html = resp.text
            m_at = re.search(r'\"SNlM0e\":\"([^\"]+)\"', html)
            m_sid = re.search(r'\"FdrFJe\":\"([^\"]+)\"', html)
            m_bl = re.search(r'\"cfb2h\":\"([^\"]+)\"', html)

            if not m_at:
                raise RuntimeError("未能从 Flow 页面中解析到页面令牌 (SNlM0e)，请确认该账号处于已登录状态")

            at_val = m_at.group(1)
            sid_val = m_sid.group(1) if m_sid else ""
            bl_val = m_bl.group(1) if m_bl else "boq_labs-ai-sandbox-frontend_20261021.00_p0"

            # 缓存 15 分钟
            self._wiz_cache[project_id] = (at_val, sid_val, bl_val, now + 900)
            return at_val, sid_val, bl_val

    async def generate_image(
        self,
        raw_cookies: str,
        project_id: str,
        prompt: str,
        model_name: str,
        aspect_ratio: str,
        recaptcha_token: str,
    ) -> Dict[str, Any]:
        """使用原生 batchexecute ogiZ0b 接口生成图片。"""
        cookie_header = self._parse_cookies_str(raw_cookies)
        if not cookie_header:
            raise RuntimeError("账号缺少有效的 Google Cookies")

        at_val, sid_val, bl_val = await self.get_wiz_data(project_id, cookie_header)

        # 映射内部模型代码
        model_code = model_name
        if model_code in ("nano-banana-2.1", "BELUGA"):
            model_code = "BELUGA"
        elif model_code in ("nano-banana-2-lite", "HARBOR_SEAL"):
            model_code = "HARBOR_SEAL"
        elif model_code in ("nano-banana-pro", "GEM_PIX_2", "GEM_PIX"):
            model_code = "GEM_PIX_2"

        job_id = random.randint(1000000000, 2147483647)
        uuid1 = str(uuid.uuid4()).upper()
        uuid2 = str(uuid.uuid4()).upper()
        batch_uuid = str(uuid.uuid4()).upper()

        # 构建与 Google 线上完全一致的 ogiZ0b 请求体
        inner_payload = [
            None,
            [
                [
                    None, None, None, job_id, 3, model_code, None,
                    [
                        None, 22, None, None, None, project_id, None, None, None, None,
                        [recaptcha_token, 1]
                    ],
                    [[[prompt]]],
                    None, None, None,
                    uuid1, uuid2
                ]
            ],
            1,
            [
                None, 22, None, None, None, project_id, None, None, None, None,
                [recaptcha_token, 1]
            ],
            [batch_uuid]
        ]

        f_req = json.dumps([[["ogiZ0b", json.dumps(inner_payload, separators=(',', ':')), None, "generic"]]], separators=(',', ':'))

        reqid = random.randint(100000, 999999)
        url = (
            f"https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute"
            f"?rpcids=ogiZ0b"
            f"&source-path={urllib.parse.quote('/project/' + project_id)}"
            f"&bl={urllib.parse.quote(bl_val)}"
            f"&f.sid={urllib.parse.quote(sid_val)}"
            f"&hl=zh-CN"
            f"&_reqid={reqid}"
            f"&rt=c"
        )

        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "X-Same-Domain": "1",
            "Origin": "https://flow.google.com",
            "Referer": f"https://flow.google.com/project/{project_id}",
            "Cookie": cookie_header,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        }

        body_data = urllib.parse.urlencode({
            "f.req": f_req,
            "at": at_val
        })

        debug_logger.log_info(f"[BATCHEXECUTE] 发送图片生成 RPC ogiZ0b, project={project_id}, prompt={prompt[:30]}...")

        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: requests.post(url, data=body_data, headers=headers, timeout=60)
        )

        if resp.status_code != 200:
            raise RuntimeError(f"Google Flow 生成请求失败: HTTP {resp.status_code} ({resp.text[:200]})")

        resp_text = resp.text
        # 从响应体中提取 flow-content.google 的图片直链
        image_urls = re.findall(r"https://flow-content\.google/image/[^\s\"\'\\]+", resp_text)
        if not image_urls:
            # 尝试搜索包含 image 的其他 Google CDN
            image_urls = re.findall(r"https://[^\s\"\'\\]*flow[^\s\"\'\\]*image[^\s\"\'\\]*", resp_text)

        if not image_urls:
            # 记录完整失败报文切片便于分析
            debug_logger.log_warning(f"[BATCHEXECUTE] 未能解析到生成的图片 URL: {resp_text[:500]}")
            raise RuntimeError(f"Flow 未返回生成图片链接（上游响应异常）")

        image_url = image_urls[0]
        # 清除转义反斜杠
        image_url = image_url.replace("\\u0026", "&").replace("\\", "")

        debug_logger.log_info(f"[BATCHEXECUTE] 图片生成成功: {image_url}")

        # 封装与原 generation_handler 兼容的返回数据结构
        return {
            "imagePanels": [
                {
                    "generatedImages": [
                        {
                            "encodedImage": image_url,
                            "fifeUrl": image_url,
                            "media": {
                                "name": uuid1
                            }
                        }
                    ]
                }
            ],
            "url": image_url
        }


# 全局单例
flow_batchexecute_client = FlowBatchExecuteClient()
