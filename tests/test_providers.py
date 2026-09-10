from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from canva_converter.config import Settings
from canva_converter.models import OcrResult
from canva_converter.providers import OpenAILayoutProvider, OpenAIVisionOcrProvider


def settings() -> Settings:
    return Settings(
        host="127.0.0.1",
        port=3000,
        capture_concurrency=1,
        capture_timeout_ms=60_000,
        job_concurrency=1,
        artifact_ttl_seconds=3600,
        max_capture_bytes=512 * 1024 * 1024,
        max_api_response_bytes=64 * 1024 * 1024,
        browser_executable_path=None,
        browser_auto_install=True,
        browser_install_timeout_seconds=900,
        openai_api_key="sk-test-openai-key-0000000000000000",
        openai_model="gpt-test",
        store_root=Path(".jobs"),
        development=True,
    )


class ProviderTests(unittest.TestCase):
    @patch("canva_converter.providers._post_json")
    def test_openai_vision_ocr_uses_image_dimensions_and_strict_schema(self, post_json):
        valid = {
            "blocks": [{"id": "word-1", "text": "Hello", "x": 10, "y": 20, "width": 40, "height": 18, "confidence": 0.97}],
            "fullText": "Hello",
        }
        post_json.return_value = {"status": "completed", "output_text": json.dumps(valid)}

        result = OpenAIVisionOcrProvider(settings()).detect("image", 200, 100)

        self.assertEqual(result.full_text, "Hello")
        self.assertEqual(result.blocks[0].text, "Hello")
        self.assertEqual(result.blocks[0].confidence, 0.97)
        self.assertEqual((result.blocks[0].x, result.blocks[0].y, result.blocks[0].width, result.blocks[0].height), (10, 20, 40, 18))
        self.assertEqual(post_json.call_args.args[0], "https://api.openai.com/v1/responses")
        self.assertEqual(post_json.call_args.kwargs["headers"]["Authorization"], "Bearer sk-test-openai-key-0000000000000000")
        payload = post_json.call_args.args[1]
        self.assertEqual(payload["model"], "gpt-test")
        self.assertFalse(payload["store"])
        self.assertIn("200 pixels wide and 100 pixels high", payload["input"][0]["content"][0]["text"])
        self.assertEqual(payload["input"][0]["content"][1]["detail"], "high")
        self.assertTrue(payload["text"]["format"]["strict"])
        block_schema = payload["text"]["format"]["schema"]["properties"]["blocks"]["items"]
        self.assertEqual(set(block_schema["required"]), set(block_schema["properties"]))

    @patch("canva_converter.providers._post_json")
    def test_openai_vision_ocr_retries_malformed_structured_output_once(self, post_json):
        valid = {"blocks": [], "fullText": ""}
        post_json.side_effect = [
            {"status": "completed", "output_text": "not json"},
            {"status": "completed", "output_text": json.dumps(valid)},
        ]

        result = OpenAIVisionOcrProvider(settings()).detect("image", 100, 100)

        self.assertEqual(result.blocks, [])
        self.assertEqual(post_json.call_count, 2)
        self.assertIn("previous response was invalid", post_json.call_args_list[1].args[1]["input"][0]["content"][0]["text"])

    @patch("canva_converter.providers._post_json")
    def test_openai_retries_malformed_structured_output_once(self, post_json):
        valid = {"backgroundColor": "#ffffff", "elements": []}
        post_json.side_effect = [
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "not json"}]}]},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(valid)}]}]},
        ]

        result = OpenAILayoutProvider(settings()).analyze("image", 100, 100, OcrResult(blocks=[], fullText=""), [], [])

        self.assertEqual(result.background_color, "#ffffff")
        self.assertEqual(post_json.call_count, 2)
        self.assertEqual(post_json.call_args_list[0].args[0], "https://api.openai.com/v1/responses")
        self.assertEqual(post_json.call_args_list[0].kwargs["headers"]["Authorization"], "Bearer sk-test-openai-key-0000000000000000")
        first_payload = post_json.call_args_list[0].args[1]
        self.assertEqual(first_payload["model"], "gpt-test")
        self.assertFalse(first_payload["store"])
        self.assertEqual(first_payload["input"][0]["content"][1]["type"], "input_image")
        self.assertTrue(first_payload["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,"))
        self.assertTrue(first_payload["text"]["format"]["strict"])
        element_schema = first_payload["text"]["format"]["schema"]["properties"]["elements"]["items"]
        self.assertEqual(set(element_schema["required"]), set(element_schema["properties"]))
        second_payload = post_json.call_args_list[1].args[1]
        self.assertIn("previous response was invalid", second_payload["input"][0]["content"][0]["text"])

    @patch("canva_converter.providers._post_json")
    def test_openai_accepts_fenced_json(self, post_json):
        post_json.return_value = {"output_text": "```json\n{\"backgroundColor\":\"#ffffff\",\"elements\":[]}\n```"}

        result = OpenAILayoutProvider(settings()).analyze("image", 100, 100, OcrResult(blocks=[], fullText=""), [], [])

        self.assertEqual(result.elements, [])

    @patch("canva_converter.providers._post_json")
    def test_openai_refusal_is_not_treated_as_layout_json(self, post_json):
        post_json.return_value = {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "Cannot process this image."}]}],
        }

        with self.assertRaisesRegex(RuntimeError, "refused"):
            OpenAILayoutProvider(settings()).analyze("image", 100, 100, OcrResult(blocks=[], fullText=""), [], [])

        self.assertEqual(post_json.call_count, 2)


if __name__ == "__main__":
    unittest.main()
