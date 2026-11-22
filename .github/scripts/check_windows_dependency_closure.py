#!/usr/bin/env python3
"""Verify that a staged Windows bundle closes every DLL dependency."""

from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path


DependencyReader = Callable[[Path], Iterable[str]]
MSVC_RUNTIME_PREFIXES = ("vcruntime", "msvcp", "concrt", "vccorlib")


def is_msvc_runtime(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(".dll") and lowered.startswith(MSVC_RUNTIME_PREFIXES)


def is_debug_msvc_runtime(name: str) -> bool:
    lowered = name.lower()
    return is_msvc_runtime(lowered) and Path(lowered).stem.endswith("d")


def parse_dumpbin_dependents(output: str) -> list[str]:
    return [
        line.strip()
        for line in output.splitlines()
        if re.fullmatch(r"[A-Za-z0-9_.-]+\.dll", line.strip(), re.IGNORECASE)
    ]


def check_dependency_closure(
    bundle: Path, system32: Path, dependency_reader: DependencyReader
) -> list[str]:
    binaries = sorted(
        path
        for path in bundle.rglob("*")
        if path.is_file() and path.suffix.lower() in {".dll", ".exe"}
    )
    if not binaries:
        return ["bundle contains no PE binaries"]
    staged = {path.name.lower() for path in binaries}
    failures = [
        f"bundle contains non-redistributable debug MSVC runtime {path.name}"
        for path in binaries
        if is_debug_msvc_runtime(path.name)
    ]

    for binary in binaries:
        relative = binary.relative_to(bundle)
        for dependency in dependency_reader(binary):
            lowered = dependency.lower()
            if is_debug_msvc_runtime(lowered):
                failures.append(
                    f"{relative} imports non-redistributable debug MSVC runtime {dependency}"
                )
                continue
            if lowered in staged:
                continue
            if is_msvc_runtime(lowered):
                failures.append(
                    f"{relative} imports unstaged MSVC runtime {dependency}"
                )
                continue
            if lowered.startswith(("api-ms-", "ext-ms-")):
                continue
            if (system32 / dependency).is_file():
                continue
            failures.append(f"{relative} imports missing dependency {dependency}")

    return sorted(set(failures))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--dumpbin", required=True, type=Path)
    parser.add_argument("--system32", required=True, type=Path)
    args = parser.parse_args()

    bundle = args.bundle.resolve()

    def read_dependencies(path: Path) -> list[str]:
        result = subprocess.run(
            [str(args.dumpbin), "/dependents", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"dumpbin failed for {path.relative_to(bundle)}: {result.stderr.strip()}"
            )
        return parse_dumpbin_dependents(result.stdout)

    try:
        failures = check_dependency_closure(
            bundle, args.system32.resolve(), read_dependencies
        )
    except (OSError, RuntimeError) as error:
        print(f"DEPENDENCY CLOSURE FAIL: {error}")
        return 1

    if failures:
        for failure in failures:
            print(f"DEPENDENCY CLOSURE FAIL: {failure}")
        return 1

    print("Dependency closure: PASS (all imports are bundled or provided by Windows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
