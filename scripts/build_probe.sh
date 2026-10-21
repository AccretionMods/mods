#!/usr/bin/env bash
# Build the IL2CPP runtime probe (runtime/probe/) for a real device and print the path of its .dylib.
# Usage: scripts/build_probe.sh     (needs $THEOS; the "Mods: runtime probe" workflow runs it)
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
dir="$root/runtime/probe"

# Same steps scripts/build.sh takes for a mod with a hooks.json.
rm -rf "$dir/runtime"
mkdir "$dir/runtime"
cp "$root"/runtime/*.c "$root"/runtime/*.h "$root"/runtime/*.m "$dir/runtime/"
python3 "$root/scripts/embed_hooks.py" "$dir/hooks.json" "$dir/runtime/am_hooks_data.c" >&2

rm -rf "$dir/.theos" "$dir/packages"
make -C "$dir" FINALPACKAGE=1 >&2

dylib="$(find "$dir/.theos/obj" -maxdepth 2 -name 'AMIl2cppProbe.dylib' -print -quit)"
[ -n "$dylib" ] || { echo "build made no .dylib" >&2; exit 1; }
echo "$dylib"
