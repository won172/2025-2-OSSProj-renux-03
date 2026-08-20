#!/bin/sh

set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
FRONTEND_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/../../.." && pwd)"

node_is_supported() {
  command -v node >/dev/null 2>&1 &&
    node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 12) || (major === 20 && minor >= 19) ? 0 : 1)'
}

if ! node_is_supported; then
  if ! command -v brew >/dev/null 2>&1; then
    echo "error: Node.js 20.19+ or 22.12+ is required, and Homebrew is unavailable." >&2
    exit 1
  fi

  brew install node@22
  NODE_PREFIX="$(brew --prefix node@22)"
  PATH="$NODE_PREFIX/bin:$PATH"
  export PATH
fi

VITE_API_BASE_URL="${VITE_API_BASE_URL:-https://api.dongttok.com}"
export VITE_API_BASE_URL

cd "$FRONTEND_DIR"

echo "Preparing Capacitor dependencies with Node $(node --version) and npm $(npm --version)"
npm ci --no-audit --no-fund
npm run ios:sync
