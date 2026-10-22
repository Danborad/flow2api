"""API routes for OpenAI-compatible and Gemini generateContent endpoints."""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, List, Optional
import base64
import io
import json
import mimetypes
from pathlib import Path
import re
import zipfile
from urllib.parse import urlparse
from types import SimpleNamespace

from curl_cffi.requests import AsyncSession
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..core.auth import AuthManager, verify_api_key_flexible
from ..core.logger import debug_logger
from ..core.model_resolver import get_base_model_aliases, get_friendly_model_aliases, resolve_model_name
from ..core.models import (
    ChatCompletionRequest,
    ChatMessage,
    GeminiContent,
    GeminiGenerateContentRequest,
)
from ..services.generation_handler import MODEL_CONFIG, GenerationHandler
from ..services.browser_captcha_extension import ExtensionCaptchaService

router = APIRouter()

MARKDOWN_IMAGE_RE = re.compile(r"!\[.*?\]\((.*?)\)")
HTML_VIDEO_RE = re.compile(r"<video[^>]+src=['\"](.*?)['\"]", re.IGNORECASE)
DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$", re.DOTALL)
MEDIA_PROMPT_TOOL_BLOCK_RE = re.compile(r"<tools>.*?</tools>", re.IGNORECASE | re.DOTALL)
MEDIA_SYSTEM_INSTRUCTION_MARKERS = (
    "<tools>",
    "</tools>",
    "function calling ai model",
    "function signatures",
    "\"$schema\"",
    "\"additionalproperties\"",
)
MEDIA_PROMPT_PREAMBLE_PATTERNS = (
    re.compile(r"^you are a function calling ai model\.?$", re.IGNORECASE),
    re.compile(
        r"^you are provided with function signatures within .* xml tags\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^you may call one or more functions to assist with the user query\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^don't make assumptions about what values to plug into functions\.?$",
        re.IGNORECASE,
    ),
    re.compile(r"^here are the available tools:.*$", re.IGNORECASE),
)
GEMINI_STATUS_MAP = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    409: "ABORTED",
    429: "RESOURCE_EXHAUSTED",
    500: "INTERNAL",
    502: "UNAVAILABLE",
    503: "UNAVAILABLE",
    504: "DEADLINE_EXCEEDED",
}

# Dependency injection will be set up in main.py
generation_handler: GenerationHandler = None


class PluginAccountImportRequest(BaseModel):
    """Current browser account credentials imported by the local extension or Host Agent."""

    session_token: Optional[str] = ""
    email: Optional[str] = None
    google_cookies: str = ""
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    extension_route_key: Optional[str] = None
    refresh_interval_minutes: int = 120
    proxy_url: Optional[str] = None
    token_id: Optional[int] = None


@dataclass
class NormalizedGenerationRequest:
    """Internal request shape shared by OpenAI and Gemini entrypoints."""

    model: str
    prompt: str
    images: List[bytes]
    messages: Optional[List[ChatMessage]] = None
    video_media_id: Optional[str] = None
    target_token_id: Optional[int] = None


def set_generation_handler(handler: GenerationHandler):
    """Set generation handler instance."""
    global generation_handler
    generation_handler = handler


def _ensure_generation_handler() -> GenerationHandler:
    if generation_handler is None:
        raise HTTPException(status_code=500, detail="Generation handler not initialized")
    return generation_handler


def _build_model_description(model_config: Dict[str, Any]) -> str:
    """Build a human-readable description for model listing endpoints."""
    description = f"{model_config['type'].capitalize()} generation"
    if model_config["type"] == "image":
        description += f" - {model_config['model_name']}"
    else:
        description += f" - {model_config['model_key']}"
    return description


def _get_openai_model_catalog() -> List[Dict[str, str]]:
    """Collect OpenAI-compatible model list entries."""
    return [
        {
            "id": model_id,
            "description": description,
        }
        for model_id, description in get_friendly_model_aliases().items()
    ]


def _get_internal_openai_model_catalog() -> List[Dict[str, str]]:
    """Collect internal model list entries for debugging/backward compatibility."""
    return [
        {
            "id": model_id,
            "description": _build_model_description(model_config),
        }
        for model_id, model_config in MODEL_CONFIG.items()
    ]


def _get_gemini_model_catalog() -> Dict[str, str]:
    """Collect Gemini-compatible model metadata for /models endpoints."""
    return dict(get_friendly_model_aliases())


def _get_internal_gemini_model_catalog() -> Dict[str, str]:
    """Collect full Gemini-compatible model metadata including internal long IDs."""
    catalog: Dict[str, str] = dict(get_base_model_aliases())

    for model_id, model_config in MODEL_CONFIG.items():
        catalog.setdefault(model_id, _build_model_description(model_config))

    return catalog


def _build_gemini_model_resource(model_id: str, description: str) -> Dict[str, Any]:
    """Build a Gemini-compatible model resource payload."""
    resolved = resolve_model_name(model_id, model_config=MODEL_CONFIG)
    video_config = MODEL_CONFIG.get(resolved, {})
    long_running = model_id in {"Omni 1.1 Flash", "Veo 3.1 - Fast", "Veo 3.1 - Lite", "Veo 3.1 - Quality"} or (
        video_config.get("type") == "video" and video_config.get("video_type") in {"t2v", "omni"}
        and not video_config.get("upsample")
    )
    return {
        "name": f"models/{model_id}",
        "displayName": model_id,
        "description": description,
        "version": "flow2api",
        "inputTokenLimit": 0,
        "outputTokenLimit": 0,
        "supportedGenerationMethods": ["generateContent", "streamGenerateContent"] + (["predictLongRunning"] if long_running else []),
    }


