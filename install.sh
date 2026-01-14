#!/bin/bash
set -e

INSTALL_DIR="$HOME/.causeway"
REPO="https://github.com/codimusmaximus/causeway.git"

echo "Installing causeway..."

# Check for uv
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

# Clone or update
if [ -d "$INSTALL_DIR" ]; then
    echo "Updating..."
    cd "$INSTALL_DIR"
    git pull --quiet
else
    echo "Downloading..."
    git clone --quiet "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Install dependencies
echo "Installing dependencies..."
uv sync --quiet 2>/dev/null || uv sync

# Create wrapper script
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/causeway" << 'EOF'
#!/bin/bash
exec uv run --directory "$HOME/.causeway" causeway "$@"
EOF
chmod +x "$HOME/.local/bin/causeway"

# Add to PATH if needed
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc" 2>/dev/null || true
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc" 2>/dev/null || true
    export PATH="$HOME/.local/bin:$PATH"
fi

echo ""
echo "Done! Run: causeway connect"
echo ""
