#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${FINALLY_CONTAINER_NAME:-finally-app}"
VOLUME_NAME="${FINALLY_VOLUME_NAME:-finally-data}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required but was not found on PATH." >&2
  exit 1
fi

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  docker rm -f "$CONTAINER_NAME" >/dev/null
  echo "Stopped and removed container: $CONTAINER_NAME"
else
  echo "No container found: $CONTAINER_NAME"
fi

echo "Data volume preserved: $VOLUME_NAME"
