#!/bin/sh
# Fill app/src-tauri/resources with the pinned Python (python-build-standalone) and herdr-core (hc), each checked
# against its published SHA-256, plus Assieme itself installed into that Python. `--check` verifies an existing bundle.
set -eu

PY_TAG=20261020
PY_VERSION=3.13.16
PY_ASSET="cpython-${PY_VERSION}+${PY_TAG}-aarch64-apple-darwin-install_only.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}/${PY_ASSET}"
PY_SUMS="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}/SHA256SUMS"
HC_VERSION=0.1.18
HC_MANIFEST="https://releases.herdr.dev/core/${HC_VERSION}/manifest.json"

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
res="$repo/app/src-tauri/resources"

check() {
  "$res/python/bin/python3" -P -c 'import assieme, tomllib, assieme.mcpserver' || { echo "bundle broken: Python or assieme"; exit 1; }
  "$res/hc-bin/hc" --version | grep -q "$HC_VERSION" || { echo "bundle broken: hc is not $HC_VERSION"; exit 1; }
  echo "bundle ok"
}
[ "${1:-}" = "--check" ] && { check; exit 0; }

[ "$(uname -s)-$(uname -m)" = "Darwin-arm64" ] || { echo "bundle.sh builds for macOS on Apple silicon only"; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

echo "Python ${PY_VERSION}"
curl -fsSL "$PY_URL" -o "$tmp/py.tar.gz"
curl -fsSL "$PY_SUMS" -o "$tmp/SHA256SUMS"
want=$(grep " ${PY_ASSET}\$" "$tmp/SHA256SUMS" | cut -d' ' -f1)
[ -n "$want" ] && [ "$(shasum -a 256 "$tmp/py.tar.gz" | cut -d' ' -f1)" = "$want" ] || { echo "Python checksum mismatch"; exit 1; }

echo "hc ${HC_VERSION}"
curl -fsSL "$HC_MANIFEST" -o "$tmp/hc.json"
hc_url=$(/usr/bin/python3 -c 'import json,sys; t=json.load(open(sys.argv[1]))["targets"]["macos-aarch64"]; print(t["url"])' "$tmp/hc.json")
hc_sum=$(/usr/bin/python3 -c 'import json,sys; t=json.load(open(sys.argv[1]))["targets"]["macos-aarch64"]; print(t["sha256"])' "$tmp/hc.json")
curl -fsSL "$hc_url" -o "$tmp/hc.tar.gz"
[ "$(shasum -a 256 "$tmp/hc.tar.gz" | cut -d' ' -f1)" = "$hc_sum" ] || { echo "hc checksum mismatch"; exit 1; }

rm -rf "$res" && mkdir -p "$res/hc" "$res/hc-bin"
tar -xzf "$tmp/py.tar.gz" -C "$res"          # -> resources/python
tar -xzf "$tmp/hc.tar.gz" -C "$res/hc"
bin=$(find "$res/hc" -type f -name herdr-core -perm -u+x | head -1)
[ -n "$bin" ] || { echo "hc archive has no herdr-core binary"; exit 1; }
# A small launcher named hc: Tauri resources do not keep symlinks.
printf '#!/bin/sh\nexec "$(dirname "$0")/../hc/%s" "$@"\n' "${bin#"$res/hc/"}" > "$res/hc-bin/hc"
chmod +x "$res/hc-bin/hc"

echo "Assieme"
PIP_DISABLE_PIP_VERSION_CHECK=1 "$res/python/bin/python3" -m pip install --quiet --no-warn-script-location "$repo"
check
