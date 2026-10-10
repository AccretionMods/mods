#!/usr/bin/env bash
# Check that an .ipa can be modded and installed, and print its details as key=value lines
# (ready to append to $GITHUB_OUTPUT). macOS only: uses otool and PlistBuddy.
# Usage: scripts/check_ipa.sh app.ipa
set -euo pipefail

ipa="${1:?usage: scripts/check_ipa.sh app.ipa}"
fail() { echo "::error::$ipa: $*" >&2; exit 1; }

unzip -tq "$ipa" >/dev/null 2>&1 || fail "not a valid zip"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
unzip -q "$ipa" 'Payload/*' -d "$tmp" || fail "has no Payload folder"

app="$(find "$tmp/Payload" -maxdepth 1 -name '*.app' -type d -print -quit)"
[ -n "$app" ] || fail "has no Payload/*.app"

plist() { /usr/libexec/PlistBuddy -c "Print :$1" "$app/Info.plist" 2>/dev/null || true; }
exe="$(plist CFBundleExecutable)"
[ -n "$exe" ] && [ -f "$app/$exe" ] || fail "main executable is missing"

# FairPlay-encrypted binaries show cryptid 1 in LC_ENCRYPTION_INFO; unencrypted ones show 0 or no such command.
# Capture first: with pipefail, grep -q exiting early could make the pipe report failure.
loads="$(otool -arch all -l "$app/$exe")" || fail "otool could not read the main binary"
if grep -Eq 'cryptid +1' <<<"$loads"; then
    fail "main binary is FairPlay-encrypted (cryptid 1); use a decrypted .ipa"
fi

echo "bundle_id=$(plist CFBundleIdentifier)"
echo "name=$(plist CFBundleDisplayName)"
echo "version=$(plist CFBundleShortVersionString)"
echo "min_os=$(plist MinimumOSVersion)"
echo "executable=$exe"
echo "links=$(otool -L "$app/$exe" | tail -n +2 | awk '{print $1}' | tr '\n' ' ')"
