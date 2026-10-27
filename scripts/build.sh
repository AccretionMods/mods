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
if [ -f "$dir/detection.json" ] && [ ! -f "$dir/hooks.json" ]; then
    cp -R "$root/runtime" "$dir/runtime"
fi
if [ ! -d "$dir/runtime" ] && grep -q 'SideloadCompat.m' "$dir/Makefile"; then
    cp -R "$root/runtime" "$dir/runtime"
fi
if [ -d "$dir/runtime" ]; then
    python3 "$root/scripts/detection_profile.py" "$dir" >&2
fi

# A mod whose menu is declared in tweaks.json gets its AMTweaks.gen.h/.m regenerated (scripts/gen_tweaks.py).
if [ -f "$dir/tweaks.json" ]; then
    python3 "$root/scripts/gen_tweaks.py" "$dir" >&2
fi

# Game names a hand-written Tweak.x writes as AMS("Name") are encoded in a copy that is compiled instead
# (scripts/hide_strings.py, runtime/am_hide.h); the source keeps them plain.
files_override=()
name="$(sed -n -e 's/^TWEAK_NAME *= *//p' -e 's/^LIBRARY_NAME *= *//p' "$dir/Makefile" | head -1 | tr -d '[:space:]')"
files="$(sed -n "s/^${name}_FILES *= *//p" "$dir/Makefile" | head -1)"
original_files="$files"
files="$(python3 "$root/scripts/detection_profile.py" --build-sources "$dir" "$files")"
rm -f "$dir/Tweak.hidden.x"
if [ -f "$dir/Tweak.x" ] && grep -q 'AMS("' "$dir/Tweak.x"; then
    python3 "$root/scripts/hide_strings.py" "$dir/Tweak.x" "$dir/Tweak.hidden.x" >&2
    files="${files/Tweak.x/Tweak.hidden.x}"
fi
if [ -n "$name" ] && [ "$files" != "$original_files" ]; then
    files_override=("${name}_FILES=$files")
fi

rm -rf "$dir/.theos" "$dir/packages"
# hidden visibility: the .dylib exports nothing (nothing links against it; the game only loads it)
make -C "$dir" FINALPACKAGE=1 ADDITIONAL_CFLAGS=-fvisibility=hidden "${files_override[@]}" >&2

dylib="$(find "$dir/.theos/obj" -maxdepth 2 -name '*.dylib' -print -quit)"
[ -n "$dylib" ] || { echo "build made no .dylib" >&2; exit 1; }
echo "$dylib"
