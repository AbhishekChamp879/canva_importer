from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "figma-plugin"


class FigmaPluginContractTests(unittest.TestCase):
    def test_plugin_has_only_page_image_import(self):
        ui = (PLUGIN / "ui.html").read_text(encoding="utf-8")
        renderer = (PLUGIN / "code.js").read_text(encoding="utf-8")
        self.assertNotIn('id="reconstruct"', ui)
        self.assertNotIn("Make editable", ui)
        self.assertNotIn("reconstruction-jobs", ui)
        self.assertNotIn('"import-design"', renderer)
        self.assertNotIn("createText", renderer)
        self.assertNotIn("figma-qa", renderer + ui)

    def test_manifest_points_to_existing_runtime_files(self):
        manifest = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "canva-to-figma-pages-local")
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

if __name__ == "__main__":
    unittest.main()
