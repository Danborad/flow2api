#!/usr/bin/env bash
set -e

SERVER_URL="${1:-http://192.168.31.63:8000}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Flow2API Extension Updater ==="
echo "Fetching latest extension from $SERVER_URL..."
curl -fsSL "$SERVER_URL/api/plugin/download" -o "$DIR/flow2api-extension.zip"

echo "Extracting into $DIR..."
unzip -qo "$DIR/flow2api-extension.zip" -d "$DIR"
rm -f "$DIR/flow2api-extension.zip"

echo "=== Update complete! Extension will auto-reload in browser. ==="
