import base64
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import routes
from src.core.auth import verify_api_key_flexible


class GeminiOnlyRoutesTests(unittest.TestCase):
    public_models = {
        "Imagen 4", "Nano Banana 2", "Nano Banana Pro", "Omni 1.1 Flash",
        "Veo 3.1 - Fast", "Veo 3.1 - Lite", "Veo 3.1 - Quality",
    }

    def setUp(self):
        self.previous_handler = routes.generation_handler
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[verify_api_key_flexible] = lambda: "test"
        self.client = TestClient(app)

    def tearDown(self):
        routes.generation_handler = self.previous_handler
        self.client.close()

    def test_only_gemini_generation_routes_remain(self):
        for path in ("/v1/chat/completions", "/v1/images/generations", "/v1/videos",
                     "/v1/models/internal", "/v1/models/aliases"):
            with self.subTest(path=path):
                response = self.client.post(path, json={}) if "models" not in path else self.client.get(path)
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/models").status_code, 200)

    def test_agent_model_discovery_returns_seven_public_ids(self):
        response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["object"], "list")
        self.assertEqual(len(payload["data"]), 7)
        self.assertEqual({model["id"] for model in payload["data"]}, self.public_models)
        for model in payload["data"]:
            self.assertEqual(model["object"], "model")
            self.assertEqual(model["owned_by"], "flow2api")
            self.assertTrue(model["description"])

    def test_gemini_discovery_keeps_native_response_format(self):
        for path in ("/models", "/v1beta/models"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                models = response.json()["models"]
                self.assertEqual(len(models), 7)
                self.assertEqual({model["name"] for model in models},
                                 {f"models/{name}" for name in self.public_models})
                veo = next(model for model in models if model["name"] == "models/Veo 3.1 - Fast")
                self.assertIn("predictLongRunning", veo["supportedGenerationMethods"])

    def test_agent_model_discovery_preserves_api_key_authentication(self):
        self.client.app.dependency_overrides.clear()
        with patch("src.core.auth.AuthManager.verify_api_key", side_effect=lambda value: value == "test-key"):
            self.assertEqual(self.client.get("/v1/models").status_code, 401)
            self.assertEqual(self.client.get("/v1/models", headers={"Authorization": "Bearer wrong"}).status_code, 401)
            for kwargs in (
                {"headers": {"Authorization": "Bearer test-key"}},
                {"headers": {"x-goog-api-key": "test-key"}},
                {"params": {"key": "test-key"}},
            ):
                with self.subTest(kwargs=kwargs):
                    response = self.client.get("/v1/models", **kwargs)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(len(response.json()["data"]), 7)

    def test_gemini_image_returns_inline_data(self):
        async def generate(**kwargs):
            yield '{"choices":[{"message":{"content":"![image](https://example.test/image.jpg)"}}]}'

        routes.generation_handler = SimpleNamespace(handle_generation=generate)
        original = routes.retrieve_image_data

        async def load_image(url):
            self.assertEqual(url, "https://example.test/image.jpg")
            return b"\xff\xd8\xffimage"

        routes.retrieve_image_data = load_image
        try:
            response = self.client.post("/models/Nano Banana 2:generateContent", json={
                "contents": [{"role": "user", "parts": [{"text": "a red apple"}]}],
                "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "1:1"}},
            })
        finally:
            routes.retrieve_image_data = original
        self.assertEqual(response.status_code, 200)
        part = response.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
        self.assertEqual(base64.b64decode(part["data"]), b"\xff\xd8\xffimage")

    def test_gemini_video_returns_file_data(self):
        calls = []

        async def generate(**kwargs):
            calls.append(kwargs)
            yield '{"choices":[{"message":{"content":"<video src=\'https://example.test/video.mp4\' controls></video>"}}]}'

        routes.generation_handler = SimpleNamespace(handle_generation=generate)
        response = self.client.post("/models/Veo 3.1 - Fast:generateContent", json={
            "contents": [{"role": "user", "parts": [{"text": "a cat runs"}]}],
            "generationConfig": {"aspectRatio": "16:9", "durationSeconds": 8},
        })
        self.assertEqual(response.status_code, 200)
        part = response.json()["candidates"][0]["content"]["parts"][0]["fileData"]
        self.assertEqual(part["fileUri"], "https://example.test/video.mp4")
        self.assertEqual(part["mimeType"], "video/mp4")
        self.assertEqual(calls[0]["model"], "veo_3_1_t2v_fast_landscape")

    def test_gemini_video_extend_forwards_source_media_id(self):
        calls = []

        async def generate(**kwargs):
            calls.append(kwargs)
            yield '{"choices":[{"message":{"content":"<video src=\'https://example.test/extended.mp4\' controls></video>"}}]}'

        routes.generation_handler = SimpleNamespace(handle_generation=generate)
        response = self.client.post("/models/veo-extend:generateContent", json={
            "contents": [{"role": "user", "parts": [
                {"text": "Continue this clip"},
                {"fileData": {"mimeType": "video/mp4", "fileUri": "extend://media-id-123"}},
            ]}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls[0]["video_media_id"], "media-id-123")


if __name__ == "__main__":
    unittest.main()
