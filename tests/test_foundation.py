from __future__ import annotations

import os
import unittest

from canva_converter.config import _env_bool, _env_int
from canva_converter.errors import public_error_message


class FoundationContractTests(unittest.TestCase):
    def test_integer_environment_contract_rejects_invalid_and_out_of_range_values(self):
        name = "CANVA_IMPORTER_TEST_INTEGER"
        original = os.environ.get(name)
        try:
            os.environ[name] = "not-an-integer"
            with self.assertRaisesRegex(RuntimeError, "must be an integer"):
                _env_int(name, 3, minimum=1, maximum=8)
            os.environ[name] = "9"
            with self.assertRaisesRegex(RuntimeError, "between 1 and 8"):
                _env_int(name, 3, minimum=1, maximum=8)
        finally:
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original

    def test_boolean_environment_contract_is_explicit(self):
        name = "CANVA_IMPORTER_TEST_BOOLEAN"
        original = os.environ.get(name)
        try:
            os.environ[name] = "sometimes"
            with self.assertRaisesRegex(RuntimeError, "must be one of"):
                _env_bool(name, True)
        finally:
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original

    def test_public_errors_are_bounded_and_redact_credentials(self):
        raw = (
            "Provider failed https://example.test/api?key=secret&safe=value "
            "Authorization: Bearer-secret "
            "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
        )
        message = public_error_message(raw, limit=160)
        self.assertLessEqual(len(message), 160)
        self.assertNotIn("secret", message)
        self.assertNotIn("sk-proj-", message)
        self.assertIn("safe=value", message)

    def test_public_errors_redact_canva_share_tokens(self):
        message = public_error_message(
            "Failed at https://www.canva.com/design/DESIGN/PrivateShareToken/view and https://canva.link/PrivateShortCode"
        )
        self.assertNotIn("PrivateShareToken", message)
        self.assertNotIn("PrivateShortCode", message)


if __name__ == "__main__":
    unittest.main()