def _decode_data_url(data_url: str) -> tuple[str, bytes]:
    match = DATA_URL_RE.match(data_url)
    if not match:
        raise HTTPException(status_code=400, detail="Invalid data URL")
    return match.group("mime"), base64.b64decode(match.group("data"))


def _detect_image_mime_type(image_bytes: bytes, fallback: str = "image/png") -> str:
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "image/gif"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return fallback


def _guess_mime_type(uri: str, fallback: str) -> str:
    guessed, _ = mimetypes.guess_type(urlparse(uri).path)
    return guessed or fallback


async def retrieve_image_data(url: str) -> Optional[bytes]:
    """Read image bytes from local /tmp cache or remote URL."""
    file_cache = getattr(generation_handler, "file_cache", None)
    try:
        if "/tmp/" in url and file_cache:
            path = urlparse(url).path
            filename = path.split("/tmp/")[-1]
            local_file_path = file_cache.cache_dir / filename

            if local_file_path.exists() and local_file_path.is_file():
                data = local_file_path.read_bytes()
                if data:
                    return data
    except Exception as exc:
        debug_logger.log_warning(f"[CONTEXT] 本地缓存读取失败: {str(exc)}")

    proxy_url = None
    try:
        if file_cache and hasattr(file_cache, "_resolve_download_proxy"):
            proxy_url = await file_cache._resolve_download_proxy("image")
    except Exception as exc:
        debug_logger.log_warning(f"[CONTEXT] 图片下载代理解析失败: {str(exc)}")

    try:
        async with AsyncSession() as session:
            response = await session.get(
                url,
                timeout=60,
                proxies={"http": proxy_url, "https": proxy_url} if proxy_url else None,
                headers={
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Referer": "https://labs.google/",
                },
                impersonate="chrome120",
                verify=False,
            )
            if response.status_code == 200 and response.content:
                return response.content
            debug_logger.log_warning(
                f"[CONTEXT] 图片下载失败，状态码: {response.status_code}"
            )
    except Exception as exc:
        debug_logger.log_error(f"[CONTEXT] 图片下载异常: {str(exc)}")

    return None


async def _load_image_bytes_from_uri(uri: str) -> bytes:
    if not uri:
        raise HTTPException(status_code=400, detail="Image URI cannot be empty")

    if uri.startswith("data:image"):
        _, image_bytes = _decode_data_url(uri)
        return image_bytes

    if uri.startswith("http://") or uri.startswith("https://") or "/tmp/" in uri:
        image_bytes = await retrieve_image_data(uri)
        if image_bytes:
            return image_bytes
        raise HTTPException(status_code=400, detail=f"Failed to load image from {uri}")

    raise HTTPException(status_code=400, detail=f"Unsupported image URI: {uri}")


def _coerce_gemini_contents(raw_contents: Optional[List[Any]]) -> List[GeminiContent]:
    contents: List[GeminiContent] = []
    for item in raw_contents or []:
        if isinstance(item, GeminiContent):
            contents.append(item)
        else:
            contents.append(GeminiContent.model_validate(item))
    return contents


def _extract_text_from_gemini_content(content: Optional[GeminiContent]) -> str:
    if content is None:
        return ""
    text_parts = [part.text.strip() for part in content.parts if part.text]
    return "\n".join(part for part in text_parts if part).strip()


def _should_ignore_media_system_instruction(system_instruction: str) -> bool:
    """Drop agent/tool scaffolding before sending media prompts upstream."""
    if not system_instruction:
        return False

    normalized = system_instruction.lower()
    if len(system_instruction) > 1200:
        return True

    return any(marker in normalized for marker in MEDIA_SYSTEM_INSTRUCTION_MARKERS)


def _sanitize_media_prompt(prompt: str) -> str:
    """Strip agent/tool scaffolding that image/video models cannot use."""
    if not prompt:
        return ""

    sanitized = MEDIA_PROMPT_TOOL_BLOCK_RE.sub(" ", prompt.strip())
    cleaned_lines: List[str] = []
    for raw_line in sanitized.splitlines():
        line = raw_line.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if any(pattern.fullmatch(line) for pattern in MEDIA_PROMPT_PREAMBLE_PATTERNS):
            continue
        cleaned_lines.append(line)

    sanitized = "\n".join(cleaned_lines).strip()
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip()


async def _extract_prompt_and_images_from_openai_messages(
    messages: List[ChatMessage],
) -> tuple[str, List[bytes], Optional[str]]:
    """Extract prompt, images, and optional video_media_id from messages.

    Returns:
        (prompt, images, video_media_id)
        video_media_id is set when an image_url starts with "extend://"
    """
    last_message = messages[-1]
    content = last_message.content
    prompt_parts: List[str] = []
    images: List[bytes] = []
    video_media_id: Optional[str] = None

    if isinstance(content, str):
        prompt_parts.append(content)
    elif isinstance(content, list):
        for item in content:
            item_type = item.get("type")
            if item_type == "text":
                text = item.get("text", "").strip()
                if text:
                    prompt_parts.append(text)
            elif item_type == "image_url":
                image_url = item.get("image_url", {}).get("url", "")
                # extend://MEDIA_ID 用于视频续写
                if image_url.startswith("extend://"):
                    video_media_id = image_url[len("extend://"):]
                else:
                    images.append(await _load_image_bytes_from_uri(image_url))

    prompt = "\n".join(part for part in prompt_parts if part).strip()
    return prompt, images, video_media_id


