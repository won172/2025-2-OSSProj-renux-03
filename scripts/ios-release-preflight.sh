#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
frontend_root="$repo_root/src/RenuxServer/wwwroot/frontend"
ios_root="$frontend_root/ios/App"
release_api_url="${DONGTTOK_API_URL:-}"

fail() {
  echo "iOS release preflight failed: $*" >&2
  exit 1
}

[[ "$release_api_url" == https://* ]] || fail "DONGTTOK_API_URL must be a stable https:// URL."
case "$release_api_url" in
  *localhost*|*127.0.0.1*|*.ngrok-free.app*|*.ngrok-free.dev*|*.ngrok.app*|*.ngrok.dev*)
    fail "temporary/local API URLs are not allowed for an App Store build"
    ;;
esac

release_api_host="${release_api_url#https://}"
release_api_host="${release_api_host%%/*}"
release_api_host="${release_api_host%%:*}"
[[ -n "$release_api_host" ]] || fail "could not resolve the API hostname"

rg -q 'MapDelete\("/account"' "$repo_root/src/RenuxServer/Apis/Auth/AuthenticationApis.cs" \
  || fail "in-app account deletion endpoint is missing"
rg -q 'capacitor://localhost' "$repo_root/src/RenuxServer/.env.example" \
  || fail "Capacitor origin is missing from the CORS example"
rg -q '회원 탈퇴' "$frontend_root/src/pages/settings/SettingsPage.tsx" \
  || fail "account deletion UI is missing"
rg -q '직접 계정 삭제' "$frontend_root/src/pages/legal/PrivacyPolicyPage.tsx" \
  || fail "privacy policy does not describe in-app deletion"

app_icon="$ios_root/App/Assets.xcassets/AppIcon.appiconset/AppIcon-512@2x.png"
[[ -f "$app_icon" ]] || fail "1024px App Store icon is missing"
icon_info="$(sips -g pixelWidth -g pixelHeight -g hasAlpha "$app_icon")"
grep -q 'pixelWidth: 1024' <<<"$icon_info" || fail "App Store icon width must be 1024"
grep -q 'pixelHeight: 1024' <<<"$icon_info" || fail "App Store icon height must be 1024"
grep -q 'hasAlpha: no' <<<"$icon_info" || fail "App Store icon must not have transparency"

(
  cd "$frontend_root"
  VITE_API_BASE_URL="$release_api_url" npm run build:native
  npx cap sync ios
)

compiled_client="$(find "$ios_root/App/public/assets" -maxdepth 1 -name 'client-*.js' -print -quit)"
[[ -n "$compiled_client" ]] || fail "compiled API client bundle was not found"
rg -Fq "$release_api_url" "$compiled_client" || fail "native bundle does not contain the requested API URL"
if rg -q 'ngrok(?:-free)?\.(?:app|dev)' "$ios_root/App/public/assets"; then
  fail "native bundle still contains an ngrok address"
fi

echo "iOS release preflight passed for $release_api_host"
echo "Archive with Xcode build setting: DONGTTOK_API_HOST=$release_api_host"
