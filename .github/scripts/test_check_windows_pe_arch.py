#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check_windows_pe_arch.py")
SPEC = importlib.util.spec_from_file_location("check_windows_pe_arch", SCRIPT)
assert SPEC and SPEC.loader
PE_ARCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PE_ARCH)


def write_pe(path: Path, machine: int) -> None:
    header = bytearray(64)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 64)
    path.write_bytes(header + b"PE\0\0" + struct.pack("<H", machine))


class WindowsPeArchTest(unittest.TestCase):
    def test_copy_matching_pe_files_skips_foreign_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "crt"
            destination = root / "dist"
            source.mkdir()
            write_pe(source / "vcruntime140.dll", 0xAA64)
            write_pe(source / "vcruntime140_1.dll", 0x8664)

            copied, skipped = PE_ARCH.copy_matching_pe_files(source, destination, "arm64")

            self.assertEqual([path.name for path in copied], ["vcruntime140.dll"])
            self.assertEqual([(path.name, machine) for path, machine in skipped],
                             [("vcruntime140_1.dll", "x64")])
            self.assertTrue((destination / "vcruntime140.dll").is_file())
            self.assertFalse((destination / "vcruntime140_1.dll").exists())

    def test_copy_matching_pe_files_rejects_invalid_dll(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "crt"
            source.mkdir()
            (source / "broken.dll").write_bytes(b"not a PE")

            with self.assertRaisesRegex(ValueError, "broken.dll"):
                PE_ARCH.copy_matching_pe_files(source, root / "dist", "arm64")


if __name__ == "__main__":
    unittest.main()
