#!/bin/sh
set -eu

APP_IMAGE="${APP_IMAGE:-ghcr.io/sasa-fajkovic/agents-skill-eval-app:latest}"
APP_CONTAINER_NAME="${APP_CONTAINER_NAME:-agents-skill-eval-app}"
APP_ENV_FILE="${APP_ENV_FILE:-/etc/agents-skill-eval/app.env}"
APP_PORT="${APP_PORT:-8080}"

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
  --env-file "$APP_ENV_FILE" \
  -p 127.0.0.1:${APP_PORT}:8080 \
  "$APP_IMAGE" >/dev/null

sleep 8
curl -fsS "http://127.0.0.1:${APP_PORT}/health" >/dev/null
