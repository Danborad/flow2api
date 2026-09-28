import tempfile
import sqlite3
import base64
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import routes
from src.core.auth import verify_api_key_flexible
from src.core.database import Database
from src.core.models import Token
from src.services.generation_handler import GenerationHandler


class GeminiVideoApiTests(unittest.TestCase):
    def setUp(self):
        self.previous = routes.generation_handler
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[verify_api_key_flexible] = lambda: "test"
        self.client = TestClient(app)

    def tearDown(self):
        routes.generation_handler = self.previous
        self.client.close()

    def test_submit_returns_pollable_operation_without_waiting(self):
        routes.generation_handler = SimpleNamespace(submit_gemini_video=AsyncMock(return_value="operations/op-123"))
        response = self.client.post("/v1beta/models/Veo%203.1%20-%20Fast:predictLongRunning", json={
            "instances": [{"prompt": "a cat runs"}],
            "parameters": {"aspectRatio": "16:9"},
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"name": "operations/op-123", "done": False})
        call = routes.generation_handler.submit_gemini_video.await_args.kwargs
        self.assertEqual(call["model"], "veo_3_1_t2v_fast_landscape")
        self.assertEqual(call["prompt"], "a cat runs")

    def test_public_veo_duration_maps_to_matching_real_model(self):
        expected = {
            ("Veo 3.1 - Quality", 4): "veo_3_1_t2v_4s",
            ("Veo 3.1 - Quality", 6): "veo_3_1_t2v_6s",
            ("Veo 3.1 - Quality", 8): "veo_3_1_t2v_8s",
            ("Veo 3.1 - Fast", 4): "veo_3_1_t2v_fast_4s",
            ("Veo 3.1 - Fast", 6): "veo_3_1_t2v_fast_6s",
            ("Veo 3.1 - Fast", 8): "veo_3_1_t2v_fast_8s",
            ("Veo 3.1 - Lite", 4): "veo_3_1_t2v_lite_4s_landscape",
            ("Veo 3.1 - Lite", 6): "veo_3_1_t2v_lite_6s_landscape",
            ("Veo 3.1 - Lite", 8): "veo_3_1_t2v_lite_8s_landscape",
        }
        for (model, seconds), internal_model in expected.items():
            with self.subTest(model=model, seconds=seconds):
                self.handler = SimpleNamespace(submit_gemini_video=AsyncMock(return_value="operations/op-123"))
                routes.generation_handler = self.handler
                response = self.client.post(f"/v1beta/models/{model}:predictLongRunning", json={
                    "instances": [{"prompt": "a cat runs"}],
                    "parameters": {"aspectRatio": "16:9", "durationSeconds": seconds},
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.handler.submit_gemini_video.await_args.kwargs["model"], internal_model)

    def test_poll_returns_gemini_video_response(self):
        routes.generation_handler = SimpleNamespace(get_gemini_video_operation=AsyncMock(return_value={
            "name": "operations/op-123", "done": True,
            "response": {"generateVideoResponse": {"generatedSamples": [{
                "video": {"uri": "https://example.test/video.mp4"},
            }]}},
        }))
        response = self.client.get("/v1beta/operations/op-123")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["response"]["generateVideoResponse"]["generatedSamples"][0]["video"]["uri"],
                         "https://example.test/video.mp4")

    def test_poll_completion_exposes_direct_uri_for_declarative_agent(self):
        routes.generation_handler = SimpleNamespace(get_gemini_video_operation=AsyncMock(return_value={
            "name": "operations/op-123", "done": True,
            "response": {"generateVideoResponse": {"generatedSamples": [{
                "video": {"uri": "https://example.test/video.mp4"},
            }]}},
        }))
        response = self.client.get("/v1beta/operations/op-123")
        sample = response.json()["response"]["generateVideoResponse"]["generatedSamples"][0]
        self.assertEqual(sample["uri"], "https://example.test/video.mp4")
        self.assertEqual(sample["video"]["uri"], "https://example.test/video.mp4")

    def test_model_scoped_operation_can_be_polled(self):
        routes.generation_handler = SimpleNamespace(get_gemini_video_operation=AsyncMock(return_value={
            "name": "operations/op-123", "done": False,
        }))
        response = self.client.get("/v1beta/models/Veo%203.1%20-%20Fast/operations/op-123")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"name": "operations/op-123", "done": False})

    def test_poll_returns_absolute_uri_for_cached_video(self):
        routes.generation_handler = SimpleNamespace(get_gemini_video_operation=AsyncMock(return_value={
            "name": "operations/op-123", "done": True,
            "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "/tmp/video.mp4"}}]}},
        }))
        response = self.client.get("/v1beta/operations/op-123")
        self.assertEqual(response.json()["response"]["generateVideoResponse"]["generatedSamples"][0]["video"]["uri"],
                         "http://testserver/tmp/video.mp4")

    def test_image_submission_routes_to_first_frame_generation(self):
        routes.generation_handler = SimpleNamespace(submit_gemini_video=AsyncMock(return_value="operations/op-123"))
        response = self.client.post("/v1beta/models/Veo%203.1%20-%20Fast:predictLongRunning", json={
            "instances": [{"prompt": "animate", "image": {
                "bytesBase64Encoded": base64.b64encode(b"image").decode(), "mimeType": "image/png",
            }}],
        })
        self.assertEqual(response.status_code, 200)
        kwargs = routes.generation_handler.submit_gemini_video.await_args.kwargs
        self.assertEqual(kwargs["images"], [b"image"])
        self.assertEqual(kwargs["model"], "veo_3_1_i2v_s_fast_fl")

    def test_agent_nested_inline_data_image_is_accepted(self):
        routes.generation_handler = SimpleNamespace(submit_gemini_video=AsyncMock(return_value="operations/op-123"))
        image = base64.b64encode(b"agent-image").decode()
        response = self.client.post("/v1beta/models/Veo%203.1%20-%20Quality:predictLongRunning", json={
            "instances": [{"prompt": "animate", "image": {
                "inlineData": {"mimeType": "image/png", "data": image},
            }}],
            "parameters": {"durationSeconds": 4, "aspectRatio": "16:9"},
        })
        self.assertEqual(response.status_code, 200, response.text)
        kwargs = routes.generation_handler.submit_gemini_video.await_args.kwargs
        self.assertEqual(kwargs["images"], [b"agent-image"])
        self.assertEqual(kwargs["model"], "veo_3_1_i2v_s_4s")

    def test_agent_first_and_last_frames_are_both_submitted(self):
        routes.generation_handler = SimpleNamespace(submit_gemini_video=AsyncMock(return_value="operations/op-123"))
        response = self.client.post("/v1beta/models/Veo%203.1%20-%20Quality:predictLongRunning", json={
            "instances": [{
                "prompt": "transition between frames",
                "image": {"inlineData": {
                    "mimeType": "image/png", "data": base64.b64encode(b"first-frame").decode(),
                }},
                "lastFrame": {"inlineData": {
                    "mimeType": "image/png", "data": base64.b64encode(b"last-frame").decode(),
                }},
            }],
            "parameters": {"durationSeconds": 4, "aspectRatio": "16:9"},
        })
        self.assertEqual(response.status_code, 200, response.text)
        kwargs = routes.generation_handler.submit_gemini_video.await_args.kwargs
        self.assertEqual(kwargs["images"], [b"first-frame", b"last-frame"])
        self.assertEqual(kwargs["model"], "veo_3_1_i2v_s_4s")

    def test_agent_file_uri_image_is_downloaded_and_accepted(self):
        routes.generation_handler = SimpleNamespace(submit_gemini_video=AsyncMock(return_value="operations/op-123"))
        with patch("src.api.routes._load_image_bytes_from_uri", new=AsyncMock(return_value=b"remote-image")) as loader:
            response = self.client.post("/v1beta/models/Veo%203.1%20-%20Fast:predictLongRunning", json={
                "instances": [{"prompt": "animate", "image": {
                    "fileUri": "https://example.test/frame.png", "mimeType": "image/png",
                }}],
                "parameters": {"durationSeconds": 6},
            })
        self.assertEqual(response.status_code, 200, response.text)
        loader.assert_awaited_once_with("https://example.test/frame.png")
        self.assertEqual(routes.generation_handler.submit_gemini_video.await_args.kwargs["images"], [b"remote-image"])
        self.assertEqual(routes.generation_handler.submit_gemini_video.await_args.kwargs["model"],
                         "veo_3_1_i2v_s_fast_6s_fl")

    def test_unknown_operation_returns_gemini_not_found(self):
        routes.generation_handler = SimpleNamespace(get_gemini_video_operation=AsyncMock(return_value=None))
        response = self.client.get("/v1beta/operations/unknown")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["status"], "NOT_FOUND")

    def test_public_video_models_advertise_long_running_method(self):
        response = self.client.get("/v1beta/models")
        veo = next(m for m in response.json()["models"] if m["name"] == "models/Veo 3.1 - Fast")
        self.assertIn("predictLongRunning", veo["supportedGenerationMethods"])

    def test_unsupported_internal_modes_are_not_advertised(self):
        response = self.client.get("/models/internal")
        model = next(m for m in response.json()["models"] if m["name"] == "models/veo_3_1_extend")
        self.assertNotIn("predictLongRunning", model["supportedGenerationMethods"])

    def test_standard_google_veo_name_submits(self):
        routes.generation_handler = SimpleNamespace(submit_gemini_video=AsyncMock(return_value="operations/op-123"))
        response = self.client.post("/v1beta/models/veo-3.1-fast-generate-preview:predictLongRunning", json={
            "instances": [{"prompt": "a cat runs"}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(routes.generation_handler.submit_gemini_video.await_args.kwargs["model"],
                         "veo_3_1_t2v_fast_landscape")

    def test_submission_failure_keeps_upstream_exception_in_service_log(self):
        routes.generation_handler = SimpleNamespace(
            submit_gemini_video=AsyncMock(side_effect=RuntimeError("upstream model rejected image")),
        )
        with self.assertLogs("src.api.routes", level="ERROR") as captured:
            response = self.client.post("/v1beta/models/Veo%203.1%20-%20Quality:predictLongRunning", json={
                "instances": [{"prompt": "animate"}],
            })

        self.assertEqual(response.status_code, 502)
        self.assertTrue(any("upstream model rejected image" in line for line in captured.output))
        self.assertIn("upstream model rejected image", response.json()["error"]["message"])

    def test_model_access_denied_is_reported_as_permission_error(self):
        routes.generation_handler = SimpleNamespace(
            submit_gemini_video=AsyncMock(side_effect=RuntimeError(
                "Flow API request failed: PUBLIC_ERROR_MODEL_ACCESS_DENIED: The caller does not have permission"
            )),
        )
        response = self.client.post("/v1beta/models/Veo%203.1%20-%20Quality:predictLongRunning", json={
            "instances": [{"prompt": "animate"}],
        })
        self.assertEqual(response.status_code, 403)
        self.assertIn("Selected Flow account cannot access this video model", response.json()["error"]["message"])

    def test_explicit_model_rejects_conflicting_aspect_ratio(self):
        routes.generation_handler = SimpleNamespace(submit_gemini_video=AsyncMock())
        response = self.client.post("/v1beta/models/veo_3_1_t2v_fast_landscape:predictLongRunning", json={
            "instances": [{"prompt": "a cat runs"}], "parameters": {"aspectRatio": "9:16"},
        })
        self.assertEqual(response.status_code, 400)
        routes.generation_handler.submit_gemini_video.assert_not_awaited()

    def test_invalid_models_or_inputs_do_not_submit(self):
        routes.generation_handler = SimpleNamespace(submit_gemini_video=AsyncMock())
        for model, body in (
            ("Nano Banana 2", {"instances": [{"prompt": "cat"}]}),
            ("Veo 3.1 - Fast", {"instances": [{"prompt": " "}]}),
            ("Veo 3.1 - Fast", {"instances": [{"prompt": "cat"}], "parameters": {"durationSeconds": 10}}),
            ("Veo 3.1 - Fast", {"instances": [{"prompt": "cat"}], "parameters": {"aspectRatio": "1:1"}}),
            ("Veo 3.1 - Fast", {"instances": [{"prompt": "cat"}], "parameters": {"sampleCount": 2}}),
        ):
            with self.subTest(model=model, body=body):
                response = self.client.post(f"/v1beta/models/{model}:predictLongRunning", json=body)
                self.assertEqual(response.status_code, 400)
        routes.generation_handler.submit_gemini_video.assert_not_awaited()

    def test_polling_requires_api_key(self):
        self.client.app.dependency_overrides.clear()
        self.assertIn(self.client.get("/v1beta/operations/op-123").status_code, (401, 403))
        self.assertIn(self.client.post("/v1beta/models/Veo%203.1%20-%20Fast:predictLongRunning", json={
            "instances": [{"prompt": "cat"}],
        }).status_code, (401, 403))


class GeminiVideoPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp.name) / "flow.db"))
        await self.db.init_db()
        token = Token(st="st", at="at", email="test@example.com", user_paygate_tier="PAYGATE_TIER_ONE")
        token.id = await self.db.add_token(token)
        self.token = token
        self.flow = SimpleNamespace(
            prefill_remote_browser_pool=AsyncMock(),
            upload_image=AsyncMock(side_effect=["first-media", "last-media"]),
            generate_video_text=AsyncMock(return_value={"operations": [{
                "operation": {"name": "op-123"}, "mediaName": "media-123", "projectId": "project-1",
            }]}),
            generate_video_start_image=AsyncMock(return_value={"operations": [{
                "operation": {"name": "op-123"}, "mediaName": "media-123", "projectId": "project-1",
            }]}),
            generate_video_start_end=AsyncMock(return_value={"operations": [{
                "operation": {"name": "op-123"}, "mediaName": "media-123", "projectId": "project-1",
            }]}),
            check_video_status=AsyncMock(return_value={"operations": [{
                "operation": {"name": "op-123"}, "mediaName": "media-123", "projectId": "project-1",
                "status": "MEDIA_GENERATION_STATUS_ACTIVE",
            }]}),
            get_media_url_redirect=AsyncMock(return_value="https://example.test/video.mp4"),
        )
        self.manager = SimpleNamespace(
            ensure_valid_token=AsyncMock(return_value=token),
            ensure_project_exists=AsyncMock(return_value="project-1"),
            record_usage=AsyncMock(), record_success=AsyncMock(),
        )
        self.balancer = SimpleNamespace(select_token=AsyncMock(return_value=token), release_pending=AsyncMock())
        self.handler = GenerationHandler(self.flow, self.manager, self.balancer, self.db, None, None)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_operation_survives_new_handler_and_polls_upstream(self):
        name = await self.handler.submit_gemini_video(model="veo_3_1_t2v_fast_landscape", prompt="a cat", images=[])
        self.assertEqual(name, "operations/op-123")
        self.assertEqual(self.flow.check_video_status.await_count, 0)
        restarted = GenerationHandler(self.flow, self.manager, self.balancer, self.db, None, None)
        pending = await restarted.get_gemini_video_operation(name)
        self.assertEqual(pending, {"name": name, "done": False})
        self.flow.check_video_status.return_value["operations"][0]["status"] = "MEDIA_GENERATION_STATUS_SUCCESSFUL"
        finished = await restarted.get_gemini_video_operation(name)
        self.assertTrue(finished["done"])
        self.assertEqual(finished["response"]["generateVideoResponse"]["generatedSamples"][0]["video"]["uri"],
                         "https://example.test/video.mp4")
        self.manager.record_usage.assert_awaited_once_with(self.token.id, is_video=True)

    async def test_two_images_submit_start_end_generation(self):
        name = await self.handler.submit_gemini_video(
            model="veo_3_1_i2v_s_4s", prompt="transition", images=[b"first", b"last"],
        )
        self.assertEqual(name, "operations/op-123")
        self.assertEqual(self.flow.upload_image.await_count, 2)
        self.flow.generate_video_start_end.assert_awaited_once()
        kwargs = self.flow.generate_video_start_end.await_args.kwargs
        self.assertEqual(kwargs["model_key"], "veo_3_1_i2v_s_quality_4s_fl")
        self.assertEqual(kwargs["start_media_id"], "first-media")
        self.assertEqual(kwargs["end_media_id"], "last-media")
        self.flow.generate_video_start_image.assert_not_awaited()

    async def test_old_database_task_schema_migrates(self):
        path = Path(self.temp.name) / "legacy.db"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, task_id TEXT UNIQUE NOT NULL, token_id INTEGER NOT NULL, model TEXT NOT NULL, prompt TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER, result_urls TEXT, error_message TEXT, scene_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP)")
        migrated = Database(str(path))
        await migrated.init_db()
        await migrated.check_and_migrate_db()
        with sqlite3.connect(path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
        self.assertTrue({"project_id", "media_name"}.issubset(columns))

    async def test_failed_upstream_operation_stays_failed_across_polls(self):
        name = await self.handler.submit_gemini_video(model="veo_3_1_t2v_fast_landscape", prompt="a cat", images=[])
        self.flow.check_video_status.return_value["operations"][0].update({
            "status": "MEDIA_GENERATION_STATUS_FAILED", "operation": {"name": "op-123", "error": {"message": "rejected"}},
        })
        failed = await self.handler.get_gemini_video_operation(name)
        self.assertEqual(failed["error"]["message"], "rejected")
        self.assertTrue(failed["done"])
        await self.handler.get_gemini_video_operation(name)
        self.assertEqual(self.flow.check_video_status.await_count, 1)

    async def test_finished_video_with_no_url_can_be_polled_again(self):
        name = await self.handler.submit_gemini_video(model="veo_3_1_t2v_fast_landscape", prompt="cat", images=[])
        operation = self.flow.check_video_status.return_value["operations"][0]
        operation["status"] = "MEDIA_GENERATION_STATUS_SUCCESSFUL"
        self.flow.get_media_url_redirect.return_value = None
        self.flow.get_media = AsyncMock(return_value={"video": {"encodedVideo": ""}})
        self.assertEqual(await self.handler.get_gemini_video_operation(name), {"name": name, "done": False})
        self.flow.get_media_url_redirect.return_value = "https://example.test/video.mp4"
        self.assertTrue((await self.handler.get_gemini_video_operation(name))["done"])

    async def test_completed_cached_video_can_recover_after_cache_cleanup(self):
        name = await self.handler.submit_gemini_video(model="veo_3_1_t2v_fast_landscape", prompt="cat", images=[])
        self.flow.check_video_status.return_value["operations"][0]["status"] = "MEDIA_GENERATION_STATUS_SUCCESSFUL"
        self.flow.get_media_url_redirect.return_value = None
        self.flow.get_media = AsyncMock(return_value={"video": {"encodedVideo": base64.b64encode(b"mp4").decode()}})
        self.handler.file_cache.cache_dir = Path(self.temp.name)
        finished = await self.handler.get_gemini_video_operation(name)
        self.assertTrue(finished["done"])
        cached = self.handler.file_cache.cache_dir / finished["response"]["generateVideoResponse"]["generatedSamples"][0]["video"]["uri"].split("/")[-1]
        cached.unlink()
        refreshed = await self.handler.get_gemini_video_operation(name)
        self.assertTrue(refreshed["done"])
        self.assertNotEqual(finished, refreshed)


if __name__ == "__main__":
    unittest.main()
