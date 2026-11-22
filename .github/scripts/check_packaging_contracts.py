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
    pe_arch_script = root / ".github" / "scripts" / "check_windows_pe_arch.py"
    dependency_closure_script = (
        root / ".github" / "scripts" / "check_windows_dependency_closure.py"
    )
    release_plugin_copy_script = (
        root / ".github" / "scripts" / "copy_windows_release_plugins.py"
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
        text,
        'WDQT_RUNTIME_ARG="--no-compiler-runtime"',
        "Windows ARM64 packaging must not copy the host compiler runtime",
        failures,
    )
    require(
        text,
        'Rehydrated ARM64 ${relative_path}',
        "Windows ARM64 packaging must replace host Qt binaries from the target kit",
        failures,
    )
    require(
        text,
        "-path '*/arm64/Microsoft.VC*.CRT'",
        "Windows ARM64 packaging must copy the target Visual Studio runtime",
        failures,
    )
    require(
        text,
        '"$CRT_DIR" arm64 --copy-matching-to dist',
        "Windows ARM64 runtime staging must filter the CRT by PE architecture",
        failures,
    )
    require(
        text,
        'check_windows_pe_arch.py dist "${{ matrix.arch }}"',
        "Windows artifacts must pass a recursive PE architecture audit",
        failures,
    )
    if not pe_arch_script.is_file():
        failures.append("Windows PE architecture checker must exist")
    if not dependency_closure_script.is_file():
        failures.append("Windows dependency closure checker must exist")
    if not release_plugin_copy_script.is_file():
        failures.append("Windows release-plugin selector must exist")
    require(
        text,
        "check_windows_dependency_closure.py dist",
        "Windows artifacts must pass dependency-closure verification",
        failures,
    )
    require(
        text,
        "copy_windows_release_plugins.py",
        "Windows matrix packaging must exclude paired Qt debug plugins",
        failures,
    )
    require(
        release_text,
        "copy_windows_release_plugins.py",
        "Windows release packaging must exclude paired Qt debug plugins",
        failures,
    )
    if "qwindowsd.dll" in text:
        failures.append("Windows matrix packaging must not explicitly stage qwindowsd.dll")
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
        'if(FINCEPT_DEV_BUILD OR (MSVC AND CMAKE_CXX_COMPILER_ID STREQUAL "Clang"))',
        "ClangCL must not combine unity config macros with precompiled headers",
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
        lint_text,
        "python3 .github/scripts/test_ci_app_checks.py",
        "packaging-metadata job must test packaged self-test discovery",
        failures,
    )
    require(
        lint_text,
        "python3 .github/scripts/test_check_windows_dependency_closure.py",
        "packaging-metadata job must test the Windows dependency closure checker",
        failures,
    )
    require(
        lint_text,
        "python3 .github/scripts/test_copy_windows_release_plugins.py",
        "packaging-metadata job must test Qt release-plugin selection",
        failures,
    )
    require(
        release_text,
        "'^(vcruntime|msvcp|concrt|vccorlib|vcomp|vcamp).*\\.dll$'",
        "Windows release closure must require MSVC runtimes inside the artifact",
        failures,
    )
    require(
        release_text,
        "check_windows_dependency_closure.py $buildDir",
        "Windows release must reject debug runtimes using the shared closure checker",
        failures,
    )
    require(
        release_text,
        "for zlib_dll in zlib1.dll; do",
        "Windows release must not stage the debug zlibd1.dll runtime",
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
            'already universal, preserving arm input',
            "macOS universal merge must preserve helpers that already contain both slices",
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
