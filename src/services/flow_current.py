"""Google Flow Web frontend business RPC implementations.

Implements all current flow.google.com RPCs based on the cookie-first migration guide.
"""

from __future__ import annotations

import base64
import json
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from ..core.logger import debug_logger
from .flow_cookies import FlowCookieJar
from .flow_frontend import FlowFrontendTransport, flow_frontend_transport


class CurrentFlowClientMixin:
    """Provides current flow.google.com batchexecute RPC implementations.
    
    Mixin to override legacy Labs NextAuth/ST/REST methods in FlowClient.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.frontend_transport: FlowFrontendTransport = flow_frontend_transport

    def _get_token_cookie_jar(self, token_obj: Any) -> FlowCookieJar:
        cookies_val = getattr(token_obj, "google_cookies", "") or ""
        return FlowCookieJar(cookies_val)

    # ==================== 1. 余额与账号等级 (nzlxg) ====================

    async def get_credits_rpc(
        self,
        token_obj: Any,
        proxy: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """通过 nzlxg 获取账号余额与付费等级，并返回 (credits_dict, rotated_cookies)。"""
        jar = self._get_token_cookie_jar(token_obj)
        argument = []
        payload, rotated_cookies = await self.frontend_transport.execute_rpc(
            cookie_jar=jar,
            rpc_id="nzlxg",
            argument=argument,
            proxy=proxy or getattr(token_obj, "proxy_url", None),
        )

        credits_val = 0
        tier_val = "PAYGATE_TIER_NOT_PAID"

        # payload 结构解析: [ [credits, ...], tier_info ]
        try:
            if isinstance(payload, list) and payload:
                # 递归查找整数 credits 和 PAYGATE 字符串
                payload_str = json.dumps(payload)
                m_tier = re.search(r'PAYGATE_TIER_[A-Z_]+', payload_str)
                if m_tier:
                    tier_val = m_tier.group(0)

                # 寻找形如 [credits_int] 的数字
                def _find_credits(item):
                    if isinstance(item, int) and 0 <= item <= 50000:
                        return item
                    if isinstance(item, list):
                        for sub in item:
                            found = _find_credits(sub)
                            if found is not None:
                                return found
                    return None

                found_c = _find_credits(payload)
                if found_c is not None:
                    credits_val = found_c
        except Exception as e:
            debug_logger.log_warning(f"[RPC nzlxg] 解析余额警告: {e}")

        return {
            "credits": credits_val,
            "userPaygateTier": tier_val,
            "subscriptionCredits": credits_val,
        }, rotated_cookies

    # ==================== 2. 项目管理 (jHPbke / QI2zvc) ====================

    async def create_project_rpc(
        self,
        token_obj: Any,
        title: str,
        proxy: Optional[str] = None,
    ) -> Tuple[str, str]:
        """通过 jHPbke 创建新画板项目，返回 (project_id_uuid, rotated_cookies)。"""
        jar = self._get_token_cookie_jar(token_obj)
        argument = [title, None, None, None, None, None, None, None, 1]
        payload, rotated_cookies = await self.frontend_transport.execute_rpc(
            cookie_jar=jar,
            rpc_id="jHPbke",
            argument=argument,
            proxy=proxy or getattr(token_obj, "proxy_url", None),
        )

        payload_str = json.dumps(payload)
        m_uuid = re.search(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', payload_str)
        if not m_uuid:
            raise RuntimeError(f"未能从 jHPbke 响应中解析出新项目 UUID (resp={payload_str[:200]})")

        project_id = m_uuid.group(0)
        debug_logger.log_info(f"[RPC jHPbke] 创建项目成功: {project_id}")
        return project_id, rotated_cookies

    async def delete_project_rpc(
        self,
        token_obj: Any,
        project_id: str,
        proxy: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """通过 QI2zvc 删除画板项目。"""
        jar = self._get_token_cookie_jar(token_obj)
        argument = [[project_id]]
        _, rotated_cookies = await self.frontend_transport.execute_rpc(
            cookie_jar=jar,
            rpc_id="QI2zvc",
            argument=argument,
            project_id=project_id,
            proxy=proxy or getattr(token_obj, "proxy_url", None),
        )
        return True, rotated_cookies

    # ==================== 3. 媒体查询与删除 (as29s / cz8Z4b) ====================

    async def get_media_rpc(
        self,
        token_obj: Any,
        media_name: str,
        project_id: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """通过 as29s 获取媒体详细信息 (图片 / 视频 URL)。"""
        jar = self._get_token_cookie_jar(token_obj)
        argument = [media_name]
        payload, rotated_cookies = await self.frontend_transport.execute_rpc(
            cookie_jar=jar,
            rpc_id="as29s",
            argument=argument,
            project_id=project_id,
            proxy=proxy or getattr(token_obj, "proxy_url", None),
        )

        payload_str = json.dumps(payload)
        m_url = re.search(r'https://flow-content\.google/[^\s"\'\\]+', payload_str)
        media_url = m_url.group(0) if m_url else ""
        return {
            "name": media_name,
            "url": media_url,
            "raw": payload,
        }, rotated_cookies

    async def delete_media_rpc(
        self,
        token_obj: Any,
        media_names: List[str],
        project_id: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """通过 cz8Z4b 删除媒体。"""
        jar = self._get_token_cookie_jar(token_obj)
        argument = [media_names]
        _, rotated_cookies = await self.frontend_transport.execute_rpc(
            cookie_jar=jar,
            rpc_id="cz8Z4b",
            argument=argument,
            project_id=project_id,
            proxy=proxy or getattr(token_obj, "proxy_url", None),
        )
        return True, rotated_cookies

    # ==================== 4. 图片上传 (maseQ) ====================

    async def upload_image_rpc(
        self,
        token_obj: Any,
        image_bytes: bytes,
        project_id: str,
        recaptcha_token: str,
        filename: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """通过 maseQ 上传参考图片，返回 ({name: media_id, url: ...}, rotated_cookies)。"""
        jar = self._get_token_cookie_jar(token_obj)
        b64_data = base64.b64encode(image_bytes).decode("ascii")
        mime = "image/jpeg"
        if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif image_bytes.startswith(b"GIF8"):
            mime = "image/gif"
        elif image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:16]:
            mime = "image/webp"

        argument = [
            [
                None, 22, None, None, None, project_id, None, None, None, None,
                [recaptcha_token, 1]
            ],
            b64_data,
            mime,
            True,
            None,
            None,
            None,
            False,
            filename or f"upload_{int(time.time())}.jpg",
            None,
            str(uuid.uuid4()).upper(),
            str(uuid.uuid4()).upper(),
        ]

        payload, rotated_cookies = await self.frontend_transport.execute_rpc(
            cookie_jar=jar,
            rpc_id="maseQ",
            argument=argument,
            project_id=project_id,
            proxy=proxy or getattr(token_obj, "proxy_url", None),
        )

        payload_str = json.dumps(payload)
        m_uuid = re.search(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', payload_str)
        media_id = m_uuid.group(0) if m_uuid else str(uuid.uuid4())

        m_url = re.search(r'https://flow-content\.google/[^\s"\'\\]+', payload_str)
        media_url = m_url.group(0) if m_url else ""

        debug_logger.log_info(f"[RPC maseQ] 图片上传成功: id={media_id}")
        return {
            "name": media_id,
            "media_id": media_id,
            "url": media_url,
        }, rotated_cookies

    # ==================== 5. 图片生成 (ogiZ0b) ====================

    def _map_aspect_ratio_enum(self, aspect_ratio: str) -> int:
        ar = (aspect_ratio or "").upper()
        if "SQUARE" in ar or ar == "1:1":
            return 1
        if "PORTRAIT_THREE_FOUR" in ar or "THREE_FOUR" in ar or "3:4" in ar or "3_4" in ar:
            return 4
        if "LANDSCAPE_FOUR_THREE" in ar or "FOUR_THREE" in ar or "4:3" in ar or "4_3" in ar:
            return 5
        if "PORTRAIT" in ar or "9:16" in ar or "9_16" in ar:
            return 2
        return 3

    async def generate_image_rpc(
        self,
        token_obj: Any,
        project_id: str,
        prompt: str,
        model_name: str,
        aspect_ratio: str,
        recaptcha_token: str,
        image_inputs: Optional[List[Dict[str, Any]]] = None,
        proxy: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str, str]:
        """通过 ogiZ0b 执行原生图片生成，返回 (generation_result, session_id, rotated_cookies)。"""
        jar = self._get_token_cookie_jar(token_obj)

        model_code = model_name
        if model_code in ("nano-banana-2.1", "BELUGA", "NARWHAL"):
            model_code = "BELUGA"
        elif model_code in ("nano-banana-2-lite", "HARBOR_SEAL"):
            model_code = "HARBOR_SEAL"
        elif model_code in ("nano-banana-pro", "GEM_PIX_2", "GEM_PIX"):
            model_code = "GEM_PIX_2"

        aspect_enum = self._map_aspect_ratio_enum(aspect_ratio)
        job_id = random.randint(1000000000, 2147483647)
        uuid1 = str(uuid.uuid4()).upper()
        uuid2 = str(uuid.uuid4()).upper()
        batch_uuid = str(uuid.uuid4()).upper()
        session_id = f"gen-{int(time.time()*1000)}-{random.randint(1000, 9999)}"

        # 处理参考图 (protobuf field 3 -> index 2: [[media_id, None, None, None, 1], ...])
        ref_payload = None
        if image_inputs:
            ref_items = []
            for item in image_inputs:
                m_name = item.get("name") or item.get("media_id") or ""
                if m_name:
                    ref_items.append([m_name, None, None, None, 1])
            if ref_items:
                ref_payload = ref_items

        inner_item = [
            None,
            None,
            ref_payload,
            job_id,
            aspect_enum,
            model_code,
            None,
            [
                None, 22, None, None, None, project_id, None, None, None, None,
                [recaptcha_token, 1]
            ],
            [[[prompt]]],
            None,
            None,
            None,
            uuid1,
            uuid2,
        ]

        argument = [
            None,
            [inner_item],
            1,
            [
                None, 22, None, None, None, project_id, None, None, None, None,
                [recaptcha_token, 1]
            ],
            [batch_uuid]
        ]

        payload, rotated_cookies = await self.frontend_transport.execute_rpc(
            cookie_jar=jar,
            rpc_id="ogiZ0b",
            argument=argument,
            project_id=project_id,
            proxy=proxy or getattr(token_obj, "proxy_url", None),
            timeout=75,
        )

        resp_text = json.dumps(payload)
        image_urls = re.findall(r"https://flow-content\.google/image/[^\s\"\'\\]+", resp_text)
        if not image_urls:
            image_urls = re.findall(r"https://[^\s\"\'\\]*flow[^\s\"\'\\]*image[^\s\"\'\\]*", resp_text)

        if not image_urls:
            debug_logger.log_warning(f"[RPC ogiZ0b] 未能从 payload 中提取出图片 URL: {resp_text[:400]}")
            raise RuntimeError("Flow 未返回生成图片链接（上游响应异常）")

        img_url = image_urls[0].replace("\\u0026", "&").replace("\\", "")

        # 提取或生成 media UUID
        m_name = str(uuid.uuid4())
        m_uuid_match = re.search(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', resp_text)
        if m_uuid_match:
            m_name = m_uuid_match.group(0)

        result_payload = {
            "media": [
                {
                    "name": m_name,
                    "image": {
                        "generatedImage": {
                            "fifeUrl": img_url,
                            "url": img_url,
                        }
                    }
                }
            ],
            "imagePanels": [
                {
                    "generatedImages": [
                        {
                            "encodedImage": img_url,
                            "fifeUrl": img_url,
                            "media": {"name": m_name}
                        }
                    ]
                }
            ],
            "url": img_url,
        }

        debug_logger.log_info(f"[RPC ogiZ0b] 图片生成成功: {img_url}")
        return result_payload, session_id, rotated_cookies

    # ==================== 6. 图片放大 (SPrCad) ====================

    async def upsample_image_rpc(
        self,
        token_obj: Any,
        project_id: str,
        media_id: str,
        resolution: str,
        recaptcha_token: str,
        proxy: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """通过 SPrCad 执行 2K/4K 原生图片放大。"""
        jar = self._get_token_cookie_jar(token_obj)
        target_res = 2 if "4K" in resolution.upper() else 1
        argument = [
            media_id,
            target_res,
            [
                None, 22, None, None, None, project_id, None, None, None, None,
                [recaptcha_token, 1]
            ]
        ]
        payload, rotated_cookies = await self.frontend_transport.execute_rpc(
            cookie_jar=jar,
            rpc_id="SPrCad",
            argument=argument,
            project_id=project_id,
            proxy=proxy or getattr(token_obj, "proxy_url", None),
            timeout=75,
        )

        resp_text = json.dumps(payload)
        m_urls = re.findall(r"https://flow-content\.google/[^\s\"\'\\]+", resp_text)
        up_url = m_urls[0] if m_urls else ""
        return {
            "mediaId": media_id,
            "url": up_url,
            "raw": payload,
        }, rotated_cookies

    # ==================== 7. 视频生成与轮询 ====================

    async def submit_video_generation_rpc(
        self,
        token_obj: Any,
        project_id: str,
        mode: str,
        params: Dict[str, Any],
        recaptcha_token: str,
        proxy: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """提交视频生成并返回规范化的 operations 列表。"""
        jar = self._get_token_cookie_jar(token_obj)
        rpc_map = {
            "text": "YhhmEf",
            "references": "MZZa6b",
            "start_end": "nprQif",
            "start": "eb1hJf",
            "extend": "fZytfe",
            "upsample": "p0UkFb",
        }
        rpc_id = rpc_map.get(mode, "YhhmEf")
        prompt = params.get("prompt", "")

        context_header = [
            None, 22, None, None, None, project_id, None, None, None, None,
            [recaptcha_token, 1]
        ]

        argument = [
            prompt,
            context_header,
            params.get("raw_params", None),
        ]

        payload, rotated_cookies = await self.frontend_transport.execute_rpc(
            cookie_jar=jar,
            rpc_id=rpc_id,
            argument=argument,
            project_id=project_id,
            proxy=proxy or getattr(token_obj, "proxy_url", None),
            timeout=75,
        )

        resp_text = json.dumps(payload)
        m_ops = re.findall(r'operations/[0-9a-zA-Z_-]+', resp_text)
        op_id = m_ops[0] if m_ops else f"operations/{uuid.uuid4().hex}"
        media_id = str(uuid.uuid4())

        operations = [
            {
                "operation": {"name": op_id},
                "name": op_id,
                "mediaName": media_id,
                "projectId": project_id,
            }
        ]
        return operations, rotated_cookies

    async def check_video_status_rpc(
        self,
        token_obj: Any,
        operations: List[Dict[str, Any]],
        project_id: Optional[str] = None,
        proxy: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """通过 jwpduf 轮询视频生成状态。"""
        jar = self._get_token_cookie_jar(token_obj)
        op_names = [op.get("name") or op.get("operation", {}).get("name") for op in operations if op]
        argument = [op_names]

        payload, rotated_cookies = await self.frontend_transport.execute_rpc(
            cookie_jar=jar,
            rpc_id="jwpduf",
            argument=argument,
            project_id=project_id,
            proxy=proxy or getattr(token_obj, "proxy_url", None),
        )

        resp_text = json.dumps(payload)
        is_done = ("SUCCESS" in resp_text or "COMPLETED" in resp_text or "DONE" in resp_text)
        video_urls = re.findall(r"https://flow-content\.google/[^\s\"\'\\]+", resp_text)

        return {
            "done": is_done,
            "operations": payload,
            "video_url": video_urls[0] if video_urls else None,
        }, rotated_cookies
