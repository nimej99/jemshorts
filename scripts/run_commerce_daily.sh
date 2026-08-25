#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/storage/commerce-metrics"
mkdir -p "$OUT"
cd "$ROOT"

STAMP="$(date +%Y-%m-%d)"
"$HOME/.local/bin/uv" run python scripts/commerce_daily.py --build-hub \
  --output "$OUT/$STAMP.json" > "$OUT/$STAMP.out" 2> "$OUT/$STAMP.err"
cp "$OUT/$STAMP.json" "$OUT/latest.json"
