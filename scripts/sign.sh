#!/usr/bin/env bash
# Sign an .ipa with your own certificate, for your own devices.
#
# Usage: scripts/sign.sh app.ipa [signed.ipa]
#
# Reads three files from ~/.accretion-signing (override with SIGNING_DIR). Keep that folder
# inside Linux/WSL, never in this repo or a synced folder:
#   cert.p12                 your certificate and private key
#   profile.mobileprovision  a profile that includes your device(s)
#   password                 the .p12 password on one line (chmod 600)
set -euo pipefail

in="${1:?usage: scripts/sign.sh app.ipa [signed.ipa]}"
out="${2:-${in%.ipa}_signed.ipa}"
dir="${SIGNING_DIR:-$HOME/.accretion-signing}"
zsign="$(command -v zsign || echo "$HOME/.local/bin/zsign")"

[ -x "$zsign" ] || { echo "zsign not found: run scripts/install_zsign.sh" >&2; exit 1; }
for f in cert.p12 profile.mobileprovision password; do
    [ -f "$dir/$f" ] || { echo "missing $dir/$f (see the top of this script)" >&2; exit 1; }
done
if [ -n "$(find "$dir/password" -perm /077)" ]; then
    echo "warning: $dir/password is readable by other users; run: chmod 600 $dir/password" >&2
fi

# Show what the profile allows and pick the bundle ID to sign with.
bundle_id="$(python3 - "$dir/profile.mobileprovision" "$in" <<'PY'
import plistlib, re, sys, zipfile
from datetime import datetime, timezone
raw = open(sys.argv[1], "rb").read()
start, end = raw.find(b"<?xml"), raw.find(b"</plist>")
if start < 0 or end < 0:
    sys.exit("profile.mobileprovision is not a provisioning profile")
p = plistlib.loads(raw[start:end + len(b"</plist>")])
team = (p.get("TeamIdentifier") or ["?"])[0]
app_id = p["Entitlements"]["application-identifier"].split(".", 1)[1]
expires = p["ExpirationDate"].replace(tzinfo=timezone.utc)
days = (expires - datetime.now(timezone.utc)).days
kind = "all devices (enterprise)" if p.get("ProvisionsAllDevices") else f"{len(p.get('ProvisionedDevices', []))} devices"
print(f"==> Profile: {p.get('Name')} | team {team} | app ID {app_id} | {kind} | expires in {days} days", file=sys.stderr)
if days < 0:
    sys.exit("profile has expired")

z = zipfile.ZipFile(sys.argv[2])
plist = next(n for n in z.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n))
current = plistlib.loads(z.read(plist))["CFBundleIdentifier"]
if app_id == "*" or (app_id.endswith(".*") and current.startswith(app_id[:-1])):
    print(current)            # wildcard profile covers the app's own bundle ID
elif app_id.endswith("*"):
    sys.exit(f"profile only covers {app_id}, not {current}; change bundle_id in mod.json to match")
else:
    print(f"==> Signing as {app_id} (the profile's explicit app ID)", file=sys.stderr)
    print(app_id)
PY
)"

"$zsign" -k "$dir/cert.p12" -p "$(cat "$dir/password")" -m "$dir/profile.mobileprovision" \
    -b "$bundle_id" -z 9 -o "$out" "$in"
echo "==> Signed: $out"
