#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("copy_windows_release_plugins.py")
SPEC = importlib.util.spec_from_file_location("copy_windows_release_plugins", SCRIPT)
assert SPEC and SPEC.loader
PLUGIN_COPY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLUGIN_COPY)


class WindowsReleasePluginCopyTest(unittest.TestCase):
    def test_skips_debug_sibling_without_dropping_release_name_ending_in_d(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "plugins"
            destination = root / "dist"
            tls = source / "tls"
            tls.mkdir(parents=True)
            for name in (
                "qschannelbackend.dll",
                "qschannelbackendd.dll",
                "legacybackend.dll",
            ):
                (tls / name).write_text(name, encoding="utf-8")
            staged_tls = destination / "tls"
            staged_tls.mkdir(parents=True)
            (staged_tls / "qschannelbackendd.dll").write_text(
                "already deployed", encoding="utf-8"
            )

            copied, skipped = PLUGIN_COPY.copy_release_plugin_directories(
                source, destination, ["tls"]
            )

            self.assertEqual(
                [path.name for path in copied],
                ["legacybackend.dll", "qschannelbackend.dll"],
            )
            self.assertEqual([path.name for path in skipped], ["qschannelbackendd.dll"])
            self.assertEqual(
                sorted(path.name for path in (destination / "tls").iterdir()),
                ["legacybackend.dll", "qschannelbackend.dll"],
            )


if __name__ == "__main__":
    unittest.main()
