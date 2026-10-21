#!/usr/bin/env bash
# Install Il2CppDumper (MIT) with support for Unity 6 metadata (v35, v38, v39).
#
# Perfare's last release, 6.7.46, stops at metadata v31 and rejects Unity 6000.3 games
# ("not a supported version[39]"). This builds a pinned commit of the Doppelglower fork, which
# reads v35-v39 and writes the same dump.cs that scripts/il2cpp_dump.py reads.
#
# Needs git and the .NET SDK (any 6.0 or newer SDK builds the net6.0 target; running the result
# needs the .NET 6 runtime, or build with -f net8.0 if that runtime is installed instead).
# Works in Linux, WSL, macOS and Git Bash on Windows.
#
#   scripts/install_il2cppdumper.sh [install-dir]      default: ~/.local/il2cppdumper
#
# Then:  dotnet <install-dir>/Il2CppDumper.dll <UnityFramework> <global-metadata.dat> <out-dir>
set -euo pipefail

REPO=https://github.com/Doppelglower/Il2CppDumper.git
COMMIT=be81a7bdf64a0f9f8f35628089c2368b2617db07   # v1.0.1.0, 2026-09-19
FRAMEWORK="${IL2CPPDUMPER_FRAMEWORK:-net6.0}"

dest="${1:-$HOME/.local/il2cppdumper}"
command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
command -v dotnet >/dev/null || { echo "the .NET SDK is required (https://dotnet.microsoft.com/download)" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
git -c advice.detachedHead=false clone -q "$REPO" "$work/src"
git -C "$work/src" checkout -q "$COMMIT"
[ "$(git -C "$work/src" rev-parse HEAD)" = "$COMMIT" ] || { echo "checkout did not land on $COMMIT" >&2; exit 1; }

dotnet publish "$work/src/Il2CppDumper/Il2CppDumper.csproj" -c Release -f "$FRAMEWORK" -o "$work/out" --nologo -v quiet
[ -f "$work/out/Il2CppDumper.dll" ] || { echo "build produced no Il2CppDumper.dll" >&2; exit 1; }

mkdir -p "$dest"
rm -rf "$dest"/*
cp -R "$work/out"/. "$dest"/
echo "$COMMIT" > "$dest/COMMIT"
echo "Installed Il2CppDumper ($COMMIT, $FRAMEWORK) to $dest"
echo "Run: dotnet $dest/Il2CppDumper.dll <UnityFramework> <global-metadata.dat> <out-dir>"
