#!/usr/bin/env bash
# Install zsign (open-source iOS signer, MIT) into ~/.local/bin on Linux/WSL x86_64.
# Downloads the pinned release and checks it against the release's SHA256SUMS.txt.
set -euo pipefail

VERSION=v1.1.2
ASSET=zsign-linux-x86_64.tar.gz
base="https://github.com/zhlynn/zsign/releases/download/$VERSION"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"
curl -fsSLO "$base/$ASSET"
curl -fsSLO "$base/SHA256SUMS.txt"
grep -E " \*?$ASSET\$" SHA256SUMS.txt | sha256sum -c -

tar -xzf "$ASSET"
bin="$(find . -type f -name zsign -print -quit)"
[ -n "$bin" ] || { echo "no zsign binary in $ASSET" >&2; exit 1; }
mkdir -p "$HOME/.local/bin"
install -m 755 "$bin" "$HOME/.local/bin/zsign"
"$HOME/.local/bin/zsign" -v
echo "Installed to ~/.local/bin/zsign"
