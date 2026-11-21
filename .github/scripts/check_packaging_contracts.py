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
    release_text = (root / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    macos_openssl_text = (
        root / ".github" / "scripts" / "build_macos_openssl.sh"
    ).read_text(encoding="utf-8")
    universal_script = root / ".github" / "scripts" / "create_macos_universal_app.sh"
    python_framework_script = (
        root / ".github" / "scripts" / "embed_macos_python_framework.sh"
    )
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
        'target_qt_modules: "qtcharts qtwebsockets qtmultimedia qtwebchannel qtpositioning qtserialport"',
        "Windows ARM64 target Qt modules must omit unavailable Qt WebEngine",
        failures,
    )
    require(
        text,
        'modules: ${{ matrix.target_qt_modules || env.QT_MODULES }}',
        "target Qt installation must honor the architecture-specific module set",
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
        cmake_text,
        'if(NOT MSVC AND CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")',
        "ClangCL must not inherit Unix -Wall/-Werror flags in addition to MSVC warnings",
        failures,
    )
    require(
        cmake_text,
        'if(MSVC AND CMAKE_CXX_COMPILER_ID STREQUAL "Clang")\n    message(STATUS "Precompiled headers disabled for ClangCL',
        "ClangCL must not consume CMake unity PCH files with mismatched config macros",
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
    for workflow_name, workflow_text in (
        ("matrix", text),
        ("release", release_text),
    ):
        require(
            workflow_text,
            "for candidate in zlib.lib zs.lib libzs.lib; do",
            f"Windows {workflow_name} packaging must recognize current vcpkg zlib archive names",
            failures,
        )
    require(
        macos_openssl_text,
        "x86_64|amd64|x64)",
        "macOS OpenSSL builder must accept the workflow's x64 architecture label",
        failures,
    )
    require(
        text,
        "actions/checkout@v4\n\n      - name: Download macOS arm64",
        "macOS Universal job must check out repository scripts before using them",
        failures,
    )
    require(
        text,
        ".github/scripts/create_macos_universal_app.sh",
        "macOS Universal job must merge the complete application bundle",
        failures,
    )
    require(
        text,
        '../.github/scripts/embed_macos_python_framework.sh "${APP_BUNDLE}" 3.12',
        "macOS matrix packaging must use the canonical Python framework embedder",
        failures,
    )
    if not python_framework_script.is_file():
        failures.append("macOS Python framework embedder must exist")
    else:
        python_framework_text = python_framework_script.read_text(encoding="utf-8")
        require(
            python_framework_text,
            'Pruning extra Python version from bundle:',
            "macOS Python embedder must remove extra framework versions",
            failures,
        )
        require(
            python_framework_text,
            'fix_framework_symlinks',
            "macOS Python embedder must rebuild framework symlink layers",
            failures,
        )
    if not universal_script.is_file():
        failures.append("macOS universal application merge script must exist")
    else:
        universal_text = universal_script.read_text(encoding="utf-8")
        require(
            universal_text,
            'find "$arm_app" -type f',
            "macOS universal merge must inspect every bundled file",
            failures,
        )
        require(
            universal_text,
            'lipo -create "$arm_file" "$x64_file"',
            "macOS universal merge must combine each matching Mach-O file",
            failures,
        )
        require(
            universal_text,
            'codesign --verify --deep --strict',
            "macOS universal bundle must pass strict signature verification",
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
