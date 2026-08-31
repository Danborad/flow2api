import base64
import types
import unittest
from io import BytesIO
from unittest.mock import AsyncMock, patch

from PIL import Image

from src.api.routes import _normalize_gemini_request, _normalize_openai_request
from src.core.models import ChatCompletionRequest, ChatMessage, GeminiGenerateContentRequest
from src.core.model_resolver import resolve_model_name
from src.services.flow_client import FlowClient
from src.services.generation_handler import MODEL_CONFIG, GenerationHandler


def _make_image_bytes(size: tuple[int, int], color: str = "white") -> bytes:
    image = Image.new("RGB", size, color=color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class VeoLiteModelResolverTests(unittest.TestCase):
    def test_resolve_nano_banana_pro_alias_with_image_options(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(
                imageConfig={"aspectRatio": "3:4", "imageSize": "4K"}
            )
        )

        resolved = resolve_model_name(
            "nano-banana-pro",
            request=request,
            model_config=MODEL_CONFIG,
        )

        self.assertEqual(resolved, "gemini-3.0-pro-image-three-four-4k")

    def test_resolve_nano_banana_2_alias_with_openai_size_and_quality(self):
        request = types.SimpleNamespace(
            __pydantic_extra__={"size": "1024x1024", "quality": "high"}
        )

        resolved = resolve_model_name(
            "nano-banana-2",
            request=request,
            model_config=MODEL_CONFIG,
        )

        self.assertEqual(resolved, "gemini-3.1-flash-image-square-4k")

    def test_resolve_friendly_veo_alias_with_duration_resolution_and_aspect(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(
                aspectRatio="portrait",
                imageSize="1080p",
                durationSeconds=6,
            )
        )

        resolved = resolve_model_name(
            "veo",
            request=request,
            model_config=MODEL_CONFIG,
        )

        self.assertEqual(resolved, "veo_3_1_t2v_portrait_6s_1080p")

    def test_resolve_friendly_i2v_fast_alias_with_duration(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(aspectRatio="9:16", duration="4s")
        )

        resolved = resolve_model_name(
            "veo-i2v-fast",
            request=request,
            model_config=MODEL_CONFIG,
        )

        self.assertEqual(resolved, "veo_3_1_i2v_s_fast_portrait_4s_fl")

    def test_public_veo_alias_ignores_duration(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(
                aspectRatio="16:9",
                durationSeconds=4,
            ),
            messages=[],
        )

        resolved = resolve_model_name(
            "Veo 3.1 - Fast",
            request=request,
            model_config=MODEL_CONFIG,
        )

        self.assertEqual(resolved, "veo_3_1_t2v_fast_landscape")

    def test_omni_11_alias_uses_duration_specific_config(self):
        for duration, expected in (
            (4, "omni_4s"),
            (6, "omni_6s"),
            (8, "omni_8s"),
            (10, "omni_10s"),
        ):
            request = types.SimpleNamespace(
                generationConfig=types.SimpleNamespace(
                    aspectRatio="16:9",
                    durationSeconds=duration,
                ),
                messages=[],
            )

            resolved = resolve_model_name(
                "Omni 1.1 Flash",
                request=request,
                model_config=MODEL_CONFIG,
            )

            self.assertEqual(resolved, expected)
            self.assertEqual(MODEL_CONFIG[resolved]["model_key"], f"abra_t2v_{duration}s")
            self.assertEqual(MODEL_CONFIG[resolved]["first_frame_model_key"], f"abra_i2v_{duration}s")
            self.assertEqual(MODEL_CONFIG[resolved]["start_end_model_key"], f"abra_i2v_{duration}s")
            self.assertEqual(MODEL_CONFIG[resolved]["reference_model_key"], f"abra_r2v_{duration}s")

    def test_omni_11_alias_accepts_legacy_name(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(
                aspectRatio="16:9",
                durationSeconds=6,
            ),
            messages=[],
        )

        self.assertEqual(
            resolve_model_name("Omni Flash", request=request, model_config=MODEL_CONFIG),
            resolve_model_name("Omni 1.1 Flash", request=request, model_config=MODEL_CONFIG),
        )

    def test_resolve_t2v_lite_alias_to_portrait_variant(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(aspectRatio="portrait")
        )

        resolved = resolve_model_name(
            "veo_3_1_t2v_lite",
            request=request,
            model_config=MODEL_CONFIG,
        )

        self.assertEqual(resolved, "veo_3_1_t2v_lite_portrait")

    def test_resolve_quality_4s_upsample_alias_to_portrait_variant(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(aspectRatio="portrait")
        )

        resolved = resolve_model_name(
            "veo_3_1_t2v_4s_4k",
            request=request,
            model_config=MODEL_CONFIG,
        )

        self.assertEqual(resolved, "veo_3_1_t2v_portrait_4s_4k")

    def test_resolve_video_image_size_to_upsample_variant(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(aspectRatio="landscape", imageSize="1080p")
        )

        resolved = resolve_model_name(
            "veo_3_1_i2v_s_6s",
            request=request,
            model_config=MODEL_CONFIG,
        )

        self.assertEqual(resolved, "veo_3_1_i2v_s_6s_1080p")

    def test_resolve_quality_8s_alias_to_portrait_variant(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(aspectRatio="portrait")
        )

        resolved = resolve_model_name(
            "veo_3_1_t2v_8s",
            request=request,
            model_config=MODEL_CONFIG,
        )

        self.assertEqual(resolved, "veo_3_1_t2v_portrait_8s")

    def test_resolve_quality_8s_upsample_alias_to_portrait_variant(self):
        request = types.SimpleNamespace(
            generationConfig=types.SimpleNamespace(aspectRatio="portrait", imageSize="4k")
        )

        resolved = resolve_model_name(
            "veo_3_1_i2v_s_8s",
            request=request,
            model_config=MODEL_CONFIG,
        )

        self.assertEqual(resolved, "veo_3_1_i2v_s_portrait_8s_4k")

    def test_image_model_follows_reference_image_aspect_ratio(self):
        request = types.SimpleNamespace(generationConfig=None)
        portrait_image = _make_image_bytes((900, 1600))

        resolved = resolve_model_name(
            "gemini-3.0-pro-image",
            request=request,
            model_config=MODEL_CONFIG,
            images=[portrait_image],
        )

        self.assertEqual(resolved, "gemini-3.0-pro-image-portrait")


class VeoLiteGenerationHandlerTests(unittest.TestCase):
    def test_tier_two_does_not_upgrade_lite_model_to_fake_ultra(self):
        handler = GenerationHandler.__new__(GenerationHandler)

        model_key, message = handler._resolve_video_model_key_for_tier(
            {
                "model_key": "veo_3_1_t2v_lite",
                "allow_tier_upgrade": False,
            },
            "PAYGATE_TIER_TWO",
        )

        self.assertEqual(model_key, "veo_3_1_t2v_lite")
        self.assertIsNone(message)

    def test_tier_two_still_upgrades_regular_model(self):
        handler = GenerationHandler.__new__(GenerationHandler)

        model_key, message = handler._resolve_video_model_key_for_tier(
            {
                "model_key": "veo_3_1_t2v_fast",
            },
            "PAYGATE_TIER_TWO",
        )

        self.assertEqual(model_key, "veo_3_1_t2v_fast_ultra")
        self.assertIn("ultra", message)

    def test_quality_model_does_not_upgrade_to_fake_ultra(self):
        handler = GenerationHandler.__new__(GenerationHandler)

        model_key, message = handler._resolve_video_model_key_for_tier(
            {
                "model_key": "veo_3_1_t2v",
            },
            "PAYGATE_TIER_TWO",
        )

        self.assertEqual(model_key, "veo_3_1_t2v")
        self.assertIsNone(message)

    def test_quality_4s_upsample_model_generates_then_upsamples(self):
        cfg = MODEL_CONFIG["veo_3_1_t2v_4s_4k"]

        self.assertEqual(cfg["model_key"], "veo_3_1_t2v_quality_4s")
        self.assertEqual(cfg["video_type"], "t2v")
        self.assertEqual(cfg["upsample"]["model_key"], "veo_3_1_upsampler_4k")
        self.assertEqual(cfg["upsample"]["resolution"], "VIDEO_RESOLUTION_4K")

    def test_quality_6s_i2v_1080p_model_generates_then_upsamples(self):
        cfg = MODEL_CONFIG["veo_3_1_i2v_s_6s_1080p"]

        self.assertEqual(cfg["model_key"], "veo_3_1_i2v_s_quality_6s_fl")
        self.assertEqual(cfg["video_type"], "i2v")
        self.assertEqual(cfg["upsample"]["model_key"], "veo_3_1_upsampler_1080p")
        self.assertEqual(cfg["upsample"]["resolution"], "VIDEO_RESOLUTION_1080P")

    def test_explicit_8s_aliases_reuse_default_upstream_keys(self):
        self.assertEqual(MODEL_CONFIG["veo_3_1_t2v_8s"]["model_key"], "veo_3_1_t2v")
        self.assertEqual(MODEL_CONFIG["veo_3_1_i2v_s_8s"]["model_key"], "veo_3_1_i2v_s_fl")
        self.assertEqual(
            MODEL_CONFIG["veo_3_1_r2v_fast_ultra_8s"]["model_key"],
            "veo_3_1_r2v_fast_landscape_ultra",
        )

    def test_short_duration_models_include_explicit_landscape_aliases(self):
        expected_aliases = {
            "veo_3_1_t2v_landscape_4s": "veo_3_1_t2v_4s",
            "veo_3_1_t2v_landscape_6s": "veo_3_1_t2v_6s",
            "veo_3_1_i2v_s_landscape_4s": "veo_3_1_i2v_s_4s",
            "veo_3_1_i2v_s_landscape_6s": "veo_3_1_i2v_s_6s",
            "veo_3_1_t2v_landscape_4s_4k": "veo_3_1_t2v_4s_4k",
            "veo_3_1_i2v_s_landscape_6s_1080p": "veo_3_1_i2v_s_6s_1080p",
        }

        for alias, target in expected_aliases.items():
            self.assertIn(alias, MODEL_CONFIG)
            self.assertEqual(MODEL_CONFIG[alias], MODEL_CONFIG[target])

    def test_default_duration_models_include_explicit_8s_aliases(self):
        expected_aliases = {
            "veo_3_1_t2v_landscape_8s": "veo_3_1_t2v_8s",
            "veo_3_1_t2v_landscape_8s_4k": "veo_3_1_t2v_8s_4k",
            "veo_3_1_t2v_lite_landscape_8s": "veo_3_1_t2v_lite_8s_landscape",
            "veo_3_1_i2v_s_landscape_8s": "veo_3_1_i2v_s_8s",
            "veo_3_1_i2v_s_landscape_8s_1080p": "veo_3_1_i2v_s_8s_1080p",
            "veo_3_1_i2v_lite_landscape_8s": "veo_3_1_i2v_lite_8s_landscape",
            "veo_3_1_interpolation_lite_landscape_8s": "veo_3_1_interpolation_lite_8s_landscape",
            "veo_3_1_r2v_fast_landscape_8s": "veo_3_1_r2v_fast_8s",
            "veo_3_1_r2v_fast_landscape_ultra_8s": "veo_3_1_r2v_fast_ultra_8s",
            "veo_3_1_r2v_fast_landscape_ultra_relaxed_8s": "veo_3_1_r2v_fast_ultra_relaxed_8s",
        }

        for alias, target in expected_aliases.items():
            self.assertIn(alias, MODEL_CONFIG)
            self.assertEqual(MODEL_CONFIG[alias], MODEL_CONFIG[target])

    def test_r2v_models_include_explicit_landscape_aliases(self):
        expected_aliases = {
            "veo_3_1_r2v_fast_landscape": "veo_3_1_r2v_fast",
            "veo_3_1_r2v_fast_landscape_ultra": "veo_3_1_r2v_fast_ultra",
            "veo_3_1_r2v_fast_landscape_ultra_relaxed": "veo_3_1_r2v_fast_ultra_relaxed",
            "veo_3_1_r2v_fast_landscape_ultra_4k": "veo_3_1_r2v_fast_ultra_4k",
            "veo_3_1_r2v_fast_landscape_ultra_1080p": "veo_3_1_r2v_fast_ultra_1080p",
        }

        for alias, target in expected_aliases.items():
            self.assertIn(alias, MODEL_CONFIG)
            self.assertEqual(MODEL_CONFIG[alias], MODEL_CONFIG[target])

    def test_direct_upsampler_keys_are_not_public_models(self):
        self.assertNotIn("veo_3_1_upsampler_4k", MODEL_CONFIG)
        self.assertNotIn("veo_3_1_upsampler_1080p", MODEL_CONFIG)


class VeoLiteFlowClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FlowClient(proxy_manager=None)
        self.client._acquire_video_launch_gate = AsyncMock(return_value=(True, None, None))
        self.client._release_video_launch_gate = AsyncMock()
        self.client._get_recaptcha_token = AsyncMock(return_value=("recaptcha-token", "browser-1"))
        self.client._notify_browser_captcha_request_finished = AsyncMock()

    async def test_generate_video_text_uses_v2_payload_for_lite(self):
        captured = {}

        async def fake_make_request(method, url, json_data, use_at, at_token, **kwargs):
            captured["url"] = url
            captured["json_data"] = json_data
            return {"operations": [{"operation": {"name": "task-1"}}]}

        self.client._make_request = AsyncMock(side_effect=fake_make_request)

        await self.client.generate_video_text(
            at="at-token",
            project_id="project-1",
            prompt="猫猫",
            model_key="veo_3_1_t2v_lite",
            aspect_ratio="VIDEO_ASPECT_RATIO_LANDSCAPE",
            use_v2_model_config=True,
        )

        json_data = captured["json_data"]
        request_data = json_data["requests"][0]
        self.assertTrue(json_data["useV2ModelConfig"])
        self.assertIn("batchId", json_data["mediaGenerationContext"])
        self.assertEqual(
            request_data["textInput"]["structuredPrompt"]["parts"][0]["text"],
            "猫猫",
        )
        self.assertNotIn("prompt", request_data["textInput"])
        self.assertEqual(request_data["videoModelKey"], "veo_3_1_t2v_lite")
        self.assertEqual(
            json_data["mediaGenerationContext"]["audioFailurePreference"],
            "BLOCK_SILENCED_VIDEOS",
        )

    async def test_generate_video_text_normalizes_media_only_create_response(self):
        captured = {}

        async def fake_make_request(method, url, json_data, use_at, at_token, **kwargs):
            captured["json_data"] = json_data
            return {
                "remainingCredits": 30,
                "workflows": [
                    {
                        "name": "workflow-1",
                        "metadata": {"primaryMediaId": "media-1"},
                        "projectId": "project-1",
                    }
                ],
                "media": [
                    {
                        "name": "media-1",
                        "projectId": "project-1",
                        "mediaMetadata": {
                            "mediaStatus": {
                                "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_PENDING"
                            }
                        },
                    }
                ],
            }

        self.client._make_request = AsyncMock(side_effect=fake_make_request)

        result = await self.client.generate_video_text(
            at="at-token",
            project_id="project-1",
            prompt="猫猫",
            model_key="veo_3_1_t2v_lite",
            aspect_ratio="VIDEO_ASPECT_RATIO_LANDSCAPE",
            use_v2_model_config=True,
        )

        self.assertEqual(
            captured["json_data"]["mediaGenerationContext"]["audioFailurePreference"],
            "BLOCK_SILENCED_VIDEOS",
        )
        self.assertEqual(result["operations"][0]["operation"]["name"], "media-1")
        self.assertEqual(result["operations"][0]["projectId"], "project-1")
        self.assertEqual(
            result["operations"][0]["status"],
            "MEDIA_GENERATION_STATUS_PENDING",
        )


class RouteNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FlowClient(proxy_manager=None)
        self.client._acquire_video_launch_gate = AsyncMock(return_value=(True, None, None))
        self.client._release_video_launch_gate = AsyncMock()
        self.client._get_recaptcha_token = AsyncMock(return_value=("recaptcha-token", "browser-1"))
        self.client._notify_browser_captcha_request_finished = AsyncMock()

    async def test_openai_history_reference_image_can_drive_aspect_ratio(self):
        portrait_image = _make_image_bytes((900, 1600))
        request = ChatCompletionRequest(
            model="gemini-3.0-pro-image",
            messages=[
                ChatMessage(role="user", content="先生成一张图"),
                ChatMessage(
                    role="assistant",
                    content="![cat](https://example.com/cat.png)",
                ),
                ChatMessage(role="user", content="基于上一张图继续编辑"),
            ],
        )

        with patch(
            "src.api.routes.retrieve_image_data",
            new=AsyncMock(return_value=portrait_image),
        ):
            normalized = await _normalize_openai_request(request)

        self.assertEqual(normalized.model, "gemini-3.0-pro-image-portrait")
        self.assertEqual(len(normalized.images), 1)

    async def test_openai_and_gemini_reference_image_use_same_lite_i2v_route(self):
        encoded_image = base64.b64encode(_make_image_bytes((1600, 900))).decode()
        openai_request = ChatCompletionRequest.model_validate(
            {
                "model": "Veo 3.1 - Lite",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "让图片动起来"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{encoded_image}"
                                },
                            },
                        ],
                    }
                ],
                "generationConfig": {"aspectRatio": "9:16"},
            }
        )
        gemini_request = GeminiGenerateContentRequest.model_validate(
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": "让图片动起来"},
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": encoded_image,
                                }
                            },
                        ],
                    }
                ],
                "generationConfig": {"aspectRatio": "9:16"},
            }
        )

        openai_normalized = await _normalize_openai_request(openai_request)
        gemini_normalized = await _normalize_gemini_request(
            "Veo 3.1 - Lite", gemini_request
        )

        self.assertEqual(openai_normalized.model, "veo_3_1_i2v_lite_portrait")
        self.assertEqual(gemini_normalized.model, openai_normalized.model)

    async def test_check_video_status_uses_media_payload_and_normalizes_response(self):
        captured = {}

        async def fake_make_request(method, url, json_data, use_at, at_token, **kwargs):
            captured["json_data"] = json_data
            return {
                "media": [
                    {
                        "name": "media-1",
                        "projectId": "project-1",
                        "mediaMetadata": {
                            "mediaStatus": {
                                "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_SUCCESSFUL"
                            }
                        },
                        "video": {
                            "fifeUrl": "https://flow-content.google/video/11111111-1111-1111-1111-111111111111?token=abc",
                            "generatedVideo": {
                                "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE"
                            },
                        },
                    }
                ]
            }

        self.client._make_request = AsyncMock(side_effect=fake_make_request)

        result = await self.client.check_video_status(
            at="at-token",
            operations=[
                {
                    "operation": {"name": "media-1"},
                    "projectId": "project-1",
                }
            ],
        )

        self.assertEqual(
            captured["json_data"],
            {"media": [{"name": "media-1", "projectId": "project-1"}]},
        )
        operation = result["operations"][0]
        self.assertEqual(operation["operation"]["name"], "media-1")
        self.assertEqual(operation["status"], "MEDIA_GENERATION_STATUS_SUCCESSFUL")
        self.assertEqual(
            operation["operation"]["metadata"]["video"]["fifeUrl"],
            "https://flow-content.google/video/11111111-1111-1111-1111-111111111111?token=abc",
        )

    async def test_generate_video_start_end_uses_v2_payload_for_interpolation_lite(self):
        captured = {}

        async def fake_make_request(method, url, json_data, use_at, at_token, **kwargs):
            captured["url"] = url
            captured["json_data"] = json_data
            return {"operations": [{"operation": {"name": "task-2"}}]}

        self.client._make_request = AsyncMock(side_effect=fake_make_request)

        await self.client.generate_video_start_end(
            at="at-token",
            project_id="project-1",
            prompt="变身猫猫",
            model_key="veo_3_1_interpolation_lite",
            aspect_ratio="VIDEO_ASPECT_RATIO_PORTRAIT",
            start_media_id="start-media",
            end_media_id="end-media",
            use_v2_model_config=True,
        )

        json_data = captured["json_data"]
        request_data = json_data["requests"][0]
        self.assertTrue(json_data["useV2ModelConfig"])
        self.assertIn("batchId", json_data["mediaGenerationContext"])
        self.assertEqual(request_data["videoModelKey"], "veo_3_1_interpolation_lite")
        self.assertEqual(request_data["startImage"]["mediaId"], "start-media")
        self.assertEqual(request_data["endImage"]["mediaId"], "end-media")
        self.assertEqual(
            request_data["textInput"]["structuredPrompt"]["parts"][0]["text"],
            "变身猫猫",
        )


if __name__ == "__main__":
    unittest.main()
