#!/bin/bash
# Ping causeway API for version check and telemetry
# Uses only curl - no python required

API_URL="https://causeway-api.fly.dev"
CAUSEWAY_DIR="$HOME/.causeway"
VERSION="0.1.0"
ID_FILE="$CAUSEWAY_DIR/.install_id"

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

# Ping API
RESPONSE=$(curl -s -X POST "$API_URL/ping" \
    -H "Content-Type: application/json" \
    -d "{\"install_id\":\"$INSTALL_ID\",\"version\":\"$VERSION\",\"platform\":\"$PLATFORM\",\"arch\":\"$ARCH\"}" \
    --connect-timeout 3 \
    --max-time 5 \
    2>/dev/null)

# Check for update (unless --silent)
if [ "$1" != "--silent" ] && [ -n "$RESPONSE" ]; then
    UPDATE=$(echo "$RESPONSE" | grep -o '"update_available":true')
    if [ -n "$UPDATE" ]; then
        LATEST=$(echo "$RESPONSE" | sed -n 's/.*"latest_version":"\([^"]*\)".*/\1/p')
        # Output context for Claude to inform user about update
        echo "[Causeway] Update available: v$LATEST. Please inform the user that a Causeway update is available and they can run 'causeway update' to install it."
    fi
fi
