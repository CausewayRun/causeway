#!/bin/bash
# Ping causeway API for telemetry and check GitHub for updates
# Uses only curl - no python required

API_URL="https://causeway-api.fly.dev"
GITHUB_API="https://api.github.com/repos/codimusmaximus/causeway/releases/latest"
CAUSEWAY_DIR="$HOME/.causeway"
ID_FILE="$CAUSEWAY_DIR/.install_id"

# Source version helper
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/version.sh"
VERSION="$CAUSEWAY_VERSION"

# Get or create install ID
if [ -f "$ID_FILE" ]; then
    INSTALL_ID=$(cat "$ID_FILE")
else
    INSTALL_ID=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid 2>/dev/null || date +%s%N)
    echo "$INSTALL_ID" > "$ID_FILE"
fi

# Detect platform
PLATFORM=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)

# Send telemetry to fly.dev (fire and forget)
curl -s -X POST "$API_URL/ping" \
    -H "Content-Type: application/json" \
    -d "{\"install_id\":\"$INSTALL_ID\",\"version\":\"$VERSION\",\"platform\":\"$PLATFORM\",\"arch\":\"$ARCH\"}" \
    --connect-timeout 3 \
    --max-time 5 \
    >/dev/null 2>&1 &

# Check for updates via GitHub API (unless --silent)
if [ "$1" != "--silent" ]; then
    GITHUB_RESPONSE=$(curl -s "$GITHUB_API" \
        -H "Accept: application/vnd.github.v3+json" \
        -H "User-Agent: causeway" \
        --connect-timeout 3 \
        --max-time 5 \
        2>/dev/null)

    if [ -n "$GITHUB_RESPONSE" ]; then
        LATEST=$(echo "$GITHUB_RESPONSE" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p')
        if [ -n "$LATEST" ] && [ "$LATEST" != "$VERSION" ]; then
            # Compare versions (strip 'v' prefix for comparison)
            CURRENT_CLEAN="${VERSION#v}"
            LATEST_CLEAN="${LATEST#v}"

            # Remove any git describe suffix (e.g., -5-gabcdef)
            CURRENT_CLEAN="${CURRENT_CLEAN%%-*}"

            # Simple version comparison using sort -V
            if [ "$(printf '%s\n' "$LATEST_CLEAN" "$CURRENT_CLEAN" | sort -V | tail -1)" = "$LATEST_CLEAN" ] && [ "$LATEST_CLEAN" != "$CURRENT_CLEAN" ]; then
                echo "[Causeway] Update available: $LATEST → Run 'causeway update' to install"
            fi
        fi
    fi
fi
