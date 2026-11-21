#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 3 ]]; then
    echo "usage: $0 <App.app> [python-version] [source-Python.framework]" >&2
    exit 2
fi

app_bundle=$1
python_version=${2:-3.12}
source_override=${3:-}

if [[ ! -d "$app_bundle/Contents" ]]; then
    echo "error: application bundle has no Contents directory: $app_bundle" >&2
    exit 1
fi

python_framework=$source_override
if [[ -z "$python_framework" ]]; then
    for candidate in \
        "$(brew --prefix "python@${python_version}" 2>/dev/null)/Frameworks/Python.framework" \
        "/Library/Frameworks/Python.framework" \
        "$(brew --prefix python3 2>/dev/null)/Frameworks/Python.framework"; do
        if [[ -d "$candidate/Versions" ]]; then
            python_framework=$candidate
            break
        fi
    done
fi

if [[ -z "$python_framework" || ! -d "$python_framework/Versions" ]]; then
    echo "warning: Python.framework not found; bundle will rely on system Python" >&2
    exit 0
fi

bundle_frameworks="$app_bundle/Contents/Frameworks"
bundled_python="$bundle_frameworks/Python.framework"
mkdir -p "$bundle_frameworks" "$app_bundle/Contents/MacOS"
rm -rf "$bundled_python"

echo "Embedding Python.framework from: $python_framework"
rsync -a "$python_framework/" "$bundled_python/"

if [[ ! -d "$bundled_python/Versions/$python_version" ]]; then
    echo "error: expected Python $python_version under $bundled_python/Versions; found:" >&2
    ls -la "$bundled_python/Versions" >&2 || true
    exit 1
fi

for version_dir in "$bundled_python/Versions/"*; do
    name=$(basename "$version_dir")
    [[ "$name" == "Current" || "$name" == "$python_version" ]] && continue
    [[ -d "$version_dir" ]] || continue
    echo "Pruning extra Python version from bundle: $name"
    rm -rf "$version_dir"
done

fix_framework_symlinks() {
    local framework=$1
    local version=$2
    local framework_name
    framework_name=$(basename "$framework" .framework)
    [[ -d "$framework/Versions/$version" ]] || {
        echo "warning: skipping $framework; Versions/$version is missing" >&2
        return 0
    }

    (
        cd "$framework/Versions"
        rm -f Current
        ln -s "$version" Current
    )
    for link in "$framework_name" Resources Headers; do
        target="Versions/Current/$link"
        if [[ -e "$framework/$target" || -L "$framework/$target" ]]; then
            (
                cd "$framework"
                rm -f "$link"
                ln -s "$target" "$link"
            )
        fi
    done
}

fix_framework_symlinks "$bundled_python" "$python_version"

nested_root="$bundled_python/Versions/$python_version/Frameworks"
if [[ -d "$nested_root" ]]; then
    for nested_framework in "$nested_root"/*.framework; do
        [[ -d "$nested_framework/Versions" ]] || continue
        nested_version=""
        for version_dir in "$nested_framework/Versions/"*; do
            name=$(basename "$version_dir")
            [[ "$name" == "Current" ]] && continue
            if [[ -d "$version_dir" ]]; then
                nested_version=$name
                break
            fi
        done
        [[ -n "$nested_version" ]] || continue
        fix_framework_symlinks "$nested_framework" "$nested_version"
    done
fi

find "$bundled_python" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$bundled_python" -name "*.pyc" -delete 2>/dev/null || true
find "$bundled_python" -name "*.pyo" -delete 2>/dev/null || true
find "$bundled_python" -type l ! -exec test -e {} \; -delete 2>/dev/null || true
xattr -cr "$bundled_python" 2>/dev/null || true

ln -sf "../Frameworks/Python.framework/Versions/$python_version/bin/python$python_version" \
    "$app_bundle/Contents/MacOS/python3"

for framework in "$bundled_python" "$nested_root"/*.framework; do
    [[ -d "$framework" ]] || continue
    if [[ ! -L "$framework/Versions/Current" ]]; then
        echo "error: $framework/Versions/Current is not a symlink" >&2
        ls -la "$framework/Versions" >&2 || true
        exit 1
    fi
done

echo "Python.framework structure OK (kept $python_version)"
