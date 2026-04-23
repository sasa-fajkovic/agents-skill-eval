#!/bin/sh
set -eu

APP_IMAGE="${APP_IMAGE:-ghcr.io/sasa-fajkovic/agents-skill-eval-app:latest}"
APP_CONTAINER_NAME="${APP_CONTAINER_NAME:-agents-skill-eval-app}"
APP_PORT="${APP_PORT:-8080}"
ZSH_ENV_FILE="${ZSH_ENV_FILE:-$HOME/.zshenv}"

if [ -f "$ZSH_ENV_FILE" ]; then
  set -a
  . "$ZSH_ENV_FILE"
  set +a
fi

current_image="$(docker inspect --format='{{.Config.Image}}' "$APP_CONTAINER_NAME" 2>/dev/null || true)"

docker pull "$APP_IMAGE" >/dev/null

if [ "$current_image" = "$APP_IMAGE" ]; then
  running_id="$(docker inspect --format='{{.Image}}' "$APP_CONTAINER_NAME" 2>/dev/null || true)"
  pulled_id="$(docker image inspect --format='{{.Id}}' "$APP_IMAGE")"
  if [ "$running_id" = "$pulled_id" ]; then
    exit 0
  fi
fi

docker rm -f "$APP_CONTAINER_NAME" >/dev/null 2>&1 || true
docker run -d \
  --name "$APP_CONTAINER_NAME" \
  --restart unless-stopped \
  -e PORT=8080 \
  -e SENTRY_DSN="${SENTRY_DSN:-}" \
  -e SENTRY_ENVIRONMENT="${SENTRY_ENVIRONMENT:-production}" \
  -e APP_ENV="${APP_ENV:-production}" \
  -e DISABLE_ABUSE_PROTECTION="${DISABLE_ABUSE_PROTECTION:-false}" \
  -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
  -e OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
  -e ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-sonnet-4-6}" \
  -e OPENAI_MODEL="${OPENAI_MODEL:-gpt-4.1}" \
  -p 127.0.0.1:${APP_PORT}:8080 \
  "$APP_IMAGE" >/dev/null

sleep 8
curl -fsS "http://127.0.0.1:${APP_PORT}/health" >/dev/null

docker image prune -f >/dev/null