async def _append_openai_reference_images(
    model: str,
    messages: List[ChatMessage],
    images: List[bytes],
) -> List[bytes]:
    model_config = MODEL_CONFIG.get(model)
    if not model_config or model_config["type"] != "image" or len(messages) <= 1:
        return images

    debug_logger.log_info(f"[CONTEXT] 开始查找历史参考图，消息数量: {len(messages)}")

    for msg in reversed(messages[:-1]):
        if msg.role == "assistant" and isinstance(msg.content, str):
            matches = MARKDOWN_IMAGE_RE.findall(msg.content)
            if not matches:
                continue

            for image_url in reversed(matches):
                if not image_url.startswith("http") and "/tmp/" not in image_url:
                    continue
                try:
                    downloaded_bytes = await retrieve_image_data(image_url)
                    if downloaded_bytes:
                        images.insert(0, downloaded_bytes)
                        debug_logger.log_info(
                            f"[CONTEXT] ✅ 添加历史参考图: {image_url}"
                        )
                        return images
                    debug_logger.log_warning(
                        f"[CONTEXT] 图片下载失败或为空，尝试下一个: {image_url}"
                    )
                except Exception as exc:
                    debug_logger.log_error(
                        f"[CONTEXT] 处理参考图时出错: {str(exc)}"
                    )
    return images


async def _extract_prompt_and_images_from_gemini_contents(
    contents: List[GeminiContent],
) -> tuple[str, List[bytes], Optional[str]]:
    if not contents:
        raise HTTPException(status_code=400, detail="contents cannot be empty")

    target_content = next(
        (content for content in reversed(contents) if (content.role or "user") == "user"),
        contents[-1],
    )

    prompt_parts: List[str] = []
    images: List[bytes] = []
    video_media_id: Optional[str] = None

    for part in target_content.parts:
        if part.text:
            text = part.text.strip()
            if text:
                prompt_parts.append(text)
        elif part.inlineData is not None:
            mime_type = part.inlineData.mimeType.lower()
            if not mime_type.startswith("image/"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported inlineData mime type: {part.inlineData.mimeType}",
                )
            images.append(base64.b64decode(part.inlineData.data))
        elif part.fileData is not None:
            mime_type = (part.fileData.mimeType or "").lower()
            if mime_type == "video/mp4" and part.fileData.fileUri.startswith("extend://"):
                video_media_id = part.fileData.fileUri[len("extend://"):].strip()
                if not video_media_id:
                    raise HTTPException(status_code=400, detail="Video media ID cannot be empty")
                continue
            if mime_type and not mime_type.startswith("image/"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported fileData mime type: {part.fileData.mimeType}",
                )
            images.append(await _load_image_bytes_from_uri(part.fileData.fileUri))

    prompt = "\n".join(part for part in prompt_parts if part).strip()
    return prompt, images, video_media_id


def _resolve_request_model(
    model: str,
    request: Any,
    images: Optional[List[bytes]] = None,
    raw_request: Optional[Request] = None,
) -> str:
    resolved_model = resolve_model_name(
        model=model,
        request=request,
        model_config=MODEL_CONFIG,
        images=images,
        raw_request=raw_request,
    )
    if resolved_model != model:
        debug_logger.log_info(f"[ROUTE] 模型名已转换: {model} → {resolved_model}")
    return resolved_model


def _get_request_base_url(request: Request) -> Optional[str]:
    """根据实际请求头推导对外可访问的基础地址。"""
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host = (forwarded_host or request.headers.get("host") or "").strip()

    if not host:
        return None

    proto = forwarded_proto or request.url.scheme or "http"
    return f"{proto}://{host}"


async def _normalize_openai_request(
    request: ChatCompletionRequest,
    raw_request: Optional[Request] = None,
) -> NormalizedGenerationRequest:
    target_token_id = None
    if raw_request:
        raw_tid = raw_request.headers.get("x-target-token-id") or raw_request.query_params.get("token_id")
        if raw_tid:
            try:
                target_token_id = int(raw_tid)
            except Exception:
                pass

    if request.messages:
        prompt, images, video_media_id = await _extract_prompt_and_images_from_openai_messages(
            request.messages
        )
        if request.image and not images:
            images.append(await _load_image_bytes_from_uri(request.image))
        model = _resolve_request_model(request.model, request, images=images, raw_request=raw_request)
        initial_image_count = len(images)
        images = await _append_openai_reference_images(model, request.messages, images)
        if len(images) != initial_image_count:
            model = _resolve_request_model(request.model, request, images=images, raw_request=raw_request)
        return NormalizedGenerationRequest(
            model=model,
            prompt=prompt,
            images=images,
            messages=request.messages,
            video_media_id=video_media_id,
            target_token_id=target_token_id,
        )

    if request.contents:
        gemini_request = GeminiGenerateContentRequest(
            contents=_coerce_gemini_contents(request.contents),
            generationConfig=request.generationConfig,
        )
        normalized = await _normalize_gemini_request(request.model, gemini_request, raw_request=raw_request)
        normalized.messages = request.messages
        return normalized

    raise HTTPException(status_code=400, detail="Messages or contents cannot be empty")


async def _normalize_gemini_request(
    model: str,
    request: GeminiGenerateContentRequest,
    raw_request: Optional[Request] = None,
) -> NormalizedGenerationRequest:
    prompt, images, video_media_id = await _extract_prompt_and_images_from_gemini_contents(request.contents)
    resolved_model = _resolve_request_model(model, request, images=images, raw_request=raw_request)
    system_instruction = _extract_text_from_gemini_content(request.systemInstruction)
    model_config = MODEL_CONFIG.get(resolved_model)
    media_model = bool(model_config and model_config.get("type") in {"image", "video"})

    if media_model:
        prompt = _sanitize_media_prompt(prompt)

    if system_instruction:
        if media_model and _should_ignore_media_system_instruction(system_instruction):
            debug_logger.log_warning(
                f"[GEMINI] 忽略媒体模型的 systemInstruction: model={resolved_model}, len={len(system_instruction)}"
            )
        else:
            if media_model:
                system_instruction = _sanitize_media_prompt(system_instruction)
            prompt = f"{system_instruction}\n\n{prompt}".strip()

    return NormalizedGenerationRequest(
        model=resolved_model,
        prompt=prompt,
        images=images,
        video_media_id=video_media_id,
    )


