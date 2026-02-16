#!/usr/bin/env bash

# Log everything to a file so we can debug failures
exec > /tmp/coderev-install.log 2>&1
set -x

# Only run inside GitHub Codespaces
if [ "${CODESPACES:-}" != "true" ]; then
    echo "Not running in Codespaces, skipping coderev setup."
    exit 0
fi

DOTFILES_DIR="/workspaces/.codespaces/.persistedshare/dotfiles"
SERVER_DEST="$DOTFILES_DIR/server/api_server.py"
AUTH_TOKEN_FILE="/tmp/coderev-auth-token"
PID_FILE="/tmp/coderev-server.pid"
PORT=8976

# ── Idempotency: if server is already running, exit ──
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "coderev server already running (PID $(cat "$PID_FILE")), skipping."
    exit 0
fi

# ── Install Claude Code ──
if ! command -v claude &>/dev/null; then
    echo "Installing Claude Code..."
    curl -fsSL https://claude.ai/install.sh | bash || true
    export PATH="$HOME/.claude/bin:$PATH"
fi

# ── Install Python dependencies ──
echo "Installing Python dependencies..."
pip install --quiet fastapi uvicorn || pip3 install --quiet fastapi uvicorn

# ── API server path ──
chmod +x "$SERVER_DEST"

# ── Generate auth token ──
echo "Generating auth token..."
python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')" > "$AUTH_TOKEN_FILE"
chmod 600 "$AUTH_TOKEN_FILE"

# ── Detect repo directory ──
REPO_DIR=""
for dir in /workspaces/*/; do
    basename="$(basename "$dir")"
    if [ "$basename" != ".codespaces" ]; then
        REPO_DIR="$dir"
        break
    fi
done

if [ -z "$REPO_DIR" ]; then
    echo "ERROR: No repo directory found in /workspaces/"
    exit 1
fi

echo "Detected repo directory: $REPO_DIR"

# ── Start server ──
AUTH_TOKEN="$(cat "$AUTH_TOKEN_FILE")"
echo "Starting coderev server on port $PORT..."
AUTH_TOKEN="$AUTH_TOKEN" REPO_DIR="$REPO_DIR" nohup python3 "$SERVER_DEST" \
    > /tmp/coderev-server.log 2>&1 &
echo $! > "$PID_FILE"

echo "coderev server started (PID $(cat "$PID_FILE")), logs at /tmp/coderev-server.log"
