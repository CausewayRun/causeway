#!/bin/bash
# Bash helper for getting causeway version from git
# Source this file to get CAUSEWAY_VERSION variable

CAUSEWAY_DIR="${CAUSEWAY_DIR:-$HOME/.causeway}"

# Get version from git describe, falling back to "unknown"
CAUSEWAY_VERSION=$(cd "$CAUSEWAY_DIR" 2>/dev/null && git describe --tags --always 2>/dev/null || echo "unknown")

export CAUSEWAY_VERSION