async def _collect_non_stream_result(
    model: str,
    prompt: str,
    images: List[bytes],
    base_url_override: Optional[str] = None,
    video_media_id: Optional[str] = None,
    target_token_id: Optional[int] = None,
) -> str:
    handler = _ensure_generation_handler()
    result = None
    async for chunk in handler.handle_generation(
        model=model,
        prompt=prompt,
        images=images if images else None,
        stream=False,
        base_url_override=base_url_override,
        video_media_id=video_media_id,
        target_token_id=target_token_id,
    ):
        result = chunk

    if result is None:
        raise HTTPException(status_code=500, detail="Generation failed: No response")

    return result


def _parse_handler_result(result: str) -> Dict[str, Any]:
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        return {"result": result}


def _get_error_status_code(payload: Dict[str, Any]) -> int:
    error = payload.get("error")
    if isinstance(error, dict):
        status_code = error.get("status_code")
        if isinstance(status_code, int):
            return status_code
        if isinstance(status_code, str) and status_code.isdigit():
            return int(status_code)
        return 400
    return 200


def _build_openai_json_response(payload: Dict[str, Any]) -> JSONResponse:
    return JSONResponse(content=payload, status_code=_get_error_status_code(payload))


def _build_gemini_error_payload(status_code: int, message: str) -> Dict[str, Any]:
    return {
        "error": {
            "code": status_code,
            "message": message,
            "status": GEMINI_STATUS_MAP.get(status_code, "UNKNOWN"),
        }
    }


def _build_gemini_error_response_from_handler(payload: Dict[str, Any]) -> JSONResponse:
    error = payload.get("error", {})
    status_code = _get_error_status_code(payload)
    message = error.get("message", "Generation failed")
    return JSONResponse(
        status_code=status_code,
        content=_build_gemini_error_payload(status_code, message),
    )


def _extract_openai_message_content(payload: Dict[str, Any]) -> str:
    choices = payload.get("choices", [])
    if not choices:
        return payload.get("result", "")

    message = choices[0].get("message", {})
    content = message.get("content", "")
    return content if isinstance(content, str) else ""


def _extract_url_from_openai_payload(payload: Dict[str, Any]) -> Optional[str]:
    direct_url = payload.get("url")
    if isinstance(direct_url, str) and direct_url.strip():
        return direct_url.strip()

    content = _extract_openai_message_content(payload).strip()
    if not content:
        return None

    image_match = MARKDOWN_IMAGE_RE.search(content)
    if image_match:
        return image_match.group(1).strip()

    video_match = HTML_VIDEO_RE.search(content)
    if video_match:
        return video_match.group(1).strip()

    return None


def _enrich_payload_with_direct_url(payload: Dict[str, Any]) -> Dict[str, Any]:
    extracted_url = _extract_url_from_openai_payload(payload)
    if extracted_url and not payload.get("url"):
        payload["url"] = extracted_url
    return payload


async def _build_image_parts_from_uri(uri: str) -> List[Dict[str, Any]]:
    if uri.startswith("data:image"):
        mime_type, _ = _decode_data_url(uri)
        match = DATA_URL_RE.match(uri)
        if match:
            return [{"inlineData": {"mimeType": mime_type, "data": match.group("data")}}]

    image_bytes = await retrieve_image_data(uri)
    if image_bytes:
        mime_type = _detect_image_mime_type(
            image_bytes,
            fallback=_guess_mime_type(uri, "image/png"),
        )
        return [
            {
                "inlineData": {
                    "mimeType": mime_type,
                    "data": base64.b64encode(image_bytes).decode("ascii"),
                }
            }
        ]

    return [
        {
            "fileData": {
                "mimeType": _guess_mime_type(uri, "image/png"),
                "fileUri": uri,
            }
        },
        {"text": uri},
    ]


def _build_video_parts_from_uri(uri: str) -> List[Dict[str, Any]]:
    return [
        {
            "fileData": {
                "mimeType": _guess_mime_type(uri, "video/mp4"),
                "fileUri": uri,
            }
        }
    ]


async def _build_gemini_parts_from_output(output: str) -> List[Dict[str, Any]]:
    if not output:
        return []

    image_matches = MARKDOWN_IMAGE_RE.findall(output)
    if image_matches:
        parts: List[Dict[str, Any]] = []
        for uri in image_matches:
            parts.extend(await _build_image_parts_from_uri(uri))
        return parts

    video_matches = HTML_VIDEO_RE.findall(output)
    if video_matches:
        parts: List[Dict[str, Any]] = []
        for uri in video_matches:
            parts.extend(_build_video_parts_from_uri(uri))
        return parts

    return [{"text": output}]


async def _build_gemini_success_payload(
    payload: Dict[str, Any],
    response_model: str,
) -> Dict[str, Any]:
    output = _extract_openai_message_content(payload)
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": await _build_gemini_parts_from_output(output),
                },
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "modelVersion": response_model,
    }


def _normalize_finish_reason(reason: Optional[str]) -> Optional[str]:
    if reason is None:
        return None
    mapping = {
        "stop": "STOP",
        "length": "MAX_TOKENS",
        "content_filter": "SAFETY",
    }
    return mapping.get(reason, "STOP")


