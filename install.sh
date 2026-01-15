#!/bin/bash
set -e

INSTALL_DIR="$HOME/.causeway"
REPO="https://github.com/codimusmaximus/causeway.git"

echo "Installing causeway..."

# Find Python
find_python() {
    if command -v python3 &> /dev/null; then
        echo "python3"
    elif command -v python &> /dev/null; then
        # Verify it's Python 3
        if python -c "import sys; sys.exit(0 if sys.version_info[0] >= 3 else 1)" 2>/dev/null; then
            echo "python"
        fi
    fi
}

PYTHON_CMD=$(find_python)

# Check for uv, python3, or python
if command -v uv &> /dev/null; then
    USE_UV=1
    echo "Found uv"
elif [ -n "$PYTHON_CMD" ]; then
    USE_UV=0
    echo "Found $PYTHON_CMD (no uv - will use venv)"
else
    echo "No Python found. Installing uv (includes Python)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    USE_UV=1
fi

# Clone or update
if [ -d "$INSTALL_DIR" ]; then
    echo "Updating..."
    cd "$INSTALL_DIR"
    git fetch --quiet
    git reset --hard origin/main --quiet
else
    echo "Downloading..."
    git clone --quiet "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Install dependencies
echo "Installing dependencies..."

if [ "$USE_UV" = "1" ]; then
    uv sync --quiet 2>/dev/null || uv sync

    # Create wrapper script for uv
    mkdir -p "$HOME/.local/bin"
    cat > "$HOME/.local/bin/causeway" << 'WRAPPER'
#!/bin/bash
CAUSEWAY_CWD="$(pwd)" exec uv run --directory "$HOME/.causeway" causeway "$@"
WRAPPER
else
    # Create venv and install with pip
    PYTHON_CMD=$(find_python)
    VENV_DIR="$INSTALL_DIR/.venv"

    if [ ! -d "$VENV_DIR" ]; then
        echo "Creating virtual environment..."
        $PYTHON_CMD -m venv "$VENV_DIR"
    fi

    echo "Installing packages..."
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -e "$INSTALL_DIR"

    # Create wrapper script for venv
    mkdir -p "$HOME/.local/bin"
    cat > "$HOME/.local/bin/causeway" << 'WRAPPER'
#!/bin/bash
CAUSEWAY_CWD="$(pwd)" exec "$HOME/.causeway/.venv/bin/python" -m causeway "$@"
WRAPPER
fi

chmod +x "$HOME/.local/bin/causeway"

# Add to PATH if needed
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc" 2>/dev/null || true
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc" 2>/dev/null || true
    export PATH="$HOME/.local/bin:$PATH"
fi

# Initial ping (version check + telemetry) - curl only, no deps
bash "$INSTALL_DIR/scripts/ping.sh" --silent 2>/dev/null &

echo ""
echo "Done! Run: causeway connect"
echo ""
