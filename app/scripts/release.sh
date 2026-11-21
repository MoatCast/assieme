#!/bin/sh
# Build a signed, notarised Assieme into app/release/<version>/: the .dmg people download, and the updater's
# Assieme.app.tar.gz, its .sig and latest.json. Publishing them is a separate, deliberate step.
# Needs on this Mac: a "Developer ID Application" certificate in the keychain, the notarytool profile
# "assieme-notary" and the updater key at ~/.tauri/assieme-updater.key. This script names them; it never reads them.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
app=$(cd "$here/.." && pwd)
res="$app/src-tauri/resources"
key="$HOME/.tauri/assieme-updater.key"
profile=assieme-notary
version=$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$app/src-tauri/tauri.conf.json")
# By its SHA-1 fingerprint: a name with accents doesn't round-trip through `security`'s output.
identity=$(security find-identity -v -p codesigning | sed -n 's/^ *[0-9]*) \([0-9A-F]\{40\}\) "Developer ID Application: .*/\1/p' | head -1)
[ -n "$identity" ] || { echo "No Developer ID Application certificate in the keychain"; exit 1; }
[ -f "$key" ] || { echo "No updater key at $key"; exit 1; }
out="$app/release/$version"
rm -rf "$out" && mkdir -p "$out"

notarise() {  # Apple's answer must be Accepted; notarytool exits 0 for Invalid too
  xcrun notarytool submit "$1" --keychain-profile "$profile" --wait --output-format json > "$out/notary.json"
  /usr/bin/python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); sys.exit(0 if s.get("status")=="Accepted" else "notarisation: "+str(s))' "$out/notary.json"
}

echo "== Assieme $version, signed as $identity"
sh "$here/bundle.sh" --refresh
echo "== Signing the runtime"
# Assieme's runtime is signed as a bundle, inside out, with its entitlements: `update --from` and Mac remote
# installs check this signature (team P2B76CVJ4Y, dev.assieme.core).
core="$res/asm/Assieme Core.app"
for target in "$core/Contents/MacOS/assieme-core" "$core"; do
  codesign --force --timestamp --options runtime --identifier dev.assieme.core \
    --entitlements "$app/../runtime/distribution/macos-entitlements.plist" --sign "$identity" "$target"
done
echo "== Building the app"
(cd "$app" && APPLE_SIGNING_IDENTITY="$identity" npm run tauri build -- --bundles app \
  --config '{"build":{"beforeBuildCommand":"npm run build"},"bundle":{"macOS":{"signingIdentity":null}}}')
built="$app/src-tauri/target/release/bundle/macos/Assieme.app"
codesign --verify --deep --strict "$built"
built_core="$built/Contents/Resources/asm/Assieme Core.app"
codesign -d --entitlements - "$built_core" 2>&1 | grep -q com.apple.security.cs.allow-dyld-environment-variables \
  || { echo "the core lost its entitlements"; exit 1; }
codesign -dv "$built_core" 2>&1 | grep -q '^TeamIdentifier=P2B76CVJ4Y$' || { echo "the core is not signed by team P2B76CVJ4Y"; exit 1; }

echo "== Notarising the app"
ditto -c -k --keepParent "$built" "$out/notarise.zip"
notarise "$out/notarise.zip"
rm "$out/notarise.zip"
xcrun stapler staple "$built"
spctl -a -vv "$built"

echo "== Update archive"
COPYFILE_DISABLE=1 tar -C "$(dirname "$built")" -czf "$out/Assieme.app.tar.gz" Assieme.app
(cd "$app" && npx tauri signer sign -f "$key" -p "" "$out/Assieme.app.tar.gz" > /dev/null)
/usr/bin/python3 - "$out" "$version" <<'EOF'
import datetime, json, sys
out, version = sys.argv[1:]
json.dump({'version': version, 'notes': f'Assieme {version}',
           'pub_date': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'platforms': {'darwin-aarch64': {
               'signature': open(f'{out}/Assieme.app.tar.gz.sig').read(),
               'url': f'https://github.com/MoatCast/assieme/releases/download/v{version}/Assieme.app.tar.gz'}}},
          open(f'{out}/latest.json', 'w'), indent=1)
EOF

echo "== Disk image"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
ditto "$built" "$stage/Assieme.app"
ln -s /Applications "$stage/Applications"
dmg="$out/Assieme-$version.dmg"
hdiutil create -quiet -volname Assieme -srcfolder "$stage" -ov -format UDZO "$dmg"
codesign --timestamp --sign "$identity" "$dmg"
notarise "$dmg"
xcrun stapler staple "$dmg"
spctl -a -vv -t open --context context:primary-signature "$dmg"
rm "$out/notary.json"
echo "== Ready in $out"
ls -1 "$out"
