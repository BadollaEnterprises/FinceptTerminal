#!/usr/bin/env python3
"""Fail when a Windows bundle contains PE binaries for the wrong CPU."""

from __future__ import annotations

import argparse
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("expected", choices=("x64", "arm64"))
    args = parser.parse_args()

    root = args.root.resolve()
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
