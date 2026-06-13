#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

status=0
tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT

scan_pattern() {
  local label="$1"
  local pattern="$2"

  if git grep -InE "$pattern" -- \
    ':!*.lock' \
    ':!src/RenuxServer/wwwroot/frontend/package-lock.json' \
    ':!SECURITY_LAUNCH_RUNBOOK.md' \
    ':!scripts/scan-secrets.sh' >"$tmp_file"; then
    echo "::error title=Potential secret detected::$label"
    sed 's/=.*/=<redacted>/' "$tmp_file"
    status=1
  fi
}

scan_pattern "OpenAI API key" 'sk-(proj-[A-Za-z0-9_-]{20,}|[A-Za-z0-9]{32,})'
scan_pattern "Hugging Face token" 'hf_[A-Za-z0-9]{30,}'
scan_pattern "Postgres URL with inline password" 'postgres(ql)?://[^[:space:]]+:[^[:space:]@]+@'
scan_pattern "JWT secret assigned in tracked file" '(^|[^A-Za-z0-9_])(JWT_KEY|Jwt__Key|Jwt:Key)[[:space:]]*[:=][[:space:]]*"?[A-Za-z0-9+/=]{32,}"?'

if find . \
  -path './.git' -prune -o \
  -path './**/node_modules' -prune -o \
  -path './**/bin' -prune -o \
  -path './**/obj' -prune -o \
  -path './**/dist' -prune -o \
  -type f \( -name '.env' -o -name '.env.*' \) ! -name '.env.example' -print | grep -q .; then
  echo "Local .env files exist. They are ignored, but rotate any real keys before launch and never add them with git add -f."
fi

exit "$status"
