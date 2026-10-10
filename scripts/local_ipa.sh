#!/usr/bin/env bash
# Build a mod's .ipa on Linux or WSL, without GitHub Actions. The result is unsigned:
# sign it with Sideloadly, AltStore/SideStore or Feather before installing.
#
# Usage: scripts/local_ipa.sh [--sign] <mod> [base.ipa or https URL] [output folder]
#   needs THEOS (default ~/theos); installs cyan into ~/.cyan the first time.
#   --sign also signs the result with scripts/sign.sh (your own certificate).
set -euo pipefail

sign=0
if [ "${1:-}" = "--sign" ]; then sign=1; shift; fi
mod="${1:?usage: scripts/local_ipa.sh [--sign] <mod> [base.ipa|url] [outdir]}"
root="$(cd "$(dirname "$0")/.." && pwd)"
meta="$root/mods/$mod/mod.json"
[ -f "$meta" ] || { echo "no mods/$mod/mod.json" >&2; exit 1; }
export THEOS="${THEOS:-$HOME/theos}"
CYAN_VERSION=v1.4.4
field() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); v=d
for k in sys.argv[2].split("."): v=(v or {}).get(k)
print(v or "")' "$meta" "$1"; }

# cyan, pinned, in its own environment so it never touches the system Python.
if [ ! -x "$HOME/.cyan/bin/cyan" ]; then
    echo "==> Installing cyan $CYAN_VERSION into ~/.cyan"
    python3 -m venv --without-pip "$HOME/.cyan"
    curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$HOME/.cyan/bin/python" - --quiet
    "$HOME/.cyan/bin/pip" install --quiet "https://github.com/asdfzxcvbn/pyzule-rw/archive/refs/tags/$CYAN_VERSION.zip"
fi

echo "==> Building $mod"
dylib="$("$root/scripts/build.sh" "$mod")"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
base="${2:-$(field base.ipa_url)}"
case "$base" in
    https://*) echo "==> Downloading $base"; curl -fsSL --retry 3 -o "$work/base.ipa" "$base" ;;
    "") echo "no base .ipa: pass one, or set base.ipa_url in mod.json" >&2; exit 1 ;;
    *) cp "$base" "$work/base.ipa" ;;
esac

# Same checks as the workflow, without macOS tools: right app, not FairPlay-encrypted.
# Prints the app version on stdout; everything else goes to stderr.
version="$(python3 - "$work/base.ipa" "$(field target_bundle_id)" <<'PY'
import plistlib, re, struct, sys, zipfile
ipa, want = sys.argv[1], sys.argv[2]
z = zipfile.ZipFile(ipa)
plists = [n for n in z.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)]
if not plists:
    sys.exit("base .ipa has no Payload/*.app")
info = plistlib.loads(z.read(plists[0]))
if info.get("CFBundleIdentifier") != want:
    sys.exit(f"base .ipa is {info.get('CFBundleIdentifier')}, but this mod targets {want}")
binary = z.read(plists[0].rsplit("/", 1)[0] + "/" + info["CFBundleExecutable"])
# LC_ENCRYPTION_INFO(_64) = 0x21 / 0x2C; cryptid is the 5th uint32 of the command.
for cmd in (0x21, 0x2C):
    for m in re.finditer(re.escape(struct.pack("<II", cmd, 20 if cmd == 0x21 else 24)), binary):
        if struct.unpack_from("<I", binary, m.start() + 16)[0] == 1:
            sys.exit("base .ipa is FairPlay-encrypted; use a decrypted one")
name = info.get("CFBundleDisplayName") or info.get("CFBundleName")
print(f"==> Base: {name} {info.get('CFBundleShortVersionString')}", file=sys.stderr)
print(info.get("CFBundleShortVersionString") or "0")
PY
)"
flags=(-u -w)
case "$(field remove_extensions)" in all) flags+=(-e) ;; *) flags+=(-g) ;; esac

out="${3:-$root}/${mod}_${version}_accretion.ipa"
echo "==> Injecting into $out"
"$HOME/.cyan/bin/cyan" -i "$work/base.ipa" -o "$out" -f "$dylib" \
    -n "$(field display_name)" -b "$(field bundle_id)" "${flags[@]}" --overwrite
echo "==> Built: $out (unsigned)"
if [ "$sign" = 1 ]; then
    "$root/scripts/sign.sh" "$out" "${out%.ipa}_signed.ipa"
fi
