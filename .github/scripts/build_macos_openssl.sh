#!/bin/bash

set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 <install-prefix> [minimum-macos-version]" >&2
  exit 2
fi

install_prefix="$1"
minimum_macos="${2:-13.0}"
openssl_version="3.5.9"
openssl_sha256="603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a"
archive="openssl-${openssl_version}.tar.gz"
source_url="https://github.com/openssl/openssl/releases/download/openssl-${openssl_version}/${archive}"
architecture="${FINCEPT_MACOS_ARCH:-$(uname -m)}"
marker="${install_prefix}/.fincept-openssl-build"

case "$install_prefix" in
  *[[:space:]]*)
    echo "OpenSSL build: install prefix must not contain whitespace: $install_prefix" >&2
    exit 2
    ;;
esac
case "$install_prefix" in
  /*/fincept-openssl-*|/fincept-openssl-*) ;;
  *)
    echo "OpenSSL build: refusing unsafe install prefix: $install_prefix" >&2
    echo "OpenSSL build: use an absolute path whose final component starts with fincept-openssl-" >&2
    exit 2
    ;;
esac

case "$architecture" in
  arm64|aarch64)
    architecture="arm64"
    configure_target="darwin64-arm64-cc"
    ;;
  x86_64|amd64)
    architecture="x86_64"
    configure_target="darwin64-x86_64-cc"
    ;;
  *)
    echo "OpenSSL build: unsupported macOS architecture: $architecture" >&2
    exit 2
    ;;
esac

validate_dylib() {
  local dylib="$1"
  if [ ! -f "$dylib" ]; then
    echo "OpenSSL build: missing library: $dylib" >&2
    return 1
  fi
  if ! lipo -archs "$dylib" | tr ' ' '\n' | grep -qx "$architecture"; then
    echo "OpenSSL build: $(basename "$dylib") does not contain ${architecture}" >&2
    return 1
  fi
  local invalid_minos
  invalid_minos="$(otool -l "$dylib" | awk -v maximum="$minimum_macos" '
    $1 == "cmd" && $2 == "LC_BUILD_VERSION" { in_build = 1; next }
    in_build && $1 == "minos" {
      split($2, a, "."); split(maximum, m, "."); newer = 0; decided = 0;
      for (i = 1; i <= 3; ++i) {
        av = (a[i] == "" ? 0 : a[i]) + 0; mv = (m[i] == "" ? 0 : m[i]) + 0;
        if (!decided && av > mv) { newer = 1; decided = 1 }
        if (!decided && av < mv) { decided = 1 }
      }
      if (newer) print $2;
      in_build = 0
    }
  ')"
  if [ -n "$invalid_minos" ]; then
    echo "OpenSSL build: $(basename "$dylib") requires macOS ${invalid_minos}, expected <= ${minimum_macos}" >&2
    return 1
  fi
}

expected_marker="version=${openssl_version};macos=${minimum_macos};arch=${architecture}"
if [ -f "$marker" ] \
   && [ "$(cat "$marker")" = "$expected_marker" ] \
   && [ -f "${install_prefix}/lib/libssl.3.dylib" ] \
   && [ -f "${install_prefix}/lib/libcrypto.3.dylib" ]; then
  if validate_dylib "${install_prefix}/lib/libssl.3.dylib" \
     && validate_dylib "${install_prefix}/lib/libcrypto.3.dylib"; then
    echo "OpenSSL build: using cached ${openssl_version} (${architecture}, macOS ${minimum_macos}) at ${install_prefix}"
    exit 0
  fi
  echo "OpenSSL build: cached libraries failed validation; rebuilding"
fi

work_dir="$(mktemp -d /tmp/fincept-openssl.XXXXXX)"
cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT

echo "OpenSSL build: downloading ${source_url}"
curl --fail --location --silent --show-error \
  --output "${work_dir}/${archive}" "$source_url"

actual_sha256="$(shasum -a 256 "${work_dir}/${archive}" | awk '{print $1}')"
if [ "$actual_sha256" != "$openssl_sha256" ]; then
  echo "OpenSSL build: checksum mismatch (expected ${openssl_sha256}, got ${actual_sha256})" >&2
  exit 1
fi

tar -xzf "${work_dir}/${archive}" -C "$work_dir"
source_dir="${work_dir}/openssl-${openssl_version}"
build_log="${work_dir}/build.log"

mkdir -p "$install_prefix"
rm -rf "${install_prefix:?}/include" "${install_prefix:?}/lib" \
       "${install_prefix:?}/bin" "${install_prefix:?}/share" \
       "${install_prefix:?}/ssl" "$marker"

echo "OpenSSL build: compiling ${openssl_version} for ${architecture}, macOS ${minimum_macos}"
(
  cd "$source_dir"
  export MACOSX_DEPLOYMENT_TARGET="$minimum_macos"
  ./Configure "$configure_target" shared no-tests \
    --prefix="$install_prefix" \
    --openssldir="${install_prefix}/ssl" \
    --libdir=lib \
    "-mmacosx-version-min=${minimum_macos}"
  make -j "$(sysctl -n hw.logicalcpu 2>/dev/null || echo 4)"
  make install_sw
) >"$build_log" 2>&1 || {
  echo "OpenSSL build: failed; final log lines:" >&2
  tail -80 "$build_log" >&2
  exit 1
}

validate_dylib "${install_prefix}/lib/libssl.3.dylib"
validate_dylib "${install_prefix}/lib/libcrypto.3.dylib"

printf '%s\n' "$expected_marker" > "$marker"
echo "OpenSSL build: ready at ${install_prefix}"
