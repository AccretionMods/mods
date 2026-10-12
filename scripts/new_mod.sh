#!/usr/bin/env bash
# Start a new mod from the template: downloads the base .ipa once to read its bundle ID, name and
# version and to record its sha256, then writes mods/<name>/ ready to edit and build.
#
# Usage: scripts/new_mod.sh <name> <https link to the base .ipa> [category]
#   name: lowercase letters, digits and dashes, like  my-app-plus
#   category: games, entertainment, utilities, photo-video, social, lifestyle, developer, other
# Then: edit mods/<name>/Tweak.x and mod.json (description, icon_url), and build it locally
# (scripts/local_ipa.sh <name>) or with Actions -> Build mod.
set -euo pipefail

name="${1:?usage: scripts/new_mod.sh <name> <base .ipa https link> [category]}"
url="${2:?usage: scripts/new_mod.sh <name> <base .ipa https link> [category]}"
category="${3:-other}"
root="$(cd "$(dirname "$0")/.." && pwd)"
dir="$root/mods/$name"

[[ "$name" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || { echo "name must be lowercase letters, digits and dashes" >&2; exit 1; }
[[ "$url" == https://* ]] || { echo "the base .ipa link must be https" >&2; exit 1; }
[ ! -e "$dir" ] || { echo "mods/$name already exists" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
echo "==> Downloading the base .ipa"
curl -fsSL --retry 3 -o "$work/base.ipa" "$url"

# bundle ID, display name, version, sha256 of the base app
read -r target app_name app_version sha < <(python3 - "$work/base.ipa" <<'PY'
import hashlib, plistlib, re, sys, zipfile
path = sys.argv[1]
z = zipfile.ZipFile(path)
plists = [n for n in z.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)]
if not plists:
    sys.exit("that file has no Payload/*.app: is it an .ipa?")
info = plistlib.loads(z.read(plists[0]))
name = (info.get("CFBundleDisplayName") or info.get("CFBundleName") or "App").replace(" ", "_")
sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
print(info["CFBundleIdentifier"], name, info.get("CFBundleShortVersionString") or "1.0", sha)
PY
)
app_name="${app_name//_/ }"
# TweakName: CamelCase of the folder name; bundle ID: io.github.accretionmods.<name without dashes>
tweak="$(python3 -c 'import sys; print("".join(p.capitalize() for p in sys.argv[1].split("-")))' "$name")"
bundle="io.github.accretionmods.${name//-/}"

mkdir -p "$dir"
cat > "$dir/Makefile" <<EOF
TARGET := iphone:clang:latest:15.0
ARCHS = arm64

include \$(THEOS)/makefiles/common.mk

TWEAK_NAME = $tweak

# menu/ is copied in from the repo root by scripts/build.sh before each build.
${tweak}_FILES = Tweak.x menu/AMMenu.m
${tweak}_CFLAGS = -fobjc-arc
${tweak}_FRAMEWORKS = UIKit

include \$(THEOS_MAKE_PATH)/tweak.mk
EOF

cat > "$dir/control" <<EOF
Package: io.github.accretionmods.$name
Name: $app_name+
Version: 1.0.0
Architecture: iphoneos-arm
Description: Accretion mod for $app_name.
Maintainer: Accretion
Author: Accretion
Section: Tweaks
Depends: mobilesubstrate
EOF

echo "{ Filter = { Bundles = ( \"$target\" ); }; }" > "$dir/$tweak.plist"

cat > "$dir/Tweak.x" <<EOF
// $app_name+: an Accretion mod. Add switches and sliders to the menu in %ctor, read them anywhere
// with [AMMenu boolForKey:] / [AMMenu floatForKey:], and hook what you need with %hook.

#import <UIKit/UIKit.h>
#import "menu/AMMenu.h"

static void applyKeepAwake(void) {
    UIApplication.sharedApplication.idleTimerDisabled = [AMMenu boolForKey:@"keepAwake"];
}

%ctor {
    AMMenu *menu = AMMenu.shared;
    menu.title = @"$app_name+";
    [menu addSwitch:@"keepAwake" title:@"Keep screen awake" defaultValue:NO onChange:^(BOOL on) {
        applyKeepAwake();
    }];
    [NSNotificationCenter.defaultCenter addObserverForName:UIApplicationDidBecomeActiveNotification
                                                    object:nil
                                                     queue:NSOperationQueue.mainQueue
                                                usingBlock:^(NSNotification *note) { applyKeepAwake(); }];
    [menu install];
}
EOF

python3 - "$dir/mod.json" "$app_name" "$target" "$bundle" "$app_version" "$category" "$url" "$sha" <<'PY'
import json, sys
path, app, target, bundle, version, category, url, sha = sys.argv[1:]
meta = {
    "name": f"{app}+",
    "version": "1.0.0",
    "kind": "tweak",
    "category": category,
    "subtitle": f"{app} with an Accretion mod menu.",
    "description": f"{app} with the Accretion mod menu built in.\n\nMod menu (tap the floating button):\n• Keep screen awake\n\nInstalls next to the original app.",
    "target_bundle_id": target,
    "bundle_id": bundle,
    "display_name": f"{app}+",
    "tested_app_version": version,
    "min_os": "15.0",
    "remove_extensions": "encrypted",
    "icon_url": "https://getaccretion.github.io/assets/icon.png",
    "screenshots": [],
    "base": {"open_source": True, "license": "", "repo": "", "ipa_url": url, "sha256": sha},
}
with open(path, "w", encoding="utf-8", newline="\n") as f:
    json.dump(meta, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY

python3 "$root/scripts/validate_mods.py" "$name"
echo "==> Made mods/$name for $app_name $app_version ($target)"
echo "    Next: edit Tweak.x, and in mod.json the description, icon_url, base.repo and base.license."
echo "    Build: scripts/local_ipa.sh $name   or   Actions -> Build mod -> $name"
