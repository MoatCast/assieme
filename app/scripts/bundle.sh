#!/bin/sh
# Fill app/src-tauri/resources with Assieme's runtime: `asm` (the runtime and the brain) built from runtime/ in its
# ad-hoc signed bundle, with the Linux builds `machine add` copies. `--check` verifies an existing bundle.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
res="$repo/app/src-tauri/resources"
ASM_VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$repo/runtime/Cargo.toml" | head -1)

check() {
  "$res/asm-bin/asm" --version | grep -q "$ASM_VERSION" || { echo "bundle broken: asm is not $ASM_VERSION"; exit 1; }
  "$res/asm-bin/asm" brain --check || { echo "bundle broken: the brain does not start"; exit 1; }
  "$res/asm-bin/asm" cli --help > /dev/null || { echo "bundle broken: asm cli"; exit 1; }
  echo "bundle ok"
}
build_core() {
  # Cargo is incremental, so this is quick once built. release.sh signs the bundle again with the Developer ID.
  rt="$repo/runtime"
  zig="${ZIG:-$HOME/.local/share/assieme-dev/zig-0.16.0/zig}"
  (cd "$rt" && ZIG="$zig" cargo build --locked --release -p asm && ZIG="$zig" just linux)
  rm -rf "$res/asm" "$res/asm-bin" && mkdir -p "$res/asm" "$res/asm-bin"
  core="$res/asm/Assieme Core.app"
  sh "$rt/scripts/mac-bundle" "$rt/target/release/asm" "$core"
  for arch in aarch64 x86_64; do
    mkdir -p "$core/Contents/Resources/linux/$arch"
    cp "$rt/target/$arch-unknown-linux-musl/release/asm" "$core/Contents/Resources/linux/$arch/assieme-core"
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
[ "$(uname -s)-$(uname -m)" = "Darwin-arm64" ] || { echo "bundle.sh builds for macOS on Apple silicon only"; exit 1; }
echo "asm ${ASM_VERSION}"
build_core
check
