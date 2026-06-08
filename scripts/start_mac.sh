#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_NAME="${FINALLY_IMAGE_NAME:-finally}"
CONTAINER_NAME="${FINALLY_CONTAINER_NAME:-finally-app}"
VOLUME_NAME="${FINALLY_VOLUME_NAME:-finally-data}"
PORT="${PORT:-8000}"
FORCE_BUILD=0
OPEN_BROWSER=0

usage() {
  cat <<'EOF'
Usage: scripts/start_mac.sh [options]

Options:
  --build        Force rebuild the Docker image before starting.
  --open         Open http://localhost:<port> after the container starts.
  --port PORT    Host port to bind to container port 8000. Default: 8000.
  -h, --help     Show this help.

Environment overrides:
  FINALLY_IMAGE_NAME       Docker image name. Default: finally
  FINALLY_CONTAINER_NAME   Docker container name. Default: finally-app
  FINALLY_VOLUME_NAME      Docker volume name. Default: finally-data
  PORT                     Host port. Default: 8000
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build)
      FORCE_BUILD=1
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

if [[ "$FORCE_BUILD" -eq 1 ]] || ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "Building Docker image: $IMAGE_NAME"
  docker build -t "$IMAGE_NAME" .
else
  echo "Using existing Docker image: $IMAGE_NAME"
fi

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "Removing existing container: $CONTAINER_NAME"
  docker rm -f "$CONTAINER_NAME" >/dev/null
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
  -p "${PORT}:8000" \
  -v "${VOLUME_NAME}:/app/db" \
  "${ENV_ARGS[@]}" \
  "$IMAGE_NAME" >/dev/null

URL="http://localhost:${PORT}"
echo "Container started. URL: $URL"

if command -v curl >/dev/null 2>&1; then
  echo "Waiting for health check..."
  for _ in {1..30}; do
    if curl -fsS "${URL}/api/health" >/dev/null 2>&1; then
      echo "Health check passed: ${URL}/api/health"
      break
    fi
    sleep 1
  done
fi

if [[ "$OPEN_BROWSER" -eq 1 ]]; then
  if command -v open >/dev/null 2>&1; then
    open "$URL"
  else
    echo "The 'open' command was not found; open this URL manually: $URL"
  fi
fi
