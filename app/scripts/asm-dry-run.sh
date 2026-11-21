#!/bin/sh
# Sign and notarise the `asm` program alone, as release.sh will sign Assieme's runtime, to surface signing problems
# early. Everything stays in a scratch folder (printed at the end); nothing is published or put in the app.
# Needs what release.sh needs: a "Developer ID Application" certificate and the notarytool profile "assieme-notary".
set -eu

here=$(cd "$(dirname "$0")" && pwd)
rt=$(cd "$here/../../runtime" && pwd)
profile=assieme-notary
identity=$(security find-identity -v -p codesigning | sed -n 's/^ *[0-9]*) \([0-9A-F]\{40\}\) "Developer ID Application: .*/\1/p' | head -1)
[ -n "$identity" ] || { echo "No Developer ID Application certificate in the keychain"; exit 1; }
out=$(mktemp -d "${TMPDIR:-/var/tmp}/asm-dry-run.XXXXXX")
core="$out/Assieme Core.app"

echo "== Building asm"
(cd "$rt" && ZIG="${ZIG:-$HOME/.local/share/assieme-dev/zig-0.16.0/zig}" cargo build --locked --release -p asm)
sh "$rt/scripts/mac-bundle" "$rt/target/release/asm" "$core"

echo "== Signing as $identity"
for target in "$core/Contents/MacOS/assieme-core" "$core"; do
  codesign --force --timestamp --options runtime --identifier dev.assieme.core \
    --entitlements "$rt/distribution/macos-entitlements.plist" --sign "$identity" "$target"
done
codesign --verify --deep --strict "$core"
codesign -d --entitlements - "$core" 2>&1 | grep -q com.apple.security.cs.allow-dyld-environment-variables \
  || { echo "asm lost its entitlements"; exit 1; }
codesign -dv "$core" 2>&1 | grep -q '^TeamIdentifier=P2B76CVJ4Y$' || { echo "asm is not signed by team P2B76CVJ4Y"; exit 1; }
codesign -dv "$core" 2>&1 | grep -q '^Identifier=dev.assieme.core$' || { echo "asm is not dev.assieme.core"; exit 1; }
"$core/Contents/MacOS/assieme-core" --version
"$core/Contents/MacOS/assieme-core" brain --check || { echo "the signed asm cannot run its brain"; exit 1; }

echo "== Notarising (Apple checks it; nothing is published)"
ditto -c -k --keepParent "$core" "$out/asm.zip"
xcrun notarytool submit "$out/asm.zip" --keychain-profile "$profile" --wait --output-format json > "$out/notary.json"
/usr/bin/python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); print("notarisation:", s.get("status"), s.get("id")); sys.exit(0 if s.get("status")=="Accepted" else 1)' "$out/notary.json" \
  || { xcrun notarytool log "$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$out/notary.json")" --keychain-profile "$profile"; exit 1; }
xcrun stapler staple "$core"
spctl --assess --type execute --verbose=2 "$core"
echo "== Dry run passed: $out"
