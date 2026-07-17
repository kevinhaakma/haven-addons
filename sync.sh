#!/bin/sh
# Kopieert agent/ en hub/ naar repo/hafleet_agent en repo/hafleet_hub, zodat
# de add-on-repository up-to-date blijft met de broncode. Draai dit script
# na elke wijziging in agent/ of hub/, vlak voordat je repo/ naar GitHub pusht.
#
# Gebruik: ./sync.sh   (of: sh sync.sh)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SRC_AGENT="$ROOT_DIR/agent"
SRC_HUB="$ROOT_DIR/hub"
DST_AGENT="$SCRIPT_DIR/hafleet_agent"
DST_HUB="$SCRIPT_DIR/hafleet_hub"

echo "Sync agent/ -> repo/hafleet_agent"
mkdir -p "$DST_AGENT"
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude '__pycache__' "$SRC_AGENT/" "$DST_AGENT/"
else
    rm -rf "$DST_AGENT"
    mkdir -p "$DST_AGENT"
    cp -R "$SRC_AGENT/." "$DST_AGENT/"
    rm -rf "$DST_AGENT/__pycache__"
fi

echo "Sync hub/ -> repo/hafleet_hub"
mkdir -p "$DST_HUB"
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude '__pycache__' "$SRC_HUB/" "$DST_HUB/"
else
    rm -rf "$DST_HUB"
    mkdir -p "$DST_HUB"
    cp -R "$SRC_HUB/." "$DST_HUB/"
    rm -rf "$DST_HUB/__pycache__"
fi

echo "Klaar. Controleer repo/hafleet_agent en repo/hafleet_hub en push naar GitHub."
