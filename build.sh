#!/usr/bin/env bash
set -euo pipefail

MOD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RIMWORLD_APP="${RIMWORLD_APP:-$(cd "$MOD_DIR/../.." && pwd)}"
MANAGED_DIR="$RIMWORLD_APP/Contents/Resources/Data/Managed"
OUTPUT_DIR="$MOD_DIR/Assemblies"
OUTPUT_DLL="$OUTPUT_DIR/RimGPT.dll"

if ! command -v mcs >/dev/null 2>&1; then
  echo "mcs was not found. Install Mono, then rerun this script." >&2
  exit 1
fi

required_refs=(
  "$MANAGED_DIR/Assembly-CSharp.dll"
  "$MANAGED_DIR/UnityEngine.dll"
  "$MANAGED_DIR/UnityEngine.CoreModule.dll"
)

for ref in "${required_refs[@]}"; do
  if [[ ! -f "$ref" ]]; then
    echo "Missing RimWorld assembly: $ref" >&2
    echo "Set RIMWORLD_APP to the RimWorldMac.app path if RimWorld is installed elsewhere." >&2
    exit 1
  fi
done

mkdir -p "$OUTPUT_DIR"

mcs \
  -target:library \
  -sdk:4.7.2 \
  -out:"$OUTPUT_DLL" \
  -reference:"${required_refs[0]}" \
  -reference:"${required_refs[1]}" \
  -reference:"${required_refs[2]}" \
  "$MOD_DIR"/Source/*.cs

echo "Built $OUTPUT_DLL"
