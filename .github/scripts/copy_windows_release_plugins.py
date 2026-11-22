#!/usr/bin/env python3
"""Copy Qt release plugins while excluding paired debug DLLs."""

from __future__ import annotations

import argparse
import shutil
from collections.abc import Iterable
from pathlib import Path


def copy_release_plugin_directories(
    source_root: Path, destination_root: Path, directories: Iterable[str]
) -> tuple[list[Path], list[Path]]:
    copied: list[Path] = []
    skipped: list[Path] = []

    for directory in directories:
        source = source_root / directory
        if not source.is_dir():
            continue
        destination = destination_root / directory
        destination.mkdir(parents=True, exist_ok=True)
        available = {path.name.lower() for path in source.glob("*.dll")}
        for path in sorted(source.glob("*.dll")):
            release_name = f"{path.stem[:-1]}{path.suffix}" if path.stem.endswith("d") else ""
            if release_name and release_name.lower() in available:
                deployed_debug = destination / path.name
                if deployed_debug.exists():
                    deployed_debug.unlink()
                skipped.append(path)
                continue
            target = destination / path.name
            if not target.exists():
                shutil.copy2(path, target)
            copied.append(target)

    return copied, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("destination_root", type=Path)
    parser.add_argument("directories", nargs="+")
    args = parser.parse_args()

    copied, skipped = copy_release_plugin_directories(
        args.source_root.resolve(), args.destination_root.resolve(), args.directories
    )
    for path in skipped:
        print(f"Skipped Qt debug plugin {path.parent.name}/{path.name}")
    for path in copied:
        print(f"Staged Qt release plugin {path.parent.name}/{path.name}")
    print(
        f"Qt release plugin copy: PASS ({len(copied)} staged, {len(skipped)} debug skipped)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
