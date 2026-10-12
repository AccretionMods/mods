#!/usr/bin/env bash
# Build a mod's .ipa on Linux, WSL or macOS (also what the "Build mod" workflow runs). The result is
# unsigned: sign it with Sideloadly, AltStore/SideStore or Feather before installing.
#
# Usage: scripts/local_ipa.sh [--sign] <mod> [base.ipa or https URL] [output folder]
#   needs THEOS (default ~/theos); installs cyan into ~/.cyan the first time.
#   --sign also signs the result with scripts/sign.sh (your own certificate).
#   REQUIRE_BASE_SHA256=1 refuses a base .ipa unless mod.json's base.sha256 matches (the workflow sets it).
#
# The .ipa's version (CFBundleShortVersionString) becomes "<app version>-mod<mod version>", like
# 4.0.0-mod1.1.0: the version it's published under, so installed copies match the store.
set -euo pipefail

sign=0
if [ "${1:-}" = "--sign" ]; then sign=1; shift; fi
mod="${1:?usage: scripts/local_ipa.sh [--sign] <mod> [base.ipa|url] [outdir]}"
root="$(cd "$(dirname "$0")/.." && pwd)"
meta="$root/mods/$mod/mod.json"
[ -f "$meta" ] || { echo "no mods/$mod/mod.json" >&2; exit 1; }
export THEOS="${THEOS:-$HOME/theos}"
# cyan v1.4.4, pinned to its exact commit (a tag or an archive can change; a commit can't)
CYAN_COMMIT=04a40f2aa65ec9cb36127f74d52ae4ef71377c50
field() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); v=d
for k in sys.argv[2].split("."): v=(v or {}).get(k)
print(v or "")' "$meta" "$1"; }

# cyan, in its own environment so it never touches the system Python.
if [ ! -x "$HOME/.cyan/bin/cyan" ]; then
    echo "==> Installing cyan ($CYAN_COMMIT) into ~/.cyan"
    if ! python3 -m venv "$HOME/.cyan" 2> /dev/null; then
        # Debian/Ubuntu without python3-venv's ensurepip
        rm -rf "$HOME/.cyan"
        python3 -m venv --without-pip "$HOME/.cyan"
        curl -fsSL https://bootstrap.pypa.io/get-pip.py | "$HOME/.cyan/bin/python" - --quiet
    fi
    "$HOME/.cyan/bin/pip" install --quiet "git+https://github.com/asdfzxcvbn/pyzule-rw@$CYAN_COMMIT"
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

# The base .ipa must be the exact file mod.json names (base.sha256), when it names one.
want_sha="$(field base.sha256)"
got_sha="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$work/base.ipa")"
if [ -n "$want_sha" ]; then
    [ "$got_sha" = "$want_sha" ] || { echo "base .ipa sha256 is $got_sha, but mod.json expects $want_sha" >&2; exit 1; }
    echo "==> Base checksum matches mod.json"
elif [ "${REQUIRE_BASE_SHA256:-0}" = 1 ]; then
    echo "mod.json has no base.sha256 (this base .ipa's is $got_sha); add it to build here" >&2
    exit 1
else
    echo "==> Note: mod.json has no base.sha256; this base .ipa's is $got_sha" >&2
fi

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
published="${version}-mod$(field version)"
flags=(-u -w)
case "$(field remove_extensions)" in all) flags+=(-e) ;; *) flags+=(-g) ;; esac

out="${3:-$root}/${mod}_${published}_accretion.ipa"
echo "==> Injecting into $out"
"$HOME/.cyan/bin/cyan" -i "$work/base.ipa" -o "$out" -f "$dylib" \
    -n "$(field display_name)" -b "$(field bundle_id)" "${flags[@]}" --overwrite

# The visible version becomes the published one; the build number (CFBundleVersion) stays as is.
python3 - "$out" "$published" <<'PY'
import os, plistlib, re, shutil, sys, tempfile, zipfile
path, version = sys.argv[1], sys.argv[2]
tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".ipa", dir=os.path.dirname(os.path.abspath(path)))
tmp.close()
with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as dst:
    for item in src.infolist():
        data = src.read(item.filename)
        if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", item.filename):
            info = plistlib.loads(data)
            info["CFBundleShortVersionString"] = version
            data = plistlib.dumps(info, fmt=plistlib.FMT_BINARY)
        dst.writestr(item, data)
shutil.move(tmp.name, path)
PY
echo "==> Version: $published"
echo "==> Built: $out (unsigned)"
# for the Build mod workflow
if [ -n "${GITHUB_OUTPUT:-}" ]; then
    { echo "ipa=$out"; echo "version=$published"; } >> "$GITHUB_OUTPUT"
fi
if [ "$sign" = 1 ]; then
    "$root/scripts/sign.sh" "$out" "${out%.ipa}_signed.ipa"
fi
