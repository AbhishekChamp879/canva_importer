from __future__ import annotations

import unittest
from urllib.parse import urlparse

from canva_converter.acquisition.url_policy import (
    is_allowed_redirect,
    is_canva_asset_host,
    is_valid_canva_url,
    is_valid_resolved_canva_url,
    normalize_canva_view_url,
    resolve_canva_source_url,
)


class UrlPolicyTests(unittest.TestCase):
    def test_allows_canva_subdomains_only_for_image_assets(self):
        self.assertTrue(is_canva_asset_host(urlparse("https://static.canva.com/image.png")))
        self.assertFalse(is_canva_asset_host(urlparse("https://canva.com.attacker.example/image.png")))

    def test_accepts_and_normalizes_common_canva_design_links(self):
        cases = {
            "https://www.canva.com/design/ABC/token/view": "https://www.canva.com/design/ABC/token/view",
            "https://www.canva.com/design/ABC/token/edit?utm_source=share": "https://www.canva.com/design/ABC/token/view",
            "https://www.canva.com/design/ABC/token/present#slide-2": "https://www.canva.com/design/ABC/token/view",
            "https://www.canva.com/design/ABC/token/watch": "https://www.canva.com/design/ABC/token/view",
            "https://www.canva.com/design/ABC/edit": "https://www.canva.com/design/ABC/view",
            "https://www.canva.com/en_in/design/ABC/token/edit": "https://www.canva.com/design/ABC/token/view",
            "https://www.canva.com/design/ABC/token/future-route": "https://www.canva.com/design/ABC/token/view",
            "https://canva.com/design/ABC/token": "https://www.canva.com/design/ABC/token/view",
            "https://www.canva.com/design/ABC": "https://www.canva.com/design/ABC/view",
            "https://www.canva.com/design/DESIGN123/ShareToken456/edit": "https://www.canva.com/design/DESIGN123/ShareToken456/view",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertTrue(is_valid_canva_url(value))
                self.assertEqual(normalize_canva_view_url(value), expected)
        self.assertTrue(is_valid_canva_url("https://canva.link/abc_123"))

    def test_rejects_untrusted_hosts_and_non_design_routes(self):
        self.assertFalse(is_valid_canva_url("https://evil.example/design/ABC/token/view"))
        self.assertFalse(is_valid_canva_url("https://canva.com.attacker.example/design/ABC/token/edit"))
        self.assertFalse(is_valid_canva_url("https://canva.link:444/abc"))
        self.assertFalse(is_valid_canva_url("https://user@canva.link/abc"))
        self.assertFalse(is_valid_canva_url("https://www.canva.com/templates/ABC"))
        self.assertFalse(is_valid_canva_url("https://www.canva.com/design/ABC/token/view/extra"))
        self.assertFalse(is_valid_canva_url("https://www.canva.com/design//ABC/edit"))
        self.assertFalse(is_valid_canva_url("https://www.canva.com/design/ABC%2Ftoken/edit"))
        self.assertFalse(is_allowed_redirect("http://www.canva.com/design/ABC/token/view"))

    def test_rejects_ambiguous_or_resource_exhausting_inputs(self):
        invalid = [
            "https://www.canva.com/design/ABC/token/view\\evil",
            "https://www.canva.com/design/ABC%5Ctoken/edit",
            "https://www.canva.com/design/ABC%00/token/edit",
            "https://www.canva.com/design/" + "A" * 257 + "/view",
            "https://canva.link/" + "A" * 257,
            "https://www.canva.com/design/ABC/token/view\nhttps://evil.example",
            "https://www.canva.com.:443/design/ABC/token/view",
        ]
        for value in invalid:
            with self.subTest(value=value[:120]):
                self.assertFalse(is_valid_canva_url(value))

    def test_canonicalization_removes_tracking_locale_mode_and_host_aliases(self):
        variants = [
            " https://canva.com/design/ABC/Token/edit?utm_source=share ",
            "https://www.canva.com/en-US/design/ABC/Token/present#page=4",
            "https://www.canva.com/design/ABC/Token/view",
        ]
        canonical = {normalize_canva_view_url(value) for value in variants}
        self.assertEqual(canonical, {"https://www.canva.com/design/ABC/Token/view"})

    def test_redirect_budget_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "max_redirects"):
            resolve_canva_source_url("https://www.canva.com/design/ABC/token/view", max_redirects=11)

    def test_normalizes_trusted_resolved_edit_url(self):
        value = "https://www.canva.com/design/ABC/token/edit?utm_source=share"
        self.assertTrue(is_valid_resolved_canva_url(value))
        self.assertEqual(normalize_canva_view_url(value), "https://www.canva.com/design/ABC/token/view")


if __name__ == "__main__":
    unittest.main()
