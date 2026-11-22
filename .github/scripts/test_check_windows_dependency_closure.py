#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check_windows_dependency_closure.py")
SPEC = importlib.util.spec_from_file_location("check_windows_dependency_closure", SCRIPT)
assert SPEC and SPEC.loader
CLOSURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLOSURE)


class WindowsDependencyClosureTest(unittest.TestCase):
    def test_rejects_bundle_without_pe_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "dist"
            system32 = root / "System32"
            bundle.mkdir()
            system32.mkdir()

            failures = CLOSURE.check_dependency_closure(
                bundle, system32, lambda path: []
            )

            self.assertEqual(failures, ["bundle contains no PE binaries"])

    def test_requires_msvc_runtime_inside_bundle_even_when_host_has_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "dist"
            system32 = root / "System32"
            bundle.mkdir()
            system32.mkdir()
            (bundle / "FinceptTerminal.exe").touch()
            (system32 / "vcruntime140_1.dll").touch()

            failures = CLOSURE.check_dependency_closure(
                bundle,
                system32,
                lambda path: ["vcruntime140_1.dll"] if path.suffix == ".exe" else [],
            )

            self.assertEqual(
                failures,
                ["FinceptTerminal.exe imports unstaged MSVC runtime vcruntime140_1.dll"],
            )

    def test_accepts_bundled_runtime_and_windows_system_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "dist"
            system32 = root / "System32"
            bundle.mkdir()
            system32.mkdir()
            executable = bundle / "FinceptTerminal.exe"
            runtime = bundle / "vcruntime140.dll"
            executable.touch()
            runtime.touch()
            (system32 / "KERNEL32.dll").touch()

            dependencies = {
                executable.name: ["vcruntime140.dll", "KERNEL32.dll", "api-ms-test.dll"],
                runtime.name: ["KERNEL32.dll"],
            }
            failures = CLOSURE.check_dependency_closure(
                bundle, system32, lambda path: dependencies[path.name]
            )

            self.assertEqual(failures, [])

    def test_rejects_nonredistributable_debug_msvc_runtime_even_when_staged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "dist"
            system32 = root / "System32"
            bundle.mkdir()
            system32.mkdir()
            executable = bundle / "FinceptTerminal.exe"
            debug_runtime = bundle / "vcruntime140_1d.dll"
            executable.touch()
            debug_runtime.touch()

            failures = CLOSURE.check_dependency_closure(
                bundle,
                system32,
                lambda path: ["vcruntime140_1d.dll"] if path == executable else [],
            )

            self.assertEqual(
                failures,
                [
                    "FinceptTerminal.exe imports non-redistributable debug MSVC runtime vcruntime140_1d.dll",
                    "bundle contains non-redistributable debug MSVC runtime vcruntime140_1d.dll",
                ],
            )

    def test_parses_only_dumpbin_dependency_lines(self) -> None:
        output = """
Dump of file FinceptTerminal.exe

  Image has the following dependencies:

    Qt6Core.dll
    VCRUNTIME140.dll

  Summary
"""

        self.assertEqual(
            CLOSURE.parse_dumpbin_dependents(output),
            ["Qt6Core.dll", "VCRUNTIME140.dll"],
        )


if __name__ == "__main__":
    unittest.main()
