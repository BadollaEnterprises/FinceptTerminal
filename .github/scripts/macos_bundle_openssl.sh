#!/bin/bash

set -euo pipefail

if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
  echo "usage: $0 <app-bundle> <libssl-dylib> <libcrypto-dylib> [maximum-macos-version]" >&2
  exit 2
fi

app_bundle="$1"
ssl_source="$2"
crypto_source="$3"
maximum_macos="${4:-13.0}"
script_dir="$(cd "$(dirname "$0")" && pwd -P)"
main_binary="${app_bundle}/Contents/MacOS/FinceptTerminal"
frameworks_dir="${app_bundle}/Contents/Frameworks"

if [ ! -f "$main_binary" ]; then
  echo "OpenSSL bundling: app binary not found: $main_binary" >&2
  exit 2
fi

resolve_file() {
  local path="$1"
  while [ -L "$path" ]; do
    local target
    target="$(readlink "$path")"
    case "$target" in
      /*) path="$target" ;;
      *) path="$(dirname "$path")/$target" ;;
    esac
  done
  printf '%s/%s\n' "$(cd "$(dirname "$path")" && pwd -P)" "$(basename "$path")"
}

ssl_source="$(resolve_file "$ssl_source")"
crypto_source="$(resolve_file "$crypto_source")"
ssl_name="$(basename "$ssl_source")"
crypto_name="$(basename "$crypto_source")"
ssl_dest="${frameworks_dir}/${ssl_name}"
crypto_dest="${frameworks_dir}/${crypto_name}"

case "$ssl_name" in libssl*.dylib) ;; *) echo "OpenSSL bundling: unexpected SSL library: $ssl_source" >&2; exit 2 ;; esac
case "$crypto_name" in libcrypto*.dylib) ;; *) echo "OpenSSL bundling: unexpected Crypto library: $crypto_source" >&2; exit 2 ;; esac

mkdir -p "$frameworks_dir"
cp -f "$ssl_source" "$ssl_dest"
cp -f "$crypto_source" "$crypto_dest"
chmod u+w "$ssl_dest" "$crypto_dest" "$main_binary"

install_name_tool -id "@rpath/${ssl_name}" "$ssl_dest"
install_name_tool -id "@rpath/${crypto_name}" "$crypto_dest"

while IFS= read -r dependency; do
  case "$(basename "$dependency")" in
    libcrypto*.dylib)
      install_name_tool -change "$dependency" "@rpath/${crypto_name}" "$ssl_dest"
      ;;
  esac
done < <(otool -L "$ssl_dest" | tail -n +2 | awk '{print $1}')

while IFS= read -r dependency; do
  case "$(basename "$dependency")" in
    libssl*.dylib)
      install_name_tool -change "$dependency" "@rpath/${ssl_name}" "$main_binary"
      ;;
    libcrypto*.dylib)
      install_name_tool -change "$dependency" "@rpath/${crypto_name}" "$main_binary"
      ;;
  esac
done < <(otool -L "$main_binary" | tail -n +2 | awk '{print $1}')

if ! otool -l "$main_binary" | awk '
  $1 == "cmd" && $2 == "LC_RPATH" { in_rpath = 1; next }
  in_rpath && $1 == "path" && $2 == "@executable_path/../Frameworks" { found = 1 }
  END { exit(found ? 0 : 1) }
'; then
  install_name_tool -add_rpath "@executable_path/../Frameworks" "$main_binary"
fi

"${script_dir}/macos_verify_openssl.sh" "$main_binary" "$maximum_macos"
echo "OpenSSL bundling: embedded ${ssl_name} and ${crypto_name}"
