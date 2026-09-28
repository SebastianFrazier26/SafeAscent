#!/usr/bin/env bash
# Builds the frontend image in both MAINTENANCE_MODE settings and asserts the
# nginx contract Railway relies on. Usage: frontend/docker-tests/test_images.sh [true|false|all]
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${IMAGE_TEST_PORT:-18080}"
NAME=safeascent-frontend-image-test
fail=0

check() {
  local desc="$1" expected="$2" actual="$3"
  if [[ "$actual" == "$expected" ]]; then
    echo "PASS $desc"
  else
    echo "FAIL $desc: expected '$expected', got '$actual'"
    fail=1
  fi
}
status() { curl --silent --output /dev/null --write-out '%{http_code}' "$@"; }
header() {
  local name="$1"; shift
  curl --silent --output /dev/null --dump-header - "$@" | tr -d '\r' \
    | awk -v h="$name" 'tolower($0) ~ "^"tolower(h)":" {sub(/^[^:]*: /, ""); print; exit}'
}

start() {
  local mode="$1" image="safeascent-frontend-test:$1"
  docker build --quiet \
    --build-arg MAINTENANCE_MODE="$mode" \
    --build-arg VITE_API_BASE_URL=http://localhost:8000/api/v1 \
    --build-arg VITE_MAPBOX_TOKEN=pk.test_token_for_ci \
    -t "$image" . >/dev/null
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" -p "$PORT:80" "$image" >/dev/null
  for _ in $(seq 1 30); do
    curl --silent --output /dev/null "http://localhost:$PORT/health" && return 0
    sleep 0.5
  done
  echo "FAIL container never answered /health"; exit 1
}
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

base="http://localhost:$PORT"

test_common() {
  check "[$1] /health is 200" 200 "$(status "$base/health")"
  check "[$1] www redirects 301" 301 "$(status -H 'Host: www.safeascent.us' "$base/x?y=1")"
  check "[$1] www redirect target" "https://safeascent.us/x?y=1" \
    "$(header Location -H 'Host: www.safeascent.us' "$base/x?y=1")"
}

test_maintenance() {
  start true
  test_common maintenance
  for path in / /index.html /__maintenance/index.html /routes/123 /api/v1/predict /assets/app.js; do
    check "[maintenance] GET $path is 503" 503 "$(status "$base$path")"
  done
  check "[maintenance] POST is 503" 503 "$(status -X POST "$base/api/v1/predict")"
  check "[maintenance] HEAD is 503" 503 "$(status --head "$base/")"
  check "[maintenance] Retry-After" 3600 "$(header Retry-After "$base/some/path")"
  check "[maintenance] Cache-Control" no-store "$(header Cache-Control "$base/")"
  check "[maintenance] body is the maintenance page" yes \
    "$(curl --silent "$base/" | grep -q 'Offline for rebuild' && echo yes || echo no)"
}

test_app() {
  start false
  test_common app
  check "[app] / is 200" 200 "$(status "$base/")"
  check "[app] SPA deep link is 200" 200 "$(status "$base/routes/123")"
  check "[app] no Retry-After" "" "$(header Retry-After "$base/")"
}

case "${1:-all}" in
  true) test_maintenance ;;
  false) test_app ;;
  all) test_maintenance; test_app ;;
  *) echo "usage: $0 [true|false|all]"; exit 2 ;;
esac

exit "$fail"