async def _convert_openai_stream_chunk_to_gemini_event(
    payload: Dict[str, Any],
    response_model: str,
) -> Optional[str]:
    choices = payload.get("choices", [])
    if not choices:
        return None

    choice = choices[0]
    delta = choice.get("delta", {})
    text = delta.get("reasoning_content") or delta.get("content") or ""
    finish_reason = _normalize_finish_reason(choice.get("finish_reason"))

    candidate: Dict[str, Any] = {"index": choice.get("index", 0)}
    if text:
        candidate["content"] = {
            "role": "model",
            "parts": await _build_gemini_parts_from_output(text),
        }
    if finish_reason:
        candidate["finishReason"] = finish_reason

    if len(candidate) == 1:
        return None

    chunk = {
        "candidates": [candidate],
        "modelVersion": response_model,
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


async def _iterate_openai_stream(
    normalized: NormalizedGenerationRequest,
    base_url_override: Optional[str] = None,
):
    handler = _ensure_generation_handler()
    async for chunk in handler.handle_generation(
        model=normalized.model,
        prompt=normalized.prompt,
        images=normalized.images if normalized.images else None,
        stream=True,
        base_url_override=base_url_override,
        video_media_id=normalized.video_media_id,
        target_token_id=normalized.target_token_id,
    ):
        if chunk.startswith("data: "):
            yield chunk
            continue

        payload = _parse_handler_result(chunk)
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    yield "data: [DONE]\n\n"


async def _iterate_gemini_stream(
    normalized: NormalizedGenerationRequest,
    response_model: str,
    base_url_override: Optional[str] = None,
):
    handler = _ensure_generation_handler()
    async for chunk in handler.handle_generation(
        model=normalized.model,
        prompt=normalized.prompt,
        images=normalized.images if normalized.images else None,
        stream=True,
        base_url_override=base_url_override,
        video_media_id=normalized.video_media_id,
    ):
        if chunk.startswith("data: "):
            payload_text = chunk[6:].strip()
            if payload_text == "[DONE]":
                continue
            payload = _parse_handler_result(payload_text)
            if "error" in payload:
                yield (
                    f"data: {json.dumps(_build_gemini_error_payload(_get_error_status_code(payload), payload['error'].get('message', 'Generation failed')), ensure_ascii=False)}\n\n"
                )
                return

            event = await _convert_openai_stream_chunk_to_gemini_event(
                payload,
                response_model,
            )
            if event:
                yield event
            continue

        payload = _parse_handler_result(chunk)
        if "error" in payload:
            yield (
                f"data: {json.dumps(_build_gemini_error_payload(_get_error_status_code(payload), payload['error'].get('message', 'Generation failed')), ensure_ascii=False)}\n\n"
            )
            return

        event = await _convert_openai_stream_chunk_to_gemini_event(
            payload,
            response_model,
        )
        if event:
            yield event


async def _reconcile_browser_account_core(
    request: PluginAccountImportRequest,
    handler: GenerationHandler,
) -> Dict[str, Any]:
    from ..services.protocol_login import protocol_loginer
    import hashlib

    raw_cookies = (request.google_cookies or "").strip()
    if not raw_cookies:
        raise HTTPException(status_code=400, detail="请求缺少 google_cookies 凭据")

    # 1. 在线验证并提取真实邮箱与轮换 Cookie
    login_result = await protocol_loginer.login(
        raw_cookies=raw_cookies,
        proxy=request.proxy_url,
        email=request.email,
    )
    if not login_result.get("success"):
        raise HTTPException(status_code=400, detail=f"Flow 登录态在线验证失败: {login_result.get('error')}")

    validated_email = (login_result.get("email") or request.email or "").strip().lower()
    rotated_cookies = login_result.get("google_cookies") or raw_cookies

    # 2. 匹配已有账号
    all_tokens = await handler.token_manager.get_all_tokens()
    existing = None

    if request.token_id:
        for t in all_tokens:
            if t.id == request.token_id:
                existing = t
                break

    if not existing and validated_email:
        for t in all_tokens:
            if t.email and t.email.strip().lower() == validated_email:
                existing = t
                break

    route_key = (request.extension_route_key or "").strip()
    if not existing and route_key:
        for t in all_tokens:
            if t.extension_route_key == route_key:
                existing = t
                break

    # 兼容迁移：数据库只有唯一未绑定 cookie 的旧 Token 时复用
    if not existing and len(all_tokens) == 1 and not getattr(all_tokens[0], "google_cookies", ""):
        existing = all_tokens[0]

    project_id = (request.project_id or "").strip() or None
    project_name = (request.project_name or "").strip() or None
    if not project_id and existing and existing.current_project_id:
        project_id = existing.current_project_id
        project_name = existing.current_project_name

    if not existing and not project_id:
        raise HTTPException(
            status_code=400,
            detail="首次添加新账号必须提供有效的 Flow 项目 ID (请在浏览器中打开项目页后同步)",
        )

    now_utc = datetime.now(timezone.utc)
    st_val = "frontend-" + hashlib.sha256(rotated_cookies.encode("utf-8")).hexdigest()[:32]
    at_val = "flow-frontend-cookie"
    at_expires = now_utc + timedelta(days=365)

    common_kwargs: Dict[str, Any] = dict(
        st=st_val,
        at=at_val,
        at_expires=at_expires,
        protocol_mode="protocol",
        google_cookies=rotated_cookies,
        email=validated_email or (existing.email if existing else "unknown@google.com"),
        login_account=validated_email or (existing.login_account if existing else None),
        auto_refresh_enabled=True,
        refresh_interval_minutes=request.refresh_interval_minutes or 120,
        last_st_refresh_at=now_utc,
        last_st_refresh_result="success",
        extension_route_key=route_key or (existing.extension_route_key if existing else None),
    )
    if request.proxy_url:
        common_kwargs["proxy_url"] = request.proxy_url

    action = "updated"
    token_id = None
    if existing:
        token_id = existing.id
        # 使用 CAS (若原有 google_cookies 存在)
        if existing.google_cookies:
            cas_ok = await handler.token_manager.db.update_token_if_cookie_matches(
                existing.id,
                expected_cookies=existing.google_cookies,
                **common_kwargs,
            )
            if not cas_ok:
                debug_logger.log_info(f"[RECONCILE] Token {existing.id} CAS 冲突，采用直接覆盖写入最新同步凭据")
                await handler.token_manager.db.update_token(existing.id, **common_kwargs)
        else:
            await handler.token_manager.db.update_token(existing.id, **common_kwargs)
    else:
        new_token = await handler.token_manager.add_token(
            project_id=project_id,
            project_name=project_name or "Flow Project",
            image_enabled=True,
            video_enabled=True,
            image_concurrency=-1,
            video_concurrency=-1,
            **common_kwargs,
        )
        token_id = new_token.id
        action = "created"

    # 读取并更新余额
    credits_val = 0
    try:
        updated_token = await handler.token_manager.get_token(token_id)
        if updated_token:
            credits_info, new_rotated = await handler.token_manager.flow_client.get_credits_rpc(updated_token)
            credits_val = credits_info.get("credits", 0)
            tier_val = credits_info.get("userPaygateTier")
            await handler.token_manager.db.update_token(
                token_id,
                credits=credits_val,
                user_paygate_tier=tier_val,
            )
    except Exception as e:
        debug_logger.log_info(f"[RECONCILE] 读取最新余额跳过: {e}")

    fingerprint = hashlib.sha256(rotated_cookies.encode("utf-8")).hexdigest()[:16]
    debug_logger.log_info(f"[RECONCILE] 账号协调同步成功: id={token_id}, email={validated_email}, action={action}")

    return {
        "success": True,
        "action": action,
        "token_id": token_id,
        "email": validated_email,
        "project_id": project_id,
        "credits": credits_val,
        "cookie_snapshot_fingerprint": fingerprint,
        "message": f"账号同步成功: {validated_email} ({action})",
    }


@router.post("/api/plugin/reconcile-browser-account")
async def reconcile_browser_account(
    request: PluginAccountImportRequest,
    api_key: str = Depends(verify_api_key_flexible),
):
    """Host Agent & browser coordination endpoint for Cookie-first account synchronization."""
    handler = _ensure_generation_handler()
    try:
        return await _reconcile_browser_account_core(request, handler)
    except HTTPException:
        raise
    except Exception as e:
        debug_logger.log_error(f"[RECONCILE] 协调账号失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/plugin/import-current-account")
async def import_current_browser_account(
    request: PluginAccountImportRequest,
    api_key: str = Depends(verify_api_key_flexible),
):
    """Import the currently signed-in browser account from the local extension."""
    handler = _ensure_generation_handler()
    try:
        res = await _reconcile_browser_account_core(request, handler)
        return {
            "success": True,
            "added": 1 if res.get("action") == "created" else 0,
            "updated": 1 if res.get("action") == "updated" else 0,
            "email": res.get("email"),
            "token_id": res.get("token_id"),
            "expires": None,
            "message": res.get("message"),
        }
    except Exception as e:
        detail = getattr(e, "detail", str(e))
        debug_logger.log_info(f"[PLUGIN_IMPORT] 扩展同步提示: {detail}")
        return {
            "success": False,
            "added": 0,
            "updated": 0,
            "message": str(detail),
        }


@router.get("/api/plugin/version")
async def get_plugin_version():
    """Get latest extension version information."""
    # 固定返回 1.1.38 兼容版本号，避免未更新文件的远程客户端陷入重启死循环
    return {
        "version": "1.1.47",
        "name": "Flow2API Captcha Worker"
    }


@router.get("/api/plugin/download")
async def download_plugin_zip():
    """Download zipped extension for remote machines."""
    extension_dir = Path(__file__).parent.parent.parent / "extension"
    if not extension_dir.exists():
        raise HTTPException(status_code=404, detail="Extension directory not found")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in extension_dir.rglob("*"):
            if file_path.is_file():
                rel_path = file_path.relative_to(extension_dir)
                if any(part.startswith(".") for part in rel_path.parts):
                    continue
                zip_file.write(file_path, arcname=str(rel_path))

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=flow2api-worker.zip"}
    )


