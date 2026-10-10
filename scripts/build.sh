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

rm -rf "$dir/.theos" "$dir/packages"
make -C "$dir" FINALPACKAGE=1 >&2

dylib="$(find "$dir/.theos/obj" -maxdepth 2 -name '*.dylib' -print -quit)"
[ -n "$dylib" ] || { echo "build made no .dylib" >&2; exit 1; }
echo "$dylib"
