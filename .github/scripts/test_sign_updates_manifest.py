#!/usr/bin/env python3
"""Regression checks for the updater manifest signing helper."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SIGNER = ROOT / ".github" / "scripts" / "sign_updates_manifest.sh"


class SignUpdatesManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SIGNER.read_text(encoding="utf-8")
        match = re.search(r"(?ms)^hex_to_bin\(\) \{.*?^\}", cls.source)
        if not match:
            raise AssertionError("hex_to_bin function not found")
        cls.hex_to_bin = match.group(0)

    def test_hex_to_bin_emits_exact_bytes(self) -> None:
        result = subprocess.run(
            ["bash", "-c", f"{self.hex_to_bin}\nhex_to_bin 302a300506032b6570"],
            check=True,
            capture_output=True,
        )
        self.assertEqual(result.stdout, bytes.fromhex("302a300506032b6570"))

    def test_hex_to_bin_does_not_use_data_as_printf_format(self) -> None:
        self.assertIn("printf '%b' \"$fmt\"", self.hex_to_bin)
        self.assertNotIn('printf "$fmt"', self.hex_to_bin)


if __name__ == "__main__":
    unittest.main()
