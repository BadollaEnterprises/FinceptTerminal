#!/bin/bash

set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <arm64-app> <x64-app> <output-app>" >&2
  exit 2
fi

arm_app="$1"
x64_app="$2"
output_app="$3"

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
  if ! tr ' ' '\n' <<<"$arm_archs" | grep -qx arm64; then
    echo "Universal app: arm64 input lacks arm64 slice: $relative_path ($arm_archs)" >&2
    exit 1
  fi
  if ! tr ' ' '\n' <<<"$x64_archs" | grep -qx x86_64; then
    echo "Universal app: x64 input lacks x86_64 slice: $relative_path ($x64_archs)" >&2
    exit 1
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

if [ "$merged_count" -eq 0 ]; then
  echo "Universal app: no Mach-O files were merged" >&2
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
    if ! tr ' ' '\n' <<<"$architectures" | grep -qx "$required_architecture"; then
      echo "Universal app: output lacks $required_architecture slice: ${output_file#"$output_app"/}" >&2
      exit 1
    fi
  done
done

echo "Universal app: merged $merged_count Mach-O files into $output_app"
