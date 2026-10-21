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

rm -rf "$dir/.theos" "$dir/packages"
make -C "$dir" FINALPACKAGE=1 >&2

dylib="$(find "$dir/.theos/obj" -maxdepth 2 -name '*.dylib' -print -quit)"
[ -n "$dylib" ] || { echo "build made no .dylib" >&2; exit 1; }
echo "$dylib"