@router.post("/api/plugin/reload")
async def reload_all_plugins(api_key: str = Depends(verify_api_key_flexible)):
    """Broadcast hot reload command to all connected browser extensions."""
    service = await ExtensionCaptchaService.get_instance()
    reloaded_count = await service.broadcast_reload()
    return {
        "success": True,
        "reloaded_clients": reloaded_count,
        "message": f"已向 {reloaded_count} 个在线扩展发送热重载指令"
    }


@router.get("/v1/models")
async def list_models(api_key: str = Depends(verify_api_key_flexible)):
    """Keep agent model discovery independent of the generation protocol."""
    return {
        "object": "list",
        "data": [
            {**model, "object": "model", "owned_by": "flow2api"}
            for model in _get_openai_model_catalog()
        ],
    }


@router.get("/v1beta/models")
@router.get("/models")
async def list_gemini_models(api_key: str = Depends(verify_api_key_flexible)):
    """List compact public models using Gemini-compatible response shape."""
    catalog = _get_gemini_model_catalog()
    return {
        "models": [
            _build_gemini_model_resource(model_id, description)
            for model_id, description in catalog.items()
        ]
    }


@router.get("/v1beta/models/internal")
@router.get("/models/internal")
async def list_internal_gemini_models(api_key: str = Depends(verify_api_key_flexible)):
    """List all internal long model IDs using Gemini-compatible response shape."""
    catalog = _get_internal_gemini_model_catalog()
    return {
        "models": [
            _build_gemini_model_resource(model_id, description)
            for model_id, description in catalog.items()
        ]
    }


