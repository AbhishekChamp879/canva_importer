from __future__ import annotations

import unittest

from scripts.live_validate import expected_page_image_size


class LiveValidatorTests(unittest.TestCase):
    def test_expected_image_size_uses_uniform_scale(self):
        self.assertEqual(expected_page_image_size(800, 600), (1600, 1200))
        self.assertEqual(expected_page_image_size(6000, 3000), (8192, 4096))

    def test_expected_image_size_rejects_invalid_dimensions(self):
        with self.assertRaises(ValueError):
            expected_page_image_size(0, 100)


if __name__ == "__main__":
    unittest.main()
