#!/bin/bash

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 <app-binary> [maximum-macos-version]" >&2
  exit 2
fi

binary="$1"
maximum_macos="${2:-13.0}"

if [ ! -f "$binary" ]; then
  echo "OpenSSL compatibility check: binary not found: $binary" >&2
  exit 2
fi

frameworks_dir="$(cd "$(dirname "$binary")/../Frameworks" 2>/dev/null && pwd -P || true)"
failures=0
checked=0

version_is_newer() {
  awk -v actual="$1" -v maximum="$2" 'BEGIN {
    split(actual, a, ".");
    split(maximum, m, ".");
    for (i = 1; i <= 3; ++i) {
      av = (a[i] == "" ? 0 : a[i]) + 0;
      mv = (m[i] == "" ? 0 : m[i]) + 0;
      if (av > mv) exit 0;
      if (av < mv) exit 1;
    }
    exit 1;
  }'
}

resolve_dependency() {
  dep="$1"
  case "$dep" in
    @rpath/*|@loader_path/../Frameworks/*|@executable_path/../Frameworks/*)
      printf '%s/%s\n' "$frameworks_dir" "${dep##*/}"
      ;;
    *)
      printf '%s\n' "$dep"
      ;;
  esac
}

while IFS= read -r dep; do
  case "$(basename "$dep")" in
    libssl*.dylib|libcrypto*.dylib) ;;
    *) continue ;;
  esac

  checked=$((checked + 1))
  resolved="$(resolve_dependency "$dep")"

  case "$dep" in
    /usr/lib/*|/System/*|@rpath/*|@loader_path/../Frameworks/*|@executable_path/../Frameworks/*) ;;
    *)
      echo "OpenSSL compatibility check: external dependency: $dep" >&2
      failures=$((failures + 1))
      ;;
  esac

  if [ ! -f "$resolved" ]; then
    echo "OpenSSL compatibility check: unresolved dependency: $dep (expected $resolved)" >&2
    failures=$((failures + 1))
    continue
  fi

  found_minos=0
  while IFS= read -r minos; do
    [ -n "$minos" ] || continue
    found_minos=1
    if version_is_newer "$minos" "$maximum_macos"; then
      echo "OpenSSL compatibility check: $(basename "$resolved") requires macOS $minos, target is $maximum_macos" >&2
      failures=$((failures + 1))
    fi
  done < <(otool -l "$resolved" | awk '
    $1 == "cmd" && $2 == "LC_BUILD_VERSION" { in_build = 1; next }
    in_build && $1 == "minos" { print $2; in_build = 0 }
  ')

  if [ "$found_minos" -eq 0 ]; then
    echo "OpenSSL compatibility check: no LC_BUILD_VERSION in $resolved" >&2
    failures=$((failures + 1))
  fi
done < <(otool -L "$binary" | tail -n +2 | awk '{print $1}')

if [ "$checked" -lt 2 ]; then
  echo "OpenSSL compatibility check: expected libssl and libcrypto, found $checked linked libraries" >&2
  failures=$((failures + 1))
fi

if [ "$failures" -ne 0 ]; then
  echo "OpenSSL compatibility check: FAILED ($failures issue(s))" >&2
  exit 1
fi

echo "OpenSSL compatibility check: PASS ($checked libraries, macOS <= $maximum_macos, bundle-relative)"
