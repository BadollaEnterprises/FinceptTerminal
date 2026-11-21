#!/usr/bin/env python3
"""Fail when a Windows bundle contains PE binaries for the wrong CPU."""

from __future__ import annotations

import argparse
import shutil
import struct
from collections import Counter
from pathlib import Path


MACHINES = {
    0x014C: "x86",
    0x01C4: "armv7",
    0x8664: "x64",
    0xAA64: "arm64",
}


def pe_machine(path: Path) -> str:
    with path.open("rb") as handle:
        header = handle.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise ValueError("missing DOS header")
        pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
        handle.seek(pe_offset)
        signature_and_machine = handle.read(6)

    if len(signature_and_machine) != 6 or signature_and_machine[:4] != bytes((80, 69, 0, 0)):
        raise ValueError("missing PE signature")
    machine = struct.unpack_from("<H", signature_and_machine, 4)[0]
    return MACHINES.get(machine, f"0x{machine:04x}")


def copy_matching_pe_files(
    source: Path, destination: Path, expected: str
) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Copy DLLs for ``expected`` and report foreign-architecture files.

    Visual Studio's ARM64 CRT directory can also contain an x64 compatibility
    runtime. Copying the directory wholesale produces a mixed-architecture
    application bundle, so packaging selects by the PE header instead of by
    path name alone. Invalid DLLs are a hard error rather than silently skipped.
    """

    copied: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.glob("*.dll")):
        try:
            machine = pe_machine(path)
        except (OSError, ValueError, struct.error) as error:
            raise ValueError(f"{path.name}: invalid PE ({error})") from error
        if machine != expected:
            skipped.append((path, machine))
            continue
        target = destination / path.name
        shutil.copy2(path, target)
        copied.append(target)
    return copied, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("expected", choices=("x64", "arm64"))
    parser.add_argument(
        "--copy-matching-to",
        type=Path,
        help="copy only matching DLLs to this directory instead of auditing a bundle",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    if args.copy_matching_to is not None:
        try:
            copied, skipped = copy_matching_pe_files(
                root, args.copy_matching_to.resolve(), args.expected
            )
        except ValueError as error:
            print(f"PE COPY FAIL: {error}")
            return 1
        for path, machine in skipped:
            print(f"Skipped {path.name}: {machine}, expected {args.expected}")
        for path in copied:
            print(f"Copied {args.expected} PE {path.name}")
        if not copied:
            print(f"PE COPY FAIL: no {args.expected} DLLs found under {root}")
            return 1
        print(f"PE copy filter: PASS ({len(copied)} {args.expected} DLLs)")
        return 0

    candidates = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".dll", ".exe"}
    )
    if not candidates:
        print(f"PE architecture check: no .exe or .dll files found under {root}")
        return 1

    counts: Counter[str] = Counter()
    failures: list[str] = []
    for path in candidates:
        relative = path.relative_to(root)
        try:
            machine = pe_machine(path)
        except (OSError, ValueError, struct.error) as error:
            failures.append(f"{relative}: invalid PE ({error})")
            continue
        counts[machine] += 1
        if machine != args.expected:
            failures.append(f"{relative}: {machine}, expected {args.expected}")

    print(
        "PE architecture check: "
        + ", ".join(f"{machine}={count}" for machine, count in sorted(counts.items()))
    )
    if failures:
        for failure in failures:
            print(f"PE ARCH FAIL: {failure}")
        return 1

    print(f"PE architecture check: PASS ({len(candidates)} {args.expected} binaries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
