#!/usr/bin/env python3
"""Fast static checks for release workflow architecture contracts."""

from __future__ import annotations

import argparse
from pathlib import Path


def require(text: str, needle: str, message: str, failures: list[str]) -> None:
    if needle not in text:
        failures.append(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    workflow = root / ".github" / "workflows" / "build-cpp.yml"
    text = workflow.read_text(encoding="utf-8")
    cmake_text = (root / "fincept-qt" / "CMakeLists.txt").read_text(encoding="utf-8")
    lint_text = (root / ".github" / "workflows" / "lint.yml").read_text(encoding="utf-8")
    failures: list[str] = []

    require(
        text,
        'OPENSSL_TRIPLET="arm64-windows"',
        "Windows ARM64 must install the arm64-windows OpenSSL triplet",
        failures,
    )
    require(
        text,
        'echo "QT_HOST_ROOT=$HOST_QT" >> "$GITHUB_ENV"',
        "Windows ARM64 must export the separately installed Qt host-tools path",
        failures,
    )
    require(
        text,
        'WDQT="${QT_HOST_ROOT}/bin/windeployqt.exe"',
        "Windows ARM64 packaging must run windeployqt from the host Qt kit",
        failures,
    )
    require(
        text,
        'echo "::error::windeployqt not found',
        "Windows packaging must fail instead of publishing an incomplete Qt bundle",
        failures,
    )
    require(
        cmake_text,
        "libcrypto-3-arm64.dll",
        "CMake OpenSSL deployment must recognize the Windows ARM64 crypto DLL",
        failures,
    )
    require(
        cmake_text,
        "libssl-3-arm64.dll",
        "CMake OpenSSL deployment must recognize the Windows ARM64 SSL DLL",
        failures,
    )
    require(
        text,
        '"${OPENSSL_ROOT_DIR}"/bin/libssl*.dll',
        "Windows packaging must copy OpenSSL DLLs from the selected vcpkg root",
        failures,
    )
    require(
        lint_text,
        "'.github/workflows/build-cpp.yml'",
        "lint workflow must run packaging checks when build-cpp.yml changes",
        failures,
    )
    require(
        lint_text,
        "'.github/workflows/release.yml'",
        "lint workflow must run packaging checks when release.yml changes",
        failures,
    )
    require(
        lint_text,
        "python3 .github/scripts/check_packaging_contracts.py .",
        "packaging-metadata job must execute the packaging contract check",
        failures,
    )
    require(
        text,
        'ZLIB_TRIPLET="arm64-windows-static-md"',
        "Windows ARM64 must install the arm64-windows-static-md zlib triplet",
        failures,
    )
    require(
        text,
        'OPENSSL_DIR="$VCPKG_ROOT/installed/$OPENSSL_TRIPLET"',
        "OPENSSL_ROOT_DIR must follow the selected matrix architecture",
        failures,
    )
    require(
        text,
        'ZLIB_DIR="$VCPKG_ROOT/installed/$ZLIB_TRIPLET"',
        "ZLIB_ROOT must follow the selected matrix architecture",
        failures,
    )

    if failures:
        for failure in failures:
            print(f"PACKAGING CONTRACT FAIL: {failure}")
        return 1

    print("Packaging workflow contracts: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
