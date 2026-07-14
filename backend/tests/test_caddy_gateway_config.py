from __future__ import annotations

import unittest
from pathlib import Path

from scripts.check_caddy_mount import (
    PORTAL_GATEWAY_HEADER_DELETE,
    PORTAL_GATEWAY_HEADER_SET,
    _portal_gateway_header_error,
)


class CaddyGatewayConfigTests(unittest.TestCase):
    def test_repository_caddyfile_uses_safe_gateway_header_config(self) -> None:
        caddyfile = Path(__file__).resolve().parents[2] / "Caddyfile"
        self.assertIsNone(_portal_gateway_header_error(caddyfile.read_text(encoding="utf-8")))

    def test_trusted_header_set_is_valid(self) -> None:
        self.assertIsNone(_portal_gateway_header_error(PORTAL_GATEWAY_HEADER_SET))

    def test_delete_and_set_combination_is_rejected(self) -> None:
        config = f"{PORTAL_GATEWAY_HEADER_DELETE}\n{PORTAL_GATEWAY_HEADER_SET}"
        error = _portal_gateway_header_error(config)
        self.assertIsNotNone(error)
        self.assertIn("deletes X-Portal-Gateway-Token", error or "")

    def test_missing_trusted_header_set_is_rejected(self) -> None:
        error = _portal_gateway_header_error("reverse_proxy portal-messages-api:8000")
        self.assertIsNotNone(error)
        self.assertIn("does not inject", error or "")


if __name__ == "__main__":
    unittest.main()
