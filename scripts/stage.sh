#!/usr/bin/env bash
# Publish a mod build made on your own machine, without the upload key: puts the .ipa in a private
# draft release and starts the "Publish mod" workflow, which uploads it with the repo's secret and
# deletes the draft. (To build and publish in the cloud instead: Actions -> Build mod.)
# Needs gh, logged in with write access to AccretionMods/mods.
#
#   scripts/stage.sh <mod> <path/to.ipa> [min-ios]
#   scripts/stage.sh swiftradio-plus out/swiftradio-plus_4.0.0-mod1.0.0_accretion.ipa
# The published version is the .ipa's own (scripts/local_ipa.sh sets it, like 4.0.0-mod1.0.0).
set -euo pipefail
REPO=AccretionMods/mods
[ $# -ge 2 ] || { sed -n '2,10p' "$0"; exit 1; }
mod=$1 ipa=$2 min_os=${3:-}
cd "$(dirname "$0")/.."
[ -f "mods/$mod/mod.json" ] || { echo "No mod named $mod (mods/$mod/mod.json)"; exit 1; }
[ -f "$ipa" ] || { echo "No file at $ipa"; exit 1; }
python3 scripts/check_mod_ipa.py "$mod" "$ipa"
name="stage-$mod-$(date -u +%Y%m%d%H%M%S)"
gh release create "$name" "$ipa" --draft --title "$name" -R "$REPO" \
  --notes "Staged for the Publish mod workflow; deleted after publishing. Private while it's a draft."
gh workflow run publish.yml -R "$REPO" -f mod="$mod" -f draft="$name" ${min_os:+-f min_os="$min_os"}
echo "Started. Follow it with: gh run watch -R $REPO \$(gh run list -R $REPO --workflow publish.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
