#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd -P)"
repo_root="$(cd "${script_dir}/../.." && pwd -P)"
architecture="$(uname -m)"
openssl_prefix="${HOME}/Library/Caches/FinceptTerminal/fincept-openssl-3.5.9-macos13-${architecture}"
qt_prefix="${QT_DIR:-}"

if [ -z "$qt_prefix" ] && [ -f "${repo_root}/.qt/6.8.3/macos/lib/cmake/Qt6/Qt6Config.cmake" ]; then
  qt_prefix="${repo_root}/.qt/6.8.3/macos"
fi
if [ -z "$qt_prefix" ]; then
  echo "macOS release build: set QT_DIR to the Qt 6.8.3 macOS kit" >&2
  exit 2
fi

export FINCEPT_MACOS_ARCH="$architecture"
"${script_dir}/build_macos_openssl.sh" "$openssl_prefix" 13.0

cd "${repo_root}/fincept-qt"
cmake --preset macos-release --fresh \
  -DCMAKE_PREFIX_PATH="$qt_prefix" \
  -DOPENSSL_ROOT_DIR="$openssl_prefix" \
  -DOPENSSL_USE_STATIC_LIBS=OFF \
  "$@"
cmake --build --preset macos-release --parallel "$(sysctl -n hw.logicalcpu)"

app_bundle="${repo_root}/fincept-qt/build/macos-release/FinceptTerminal.app"
main_binary="${app_bundle}/Contents/MacOS/FinceptTerminal"

# macdeployqt and the OpenSSL bundler both rewrite Mach-O files. Apply the
# local ad-hoc signature only after all bundle mutations are complete.
codesign --force --deep --sign - --timestamp=none "$app_bundle"
codesign --verify --deep --strict --verbose=2 "$app_bundle"
"${script_dir}/macos_verify_openssl.sh" "$main_binary" 13.0

echo "macOS release build ready: ${app_bundle}"
