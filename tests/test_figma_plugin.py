from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "figma-plugin"


class FigmaPluginContractTests(unittest.TestCase):
    def test_manifest_points_to_existing_runtime_files(self):
        manifest = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "canva-to-editable-figma-local")
        self.assertEqual(manifest["main"], "code.js")
        self.assertEqual(manifest["ui"], "ui.html")
        self.assertTrue((PLUGIN / manifest["main"]).is_file())
        self.assertTrue((PLUGIN / manifest["ui"]).is_file())

    def test_manifest_restricts_development_network_to_loopback(self):
        manifest = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["networkAccess"]["allowedDomains"], ["none"])
        self.assertEqual(manifest["networkAccess"]["devAllowedDomains"], ["http://localhost:3000"])

    def test_ui_does_not_use_untrusted_inner_html(self):
        ui = (PLUGIN / "ui.html").read_text(encoding="utf-8")
        self.assertNotIn(".innerHTML", ui)
        self.assertIn('type: "import-design"', ui)
        self.assertIn('type: "begin-page-image-import"', ui)
        self.assertIn('api("/api/capture-jobs"', ui)
        self.assertIn('method: "DELETE"', ui)

    def test_exact_image_import_streams_full_resolution_assets(self):
        ui = (PLUGIN / "ui.html").read_text(encoding="utf-8")
        renderer = (PLUGIN / "code.js").read_text(encoding="utf-8")
        self.assertIn("page.imageUrl", ui)
        self.assertIn("response.arrayBuffer()", ui)
        self.assertIn('type: "append-page-image"', ui)
        self.assertIn('message.type === "append-page-image"', renderer)
        self.assertIn("captured-page-full-resolution", renderer)
        self.assertIn("canva-importer.orientation", renderer)

    def test_renderer_covers_every_ir_node_type(self):
        renderer = (PLUGIN / "code.js").read_text(encoding="utf-8")
        for node_type in ("group", "text", "image", "rectangle", "ellipse", "vector", "raster-fallback"):
            self.assertIn(f'irNode.type === "{node_type}"', renderer)
        self.assertIn("canva-importer.canva-id", renderer)
        self.assertIn("QA Reference · hidden", renderer)

    def test_editable_import_exports_and_submits_final_figma_qa(self):
        ui = (PLUGIN / "ui.html").read_text(encoding="utf-8")
        renderer = (PLUGIN / "code.js").read_text(encoding="utf-8")
        self.assertIn("frame.exportAsync", renderer)
        self.assertIn('type: "figma-qa-export"', renderer)
        self.assertIn('message.type === "figma-qa-export"', ui)
        self.assertIn("/figma-qa", ui)
        self.assertIn("QA job ID:", ui)


if __name__ == "__main__":
    unittest.main()
