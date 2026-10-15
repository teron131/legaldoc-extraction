#!/usr/bin/env bash
set -euo pipefail

APP_TARGET="legaldoc_extraction.api:app"
FRONTEND_HOST="${FRONTEND_HOST:-localhost}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
RUN_BACKEND="${RUN_BACKEND:-1}"
BACKEND_HOST="${BACKEND_HOST:-${HOST:-localhost}}"
BACKEND_PORT="${BACKEND_PORT:-${PORT:-8000}}"
BACKEND_PID=""

cleanup() {
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
    wait "${BACKEND_PID}" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

# Read .env as data, not shell code: the app saves values such as PLATFORM=Vercel AI Gateway unquoted, which `source` would try to run as a command.
load_env_file() {
  local pattern='^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$'
  local line key value
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    [[ "${line}" =~ ${pattern} ]] || continue
    key="${BASH_REMATCH[2]}"
    value="${BASH_REMATCH[3]}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ ${#value} -ge 2 && ${value:0:1} == "${value: -1}" && ( ${value:0:1} == '"' || ${value:0:1} == "'" ) ]]; then
      value="${value:1:${#value}-2}"
    fi
    export "${key}=${value}"
  done < "$1"
}

if [[ -f .env ]]; then
  load_env_file .env
fi

if [[ ! -d frontend/node_modules ]]; then
  (
    cd frontend
    pnpm install
  )
fi

if [[ "${RUN_BACKEND}" == "1" ]]; then
  uv sync
  export HOST="${BACKEND_HOST}"
  export PORT="${BACKEND_PORT}"
  export FRONTEND_HOST
  export FRONTEND_PORT
  export VITE_BACKEND_URL="http://${BACKEND_HOST}:${BACKEND_PORT}"
  uv run uvicorn "${APP_TARGET}" --reload --host "${BACKEND_HOST}" --port "${BACKEND_PORT}" &
  BACKEND_PID=$!
  echo "Backend API running on ${VITE_BACKEND_URL}"
else
  unset VITE_BACKEND_URL || true
  echo "Backend disabled. Frontend will use mock mode."
fi

if [[ -n "${KEYCLOAK_BASE_URL:-}" ]]; then
  export VITE_KEYCLOAK_URL="${KEYCLOAK_BASE_URL%/}"
fi
if [[ -n "${KEYCLOAK_REALM:-}" ]]; then
  export VITE_KEYCLOAK_REALM="${KEYCLOAK_REALM}"
fi
if [[ -n "${KEYCLOAK_CLIENT_ID:-}" ]]; then
  export VITE_KEYCLOAK_CLIENT_ID="${KEYCLOAK_CLIENT_ID}"
fi
if [[ -n "${AUTH_ENABLED:-}" ]]; then
  export VITE_AUTH_ENABLED="${AUTH_ENABLED}"
fi

echo "Frontend UI running on http://${FRONTEND_HOST}:${FRONTEND_PORT}"

(
  cd frontend
  pnpm run dev -- --host "${FRONTEND_HOST}" --port "${FRONTEND_PORT}" --strictPort
)
