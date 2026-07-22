#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_NAME="${FINALLY_IMAGE_NAME:-finally}"
CONTAINER_NAME="${FINALLY_CONTAINER_NAME:-finally-app}"
VOLUME_NAME="${FINALLY_VOLUME_NAME:-finally-data}"
PORT="${PORT:-8000}"
BIND_HOST="${FINALLY_BIND_HOST:-127.0.0.1}"
OPEN_BROWSER=0

usage() {
  cat <<'EOF'
Usage: scripts/start_mac.sh [options]

Options:
  --build        Accepted for compatibility; images are always rebuilt safely.
  --open         Open http://localhost:<port> after the container starts.
  --port PORT    Host port to bind to container port 8000. Default: 8000.
  --host HOST    Host interface to bind. Default: 127.0.0.1 (localhost only).
  -h, --help     Show this help.

Environment overrides:
  FINALLY_IMAGE_NAME       Docker image name. Default: finally
  FINALLY_CONTAINER_NAME   Docker container name. Default: finally-app
  FINALLY_VOLUME_NAME      Docker volume name. Default: finally-data
  FINALLY_BIND_HOST        Host bind address. Default: 127.0.0.1
  PORT                     Host port. Default: 8000
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build)
      shift
      ;;
    --open)
      OPEN_BROWSER=1
      shift
      ;;
    --port)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --port" >&2
        exit 2
      fi
      PORT="$2"
      shift 2
      ;;
    --host)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --host" >&2
        exit 2
      fi
      BIND_HOST="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required but was not found on PATH." >&2
  exit 1
fi

if [[ ! -f "$ROOT_DIR/Dockerfile" ]]; then
  echo "Dockerfile not found at $ROOT_DIR/Dockerfile" >&2
  exit 1
fi

cd "$ROOT_DIR"

# Always build. Docker's layer cache keeps unchanged rebuilds fast, while this
# prevents an existing tag from silently serving source from an older checkout.
echo "Building Docker image: $IMAGE_NAME"
docker build -t "$IMAGE_NAME" .

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "Stopping existing container gracefully: $CONTAINER_NAME"
  docker stop --time 10 "$CONTAINER_NAME" >/dev/null || true
  docker rm "$CONTAINER_NAME" >/dev/null
fi

docker volume create "$VOLUME_NAME" >/dev/null

ENV_ARGS=()
if [[ -f "$ROOT_DIR/.env" ]]; then
  ENV_ARGS+=(--env-file "$ROOT_DIR/.env")
else
  echo "No .env file found; starting with built-in defaults."
fi
ENV_ARGS+=(-e "DB_PATH=/app/db/finally.db")

echo "Starting container: $CONTAINER_NAME"
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  -p "${BIND_HOST}:${PORT}:8000" \
  -v "${VOLUME_NAME}:/app/db" \
  "${ENV_ARGS[@]}" \
  "$IMAGE_NAME" >/dev/null

URL="http://localhost:${PORT}"
echo "Container started. URL: $URL"

echo "Waiting for container health check..."
HEALTHY=0
for _ in {1..60}; do
  STATUS="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)"
  if [[ "$STATUS" == "healthy" ]]; then
    HEALTHY=1
    break
  fi
  if [[ "$STATUS" == "exited" || "$STATUS" == "dead" || "$STATUS" == "unhealthy" ]]; then
    break
  fi
  sleep 1
done

if [[ "$HEALTHY" -ne 1 ]]; then
  echo "Container failed to become healthy; recent logs:" >&2
  docker logs --tail 80 "$CONTAINER_NAME" >&2 || true
  docker stop --time 10 "$CONTAINER_NAME" >/dev/null 2>&1 || true
  docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true
  exit 1
fi
echo "Readiness check passed: ${URL}/api/ready"

if [[ "$OPEN_BROWSER" -eq 1 ]]; then
  if command -v open >/dev/null 2>&1; then
    open "$URL"
  else
    echo "The 'open' command was not found; open this URL manually: $URL"
  fi
fi
