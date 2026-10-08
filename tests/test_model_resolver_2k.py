"""Unit tests for model resolution with 2k/4k resolutions and Gemini protocol."""
import unittest
from types import SimpleNamespace
from src.core.model_resolver import resolve_model_name, _extract_generation_params
from src.services.generation_handler import MODEL_CONFIG
from src.core.models import (
    GeminiGenerateContentRequest,
    GenerationConfigParam,
    ImageConfig,
    GeminiContent,
    GeminiPart,
)


class ModelResolver2KTests(unittest.TestCase):
    def test_base_model_with_image_size_2k(self):
        req = GeminiGenerateContentRequest(
            contents=[],
            generationConfig=GenerationConfigParam(
                imageConfig=ImageConfig(aspectRatio="16:9", imageSize="2k")
            ),
        )
        resolved = resolve_model_name("gemini-3.0-pro-image", req, MODEL_CONFIG)
        self.assertEqual(resolved, "gemini-3.0-pro-image-landscape-2k")

    def test_base_model_with_image_size_2K_uppercase(self):
        req = GeminiGenerateContentRequest(
            contents=[],
            generationConfig=GenerationConfigParam(
                imageConfig=ImageConfig(aspectRatio="9:16", imageSize="2K")
            ),
        )
        resolved = resolve_model_name("gemini-3.0-pro-image", req, MODEL_CONFIG)
        self.assertEqual(resolved, "gemini-3.0-pro-image-portrait-2k")

    def test_full_aspect_model_upgrades_to_2k_when_image_size_specified(self):
        # 核心回归测试：请求具体方向的模型（如 gemini-3.0-pro-image-portrait）在选了 2k 时必须升级为 -2k
        req = GeminiGenerateContentRequest(
            contents=[],
            generationConfig=GenerationConfigParam(
                imageConfig=ImageConfig(imageSize="2k")
            ),
        )
        resolved = resolve_model_name("gemini-3.0-pro-image-portrait", req, MODEL_CONFIG)
        self.assertEqual(resolved, "gemini-3.0-pro-image-portrait-2k")

        resolved_sq = resolve_model_name("gemini-3.0-pro-image-square", req, MODEL_CONFIG)
        self.assertEqual(resolved_sq, "gemini-3.0-pro-image-square-2k")

    def test_flash_full_aspect_model_upgrades_to_2k(self):
        req = GeminiGenerateContentRequest(
            contents=[],
            generationConfig=GenerationConfigParam(
                imageConfig=ImageConfig(imageSize="2K")
            ),
        )
        resolved = resolve_model_name("gemini-3.1-flash-image-portrait", req, MODEL_CONFIG)
        self.assertEqual(resolved, "gemini-3.1-flash-image-portrait-2k")

    def test_friendly_aliases_with_2k(self):
        req = GeminiGenerateContentRequest(
            contents=[],
            generationConfig=GenerationConfigParam(
                imageConfig=ImageConfig(imageSize="2k")
            ),
        )
        self.assertEqual(resolve_model_name("Nano Banana Pro", req, MODEL_CONFIG), "gemini-3.0-pro-image-landscape-2k")
        self.assertEqual(resolve_model_name("Nano Banana 2.1", req, MODEL_CONFIG), "gemini-3.1-flash-image-landscape-2k")
        self.assertEqual(resolve_model_name("Nano Banana 2 Lite", req, MODEL_CONFIG), "gemini-3.1-flash-lite-image-landscape-2k")
        self.assertEqual(resolve_model_name("Nano Banana 2", req, MODEL_CONFIG), "gemini-3.1-flash-image-landscape-2k")
        self.assertEqual(resolve_model_name("nano-banana-pro", req, MODEL_CONFIG), "gemini-3.0-pro-image-landscape-2k")
        self.assertEqual(resolve_model_name("nano-banana-2.1", req, MODEL_CONFIG), "gemini-3.1-flash-image-landscape-2k")
        self.assertEqual(resolve_model_name("nano-banana-2-lite", req, MODEL_CONFIG), "gemini-3.1-flash-lite-image-landscape-2k")

    def test_model_name_embedded_2k(self):
        # 客户端在模型名称中直接自带 2k
        self.assertEqual(resolve_model_name("gemini-3.0-pro-image-2k", None, MODEL_CONFIG), "gemini-3.0-pro-image-landscape-2k")
        self.assertEqual(resolve_model_name("Nano Banana Pro 2K", None, MODEL_CONFIG), "gemini-3.0-pro-image-landscape-2k")
        self.assertEqual(resolve_model_name("Nano Banana 2.1 2K", None, MODEL_CONFIG), "gemini-3.1-flash-image-landscape-2k")
        self.assertEqual(resolve_model_name("Nano Banana 2 Lite 2K", None, MODEL_CONFIG), "gemini-3.1-flash-lite-image-landscape-2k")
        self.assertEqual(resolve_model_name("Nano Banana 2 2K", None, MODEL_CONFIG), "gemini-3.1-flash-image-landscape-2k")

    def test_snake_case_generation_config(self):
        req = GeminiGenerateContentRequest(
            contents=[],
            generation_config={"image_config": {"image_size": "2k"}}
        )
        self.assertEqual(resolve_model_name("gemini-3.0-pro-image-portrait", req, MODEL_CONFIG), "gemini-3.0-pro-image-portrait-2k")

    def test_pixel_size_2048_maps_to_2k(self):
        req = GeminiGenerateContentRequest(
            contents=[],
            generationConfig=GenerationConfigParam(
                imageConfig=ImageConfig(imageSize="2048x2048")
            ),
        )
        self.assertEqual(resolve_model_name("gemini-3.0-pro-image", req, MODEL_CONFIG), "gemini-3.0-pro-image-landscape-2k")

    def test_query_params_resolution(self):
        raw_req = SimpleNamespace(query_params={"imageSize": "2k"})
        resolved = resolve_model_name("gemini-3.0-pro-image-portrait", None, MODEL_CONFIG, raw_request=raw_req)
        self.assertEqual(resolved, "gemini-3.0-pro-image-portrait-2k")

    def test_normal_1k_remains_unchanged(self):
        self.assertEqual(resolve_model_name("gemini-3.0-pro-image-portrait", None, MODEL_CONFIG), "gemini-3.0-pro-image-portrait")
        self.assertEqual(resolve_model_name("gemini-3.0-pro-image", None, MODEL_CONFIG), "gemini-3.0-pro-image-landscape")


if __name__ == "__main__":
    unittest.main()