@router.get("/v1beta/models/{model}")
@router.get("/models/{model}")
async def get_gemini_model(model: str, api_key: str = Depends(verify_api_key_flexible)):
    """Return a single model using Gemini-compatible response shape."""
    catalog = _get_gemini_model_catalog()
    description = catalog.get(model)
    if not description:
        return JSONResponse(
            status_code=404,
            content=_build_gemini_error_payload(404, f"Model not found: {model}"),
        )

    return _build_gemini_model_resource(model, description)


@router.post("/v1beta/models/{model}:predictLongRunning")
@router.post("/models/{model}:predictLongRunning")
async def predict_video_long_running(
    model: str, payload: Dict[str, Any], api_key: str = Depends(verify_api_key_flexible),
):
    model = {
        "veo-3.1-generate-preview": "Veo 3.1 - Quality",
        "veo-3.1-fast-generate-preview": "Veo 3.1 - Fast",
        "veo-3.1-lite-generate-preview": "Veo 3.1 - Lite",
    }.get(model, model)
    instances = payload.get("instances")
    if not isinstance(instances, list) or len(instances) != 1 or not isinstance(instances[0], dict):
        return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "Exactly one video instance is required"))
    instance = instances[0]
    prompt = str(instance.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "Prompt cannot be empty"))
    parameters = payload.get("parameters") or {}
    if not isinstance(parameters, dict):
        return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "parameters must be an object"))
    if parameters.get("sampleCount", 1) != 1:
        return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "Only one generated video is supported"))
    aspect_ratio = parameters.get("aspectRatio") or "16:9"
    if aspect_ratio not in {"16:9", "9:16"}:
        return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "Unsupported video aspectRatio"))
    duration = parameters.get("durationSeconds")
    if duration is not None and (str(duration) not in {"4", "6", "8", "10"} or (str(duration) == "10" and model != "Omni 1.1 Flash")):
        return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "Unsupported video duration"))
    images = []
    for frame_field in ("image", "lastFrame"):
        image = instance.get(frame_field)
        if image is None:
            continue
        if not isinstance(image, dict):
            return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "Unsupported input image"))
        try:
            inline_data = image.get("inlineData")
            if isinstance(inline_data, dict):
                mime_type = inline_data.get("mimeType")
                encoded = inline_data.get("data")
                if mime_type not in {"image/png", "image/jpeg", "image/webp"} or not isinstance(encoded, str) or not encoded:
                    raise ValueError
                images.append(base64.b64decode(encoded, validate=True))
            elif image.get("fileUri"):
                if image.get("mimeType") not in {"image/png", "image/jpeg", "image/webp"}:
                    raise ValueError
                images.append(await _load_image_bytes_from_uri(str(image["fileUri"])))
            elif image.get("bytesBase64Encoded"):
                if image.get("mimeType") not in {"image/png", "image/jpeg", "image/webp"}:
                    raise ValueError
                images.append(base64.b64decode(image["bytesBase64Encoded"], validate=True))
            else:
                raise ValueError
        except (ValueError, base64.binascii.Error):
            return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "Invalid base64 input image"))
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content=_build_gemini_error_payload(exc.status_code, str(exc.detail)))
    if instance.get("lastFrame") is not None and instance.get("image") is None:
        return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "lastFrame requires a first input image"))
    public_veo_family = {
        "Veo 3.1 - Quality": "veo", "Veo 3.1 - Fast": "veo-fast", "Veo 3.1 - Lite": "veo-lite",
    }.get(model)
    resolution_model = public_veo_family or model
    if images and public_veo_family:
        resolution_model = {
            "veo": "veo-i2v", "veo-fast": "veo-i2v-fast", "veo-lite": "veo-i2v-lite",
        }[public_veo_family]
    params = SimpleNamespace(generationConfig={"aspectRatio": aspect_ratio, "durationSeconds": duration})
    resolved = _resolve_request_model(resolution_model, params, images=images)
    model_config = MODEL_CONFIG.get(resolved)
    if not model_config or model_config.get("type") != "video":
        return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, f"Model is not a video model: {model}"))
    requested_aspect = "VIDEO_ASPECT_RATIO_PORTRAIT" if aspect_ratio == "9:16" else "VIDEO_ASPECT_RATIO_LANDSCAPE"
    if model_config.get("aspect_ratio") != requested_aspect:
        return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "Model does not support requested aspectRatio"))
    if duration is not None:
        match = re.search(r"(?:^|_)(4|6|8|10)s(?:_|$)", resolved)
        actual_duration = model_config.get("reference_duration") or (int(match.group(1)) if match else 8)
        if int(duration) != actual_duration:
            return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "Model does not support requested durationSeconds"))
    if images and model_config.get("video_type") not in {"i2v", "omni"}:
        return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "Model does not support input image"))
    if images and len(images) > model_config.get("max_images", 0):
        return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "Model does not support this number of input images"))
    if not images and model_config.get("video_type") not in {"t2v", "omni"}:
        return JSONResponse(status_code=400, content=_build_gemini_error_payload(400, "Model requires an input image"))
    try:
        name = await _ensure_generation_handler().submit_gemini_video(model=resolved, prompt=prompt, images=images)
        return {"name": name, "done": False}
    except ValueError as exc:
        return JSONResponse(status_code=503, content=_build_gemini_error_payload(503, str(exc)))
    except Exception as exc:
        logging.getLogger(__name__).exception("[GEMINI VIDEO] Submission failed")
        reason = " ".join(str(exc).split())[:500] or type(exc).__name__
        if "MODEL_ACCESS_DENIED" in reason:
            return JSONResponse(
                status_code=403,
                content=_build_gemini_error_payload(
                    403,
                    "Selected Flow account cannot access this video model; choose an available model or account",
                ),
            )
        return JSONResponse(
            status_code=502,
            content=_build_gemini_error_payload(502, f"Video submission failed: {reason}"),
        )


