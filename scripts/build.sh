#!/usr/bin/env bash
# Build one mod's tweak and print the path of the .dylib it made.
# Usage: scripts/build.sh <mod>     (needs $THEOS)
set -euo pipefail

mod="${1:?usage: scripts/build.sh <mod>}"
root="$(cd "$(dirname "$0")/.." && pwd)"
dir="$root/mods/$mod"
[ -f "$dir/Makefile" ] || { echo "mods/$mod has no Makefile" >&2; exit 1; }

# Every mod compiles its own copy of the shared menu, so there is one .dylib to inject.
rm -rf "$dir/menu"
cp -R "$root/menu" "$dir/menu"

# A Unity game's mod also compiles the shared IL2CPP runtime, with its hooks.json as a C table.
rm -rf "$dir/runtime"
if [ -f "$dir/hooks.json" ]; then
    cp -R "$root/runtime" "$dir/runtime"
    python3 "$root/scripts/embed_hooks.py" "$dir/hooks.json" "$dir/runtime/am_hooks_data.c" >&2
fi

# A mod whose menu is declared in tweaks.json gets its AMTweaks.gen.h/.m regenerated (scripts/gen_tweaks.py).
if [ -f "$dir/tweaks.json" ]; then
    python3 "$root/scripts/gen_tweaks.py" "$dir" >&2
fi

# Game names a hand-written Tweak.x writes as AMS("Name") are encoded in a copy that is compiled instead
# (scripts/hide_strings.py, runtime/am_hide.h); the source keeps them plain.
files_override=()
rm -f "$dir/Tweak.hidden.x"
if [ -f "$dir/Tweak.x" ] && grep -q 'AMS("' "$dir/Tweak.x"; then
    python3 "$root/scripts/hide_strings.py" "$dir/Tweak.x" "$dir/Tweak.hidden.x" >&2
    name="$(sed -n 's/^TWEAK_NAME *= *//p' "$dir/Makefile" | head -1 | tr -d '[:space:]')"
    files="$(sed -n "s/^${name}_FILES *= *//p" "$dir/Makefile" | head -1)"
    files_override=("${name}_FILES=${files/Tweak.x/Tweak.hidden.x}")
fi

rm -rf "$dir/.theos" "$dir/packages"
# hidden visibility: the .dylib exports nothing (nothing links against it; the game only loads it)
cflags="-fvisibility=hidden"
# mod.json "branding": false: the menu is built without Accretion's branding: the "getaccretion.app ›" link, the
# mark by the title, "Made by Accretion." and the Accretion account: no Log in, no Cloud saves, no call to its
# server (menu/AMMenu.m); the switches and the Display settings stay
if python3 -c 'import json, sys; sys.exit(0 if json.load(open(sys.argv[1])).get("branding", True) is False else 1)' \
        "$dir/mod.json"; then
    cflags="$cflags -DAM_NO_BRANDING=1"
fi
# AM_HOOKS=off (dev.py build/run --no-hooks): a build that hooks nothing: no game-method hooks, no sideload
# compatibility rebinding, no Objective-C method swaps (runtime/AMIl2cpp.m, runtime/SideloadCompat.m); the menu,
# pins and pushed switches stay
if [ "${AM_HOOKS:-on}" = off ]; then
    cflags="$cflags -DAM_NO_HOOKS=1"
    echo "==> without hooks (AM_HOOKS=off)" >&2
fi
make -C "$dir" FINALPACKAGE=1 ADDITIONAL_CFLAGS="$cflags" "${files_override[@]}" >&2

dylib="$(find "$dir/.theos/obj" -maxdepth 2 -name '*.dylib' -print -quit)"
[ -n "$dylib" ] || { echo "build made no .dylib" >&2; exit 1; }
echo "$dylib"
