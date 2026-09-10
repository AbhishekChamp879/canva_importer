from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from canva_converter.models import CapturedPage
from canva_converter.errors import ServiceError
from canva_converter.store import ArtifactStore


class ArtifactStoreTests(unittest.TestCase):
    def test_capture_images_are_stored_as_files_not_base64_json(self):
        image = Image.new("RGB", (32, 16), "purple")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
        page = CapturedPage(
            id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", index=0, width=32, height=16,
            screenshotBase64=base64.b64encode(image_bytes).decode("ascii"),
        )
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory), ttl_seconds=3600)
            record = store.put_capture("https://www.canva.com/design/ABC/token/view", "Stored design", [page])
            capture_directory = Path(directory) / "captures" / record.id
            metadata = json.loads((capture_directory / "metadata.json").read_text(encoding="utf-8"))

            self.assertNotIn("screenshotBase64", metadata["pages"][0])
            self.assertEqual((capture_directory / metadata["pages"][0]["_imageFile"]).read_bytes(), image_bytes)

            store._captures.clear()
            restored = store.get_capture(record.id)
            self.assertEqual(base64.b64decode(restored.pages[0].screenshot_base64), image_bytes)

            # Previously saved captures may include layer hints. They should
            # remain usable for page import after removing the hint models.
            metadata["pages"][0]["textHints"] = [{"text": "Old hint"}]
            metadata["pages"][0]["imageHints"] = [{"src": "https://media.canva.com/old.png"}]
            (capture_directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            reloaded = ArtifactStore(Path(directory)).get_capture(record.id)
            self.assertEqual(base64.b64decode(reloaded.pages[0].screenshot_base64), image_bytes)
            self.assertNotIn("textHints", reloaded.pages[0].json_dict())
            self.assertNotIn("imageHints", reloaded.pages[0].json_dict())

    def test_total_capture_byte_limit_is_enforced_before_persistence(self):
        image = Image.new("RGB", (32, 16), "orange")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        page = CapturedPage(
            id="cccccccc-cccc-4ccc-8ccc-cccccccccccc", index=0, width=32, height=16,
            screenshotBase64=base64.b64encode(buffer.getvalue()).decode("ascii"),
        )
        with tempfile.TemporaryDirectory() as directory:
            store = ArtifactStore(Path(directory), ttl_seconds=3600, max_capture_bytes=10)
            with self.assertRaisesRegex(ServiceError, "design limit"):
                store.put_capture("https://www.canva.com/design/ABC/token/view", "Oversized", [page])
            self.assertEqual(list((Path(directory) / "captures").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