@router.get("/v1beta/operations/{operation_id}")
@router.get("/operations/{operation_id}")
@router.get("/v1beta/models/{model}/operations/{operation_id}")
@router.get("/models/{model}/operations/{operation_id}")
async def poll_video_operation(operation_id: str, raw_request: Request, model: Optional[str] = None, api_key: str = Depends(verify_api_key_flexible)):
    name = f"operations/{operation_id}"
    try:
        operation = await _ensure_generation_handler().get_gemini_video_operation(name)
    except Exception as exc:
        debug_logger.log_error(f"[GEMINI VIDEO] Poll failed: {exc}")
        return JSONResponse(status_code=502, content=_build_gemini_error_payload(502, "Video status lookup failed"))
    if operation is None:
        return JSONResponse(status_code=404, content=_build_gemini_error_payload(404, "Video operation not found"))
    if operation.get("done") and "response" in operation:
        for sample in operation.get("response", {}).get("generateVideoResponse", {}).get("generatedSamples", []):
            video = sample.get("video", {})
            uri = sample.get("uri") or video.get("uri") or ""
            if uri.startswith("/tmp/"):
                uri = f"{_get_request_base_url(raw_request)}{uri}"
            if uri:
                sample["uri"] = uri
                video["uri"] = uri
                sample["video"] = video
    return operation


@router.post("/v1/chat/completions")
async def create_chat_completion(
    request: ChatCompletionRequest,
    raw_request: Request,
    api_key: str = Depends(verify_api_key_flexible),
):
    """OpenAI-compatible unified generation endpoint."""
    try:
        normalized = await _normalize_openai_request(request, raw_request=raw_request)
        if not normalized.prompt:
            raise HTTPException(status_code=400, detail="Prompt cannot be empty")

        request_base_url = _get_request_base_url(raw_request)

        if request.stream:
            return StreamingResponse(
                _iterate_openai_stream(normalized, base_url_override=request_base_url),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        payload = _enrich_payload_with_direct_url(
            _parse_handler_result(
                await _collect_non_stream_result(
                    normalized.model,
                    normalized.prompt,
                    normalized.images,
                    base_url_override=request_base_url,
                    video_media_id=normalized.video_media_id,
                    target_token_id=normalized.target_token_id,
                )
            )
        )
        return _build_openai_json_response(payload)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/v1beta/models/{model}:generateContent")
@router.post("/models/{model}:generateContent")
async def generate_content(
    model: str,
    request: GeminiGenerateContentRequest,
    raw_request: Request,
    api_key: str = Depends(verify_api_key_flexible),
):
    """Gemini official generateContent endpoint."""
    try:
        normalized = await _normalize_gemini_request(model, request, raw_request=raw_request)
        if not normalized.prompt:
            raise HTTPException(status_code=400, detail="Prompt cannot be empty")

        request_base_url = _get_request_base_url(raw_request)

        payload = _enrich_payload_with_direct_url(
            _parse_handler_result(
                await _collect_non_stream_result(
                    normalized.model,
                    normalized.prompt,
                    normalized.images,
                    base_url_override=request_base_url,
                    video_media_id=normalized.video_media_id,
                    target_token_id=normalized.target_token_id,
                )
            )
        )
        if "error" in payload:
            return _build_gemini_error_response_from_handler(payload)

        return JSONResponse(
            content=await _build_gemini_success_payload(payload, normalized.model)
        )

    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=_build_gemini_error_payload(exc.status_code, str(exc.detail)),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=_build_gemini_error_payload(500, str(exc)),
        )


@router.post("/v1beta/models/{model}:streamGenerateContent")
@router.post("/models/{model}:streamGenerateContent")
async def stream_generate_content(
    model: str,
    request: GeminiGenerateContentRequest,
    raw_request: Request,
    alt: Optional[str] = Query(None),
    api_key: str = Depends(verify_api_key_flexible),
):
    """Gemini official streamGenerateContent endpoint."""
    try:
        normalized = await _normalize_gemini_request(model, request, raw_request=raw_request)
        if not normalized.prompt:
            raise HTTPException(status_code=400, detail="Prompt cannot be empty")

        request_base_url = _get_request_base_url(raw_request)

        return StreamingResponse(
            _iterate_gemini_stream(normalized, normalized.model, request_base_url),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=_build_gemini_error_payload(exc.status_code, str(exc.detail)),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content=_build_gemini_error_payload(500, str(exc)),
        )

@router.websocket("/captcha_ws")
async def captcha_websocket_endpoint(websocket: WebSocket):
    from ..core.logger import debug_logger
    api_key = (
        websocket.query_params.get("key")
        or websocket.query_params.get("api_key")
        or websocket.headers.get("x-goog-api-key")
        or ""
    ).strip()
    authorization = (websocket.headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        api_key = authorization[7:].strip()

    if not api_key or not AuthManager.verify_api_key(api_key):
        await websocket.close(code=1008)
        return

    service = await ExtensionCaptchaService.get_instance()
    await service.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await service.handle_message(websocket, data)
    except WebSocketDisconnect:
        service.disconnect(websocket)
    except Exception as e:
        debug_logger.log_error(f"WebSocket error: {e}")
        service.disconnect(websocket)
