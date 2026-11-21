#!/bin/sh
# Fill app/src-tauri/resources with the pinned Python (python-build-standalone), checked against its published SHA-256,
# Assieme's runtime (asm) built from runtime/ in its ad-hoc signed bundle with the Linux builds `machine add` copies,
# plus Assieme itself installed into that Python. `--check` verifies an existing bundle.
set -eu

PY_TAG=20261020
PY_VERSION=3.13.16
PY_ASSET="cpython-${PY_VERSION}+${PY_TAG}-aarch64-apple-darwin-install_only.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}/${PY_ASSET}"
PY_SUMS="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}/SHA256SUMS"

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
res="$repo/app/src-tauri/resources"
ASM_VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$repo/runtime/Cargo.toml" | head -1)

check() {
  "$res/python/bin/python3" -P -c 'import assieme, tomllib, assieme.mcpserver' || { echo "bundle broken: Python or assieme"; exit 1; }
  "$res/asm-bin/asm" --version | grep -q "$ASM_VERSION" || { echo "bundle broken: asm is not $ASM_VERSION"; exit 1; }
  [ -z "$(diff -rq -x __pycache__ "$repo/assieme" "$res/python/lib/python3.13/site-packages/assieme")" ] || { echo "bundle stale: its Assieme differs from this checkout"; exit 1; }
  echo "bundle ok"
}
install_assieme() {
  # Deps first, then Assieme over itself: pip would otherwise keep an installed copy with the same version number.
  PIP_DISABLE_PIP_VERSION_CHECK=1 "$res/python/bin/python3" -m pip install --quiet --no-warn-script-location "$repo"
  rm -rf "$repo/build"  # setuptools would otherwise reuse deleted files from an earlier build
  PIP_DISABLE_PIP_VERSION_CHECK=1 "$res/python/bin/python3" -m pip install --quiet --no-warn-script-location --no-deps --force-reinstall "$repo"
}
build_core() {
  # Cargo is incremental, so this is quick once built. release.sh signs the bundle again with the Developer ID.
  rt="$repo/runtime"
  zig="${ZIG:-$HOME/.local/share/assieme-dev/zig-0.16.0/zig}"
  (cd "$rt" && ZIG="$zig" cargo build --locked --release -p assieme-core && ZIG="$zig" just linux)
  rm -rf "$res/asm" "$res/asm-bin" && mkdir -p "$res/asm" "$res/asm-bin"
  core="$res/asm/Assieme Core.app"
  sh "$rt/scripts/mac-bundle" "$rt/target/release/assieme-core" "$core"
  for arch in aarch64 x86_64; do
    mkdir -p "$core/Contents/Resources/linux/$arch"
    cp "$rt/target/$arch-unknown-linux-musl/release/assieme-core" "$core/Contents/Resources/linux/$arch/"
  done
  APPLE_CERTIFICATE_P12='' APPLE_CERTIFICATE_PASSWORD='' bash "$rt/scripts/mac-sign" "$core"
  cp "$rt/LICENSE" "$rt/NOTICE" "$rt/crates/assieme-core/THIRD-PARTY-NOTICES" "$res/asm/"
  # Launchers: Tauri resources do not keep symlinks.
  for name in asm assieme-core; do
    printf '#!/bin/sh\nexec "$(dirname "$0")/../asm/Assieme Core.app/Contents/MacOS/assieme-core" "$@"\n' > "$res/asm-bin/$name"
    chmod +x "$res/asm-bin/$name"
  done
}
[ "${1:-}" = "--check" ] && { check; exit 0; }
# Every `tauri build`: asm and Assieme, on top of what's there; the first one downloads Python too.
[ "${1:-}" = "--refresh" ] && [ -x "$res/python/bin/python3" ] && [ -x "$res/asm-bin/asm" ] && {
  echo "asm ${ASM_VERSION}"; build_core; echo "Assieme"; install_assieme; check; exit 0; }

[ "$(uname -s)-$(uname -m)" = "Darwin-arm64" ] || { echo "bundle.sh builds for macOS on Apple silicon only"; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

echo "Python ${PY_VERSION}"
curl -fsSL "$PY_URL" -o "$tmp/py.tar.gz"
curl -fsSL "$PY_SUMS" -o "$tmp/SHA256SUMS"
want=$(grep " ${PY_ASSET}\$" "$tmp/SHA256SUMS" | cut -d' ' -f1)
[ -n "$want" ] && [ "$(shasum -a 256 "$tmp/py.tar.gz" | cut -d' ' -f1)" = "$want" ] || { echo "Python checksum mismatch"; exit 1; }

rm -rf "$res" && mkdir -p "$res"
tar -xzf "$tmp/py.tar.gz" -C "$res"          # -> resources/python

echo "asm ${ASM_VERSION}"
build_core

echo "Assieme"
install_assieme
check
