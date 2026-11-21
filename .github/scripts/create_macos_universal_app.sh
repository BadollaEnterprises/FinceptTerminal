#!/bin/bash

set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <arm64-app> <x64-app> <output-app>" >&2
  exit 2
fi

arm_app="$1"
x64_app="$2"
output_app="$3"

has_arch() {
  tr ' ' '\n' <<<"$1" | grep -qx "$2"
}

for app in "$arm_app" "$x64_app"; do
  if [ ! -d "$app/Contents/MacOS" ]; then
    echo "Universal app: invalid application bundle: $app" >&2
    exit 2
  fi
done

case "$output_app" in
  /*.app|./*.app|*.app) ;;
  *)
    echo "Universal app: output must be an .app bundle: $output_app" >&2
    exit 2
    ;;
esac

rm -rf "$output_app"
mkdir -p "$(dirname "$output_app")"
ditto "$arm_app" "$output_app"

merged_count=0
preserved_universal_count=0
while IFS= read -r -d '' arm_file; do
  if ! file -b "$arm_file" | grep -q 'Mach-O'; then
    continue
  fi

  relative_path="${arm_file#"$arm_app"/}"
  x64_file="$x64_app/$relative_path"
  output_file="$output_app/$relative_path"
  if [ ! -f "$x64_file" ] || ! file -b "$x64_file" | grep -q 'Mach-O'; then
    echo "Universal app: missing x64 Mach-O counterpart: $relative_path" >&2
    exit 1
  fi

  arm_archs="$(lipo -archs "$arm_file")"
  x64_archs="$(lipo -archs "$x64_file")"
  if ! has_arch "$arm_archs" arm64; then
    echo "Universal app: arm64 input lacks arm64 slice: $relative_path ($arm_archs)" >&2
    exit 1
  fi
  if ! has_arch "$x64_archs" x86_64; then
    echo "Universal app: x64 input lacks x86_64 slice: $relative_path ($x64_archs)" >&2
    exit 1
  fi

  # Some pinned third-party helpers (notably yt-dlp_macos) are already
  # universal in both architecture-specific app bundles. Feeding two fat
  # binaries with overlapping slices to `lipo -create` is an error. Preserve
  # an input that already contains both required slices; the final audit below
  # still verifies every Mach-O in the output bundle.
  if has_arch "$arm_archs" arm64 && has_arch "$arm_archs" x86_64; then
    echo "Universal app: already universal, preserving arm input: $relative_path"
    preserved_universal_count=$((preserved_universal_count + 1))
    continue
  fi
  if has_arch "$x64_archs" arm64 && has_arch "$x64_archs" x86_64; then
    echo "Universal app: already universal, preserving x64 input: $relative_path"
    cp "$x64_file" "$output_file"
    chmod "$(stat -f '%Lp' "$x64_file")" "$output_file"
    preserved_universal_count=$((preserved_universal_count + 1))
    continue
  fi

  temporary_file="${output_file}.universal"
  lipo -create "$arm_file" "$x64_file" -output "$temporary_file"
  chmod "$(stat -f '%Lp' "$arm_file")" "$temporary_file"
  mv "$temporary_file" "$output_file"
  merged_count=$((merged_count + 1))
done < <(find "$arm_app" -type f -print0)

while IFS= read -r -d '' x64_file; do
  if ! file -b "$x64_file" | grep -q 'Mach-O'; then
    continue
  fi
  relative_path="${x64_file#"$x64_app"/}"
  arm_file="$arm_app/$relative_path"
  if [ ! -f "$arm_file" ] || ! file -b "$arm_file" | grep -q 'Mach-O'; then
    echo "Universal app: missing arm64 Mach-O counterpart: $relative_path" >&2
    exit 1
  fi
done < <(find "$x64_app" -type f -print0)

if [ $((merged_count + preserved_universal_count)) -eq 0 ]; then
  echo "Universal app: no Mach-O files were merged or preserved" >&2
  exit 1
fi

xattr -cr "$output_app"
find "$output_app" -type f -print0 | while IFS= read -r -d '' output_file; do
  if file -b "$output_file" | grep -q 'Mach-O'; then
    codesign --remove-signature "$output_file" 2>/dev/null || true
  fi
done
codesign --remove-signature "$output_app" 2>/dev/null || true
codesign --force --deep --sign - --timestamp=none "$output_app"
codesign --verify --deep --strict --verbose=2 "$output_app"

find "$output_app" -type f -print0 | while IFS= read -r -d '' output_file; do
  if ! file -b "$output_file" | grep -q 'Mach-O'; then
    continue
  fi
  architectures="$(lipo -archs "$output_file")"
  for required_architecture in arm64 x86_64; do
    if ! has_arch "$architectures" "$required_architecture"; then
      echo "Universal app: output lacks $required_architecture slice: ${output_file#"$output_app"/}" >&2
      exit 1
    fi
  done
done

echo "Universal app: merged $merged_count Mach-O files and preserved $preserved_universal_count already-universal files into $output_app"
