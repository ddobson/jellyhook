#!/usr/bin/env bash
# Run Jellyhook with plain `docker` (no Compose). See docs/docker.md for what each setting does.
#
# Copy this file, fill in the values below, and run it. Running it again recreates the
# containers with the current settings and images, which is also how you upgrade: queued
# messages live in the rabbitmq_data volume and survive.
set -euo pipefail

# --- Images ---------------------------------------------------------------------------------
TAG="latest-release-tag"  # e.g. v0.9.0
API_IMAGE="ghcr.io/ddobson/jellyhook-api:${TAG}"
WORKER_IMAGE="ghcr.io/ddobson/jellyhook-worker:${TAG}"
RABBITMQ_IMAGE="rabbitmq:4.0.5-management-alpine"

# --- Host paths -----------------------------------------------------------------------------
# Copy of rabbitmq/rabbitmq.conf from this repository (raises consumer_timeout to 24 hours)
RABBITMQ_CONF="/srv/jellyhook/rabbitmq.conf"
# Directory holding jellyhook.json (or .yaml); the worker also writes logs/worker.log here
CONFIG_DIR="/srv/jellyhook/config"
# Library root containing the movies/ and stand-up/ folders, mounted read-write
MEDIA_DIR="/mnt/media"
# Scratch space with room for a full remux; same filesystem as MEDIA_DIR is best
SCRATCH_DIR="/mnt/media/.jellyhook-tmp"

# --- Secrets --------------------------------------------------------------------------------
RABBITMQ_USER="jellyhook"
RABBITMQ_PASS="change-me"
SECRET_KEY="change-me"      # e.g. `openssl rand -hex 32`
WEBHOOK_TOKEN="change-me"   # Jellyfin must send "Authorization: Bearer <token>"
JELLYFIN_HOST="http://jellyfin.lan"  # include the scheme; the port is added separately
JELLYFIN_PORT="8096"
JELLYFIN_API_KEY="change-me"
JELLYFIN_USER_ID="change-me"

# --- Ports published on the host ------------------------------------------------------------
API_PORT="8000"

# Stop (gracefully, honouring --stop-timeout) and remove a container if it exists.
replace_container() {
    if docker container inspect "$1" >/dev/null 2>&1; then
        docker stop "$1" >/dev/null
        docker rm "$1" >/dev/null
    fi
}

docker network inspect jellyhook >/dev/null 2>&1 || docker network create jellyhook
docker volume inspect rabbitmq_data >/dev/null 2>&1 || docker volume create rabbitmq_data

docker pull "$RABBITMQ_IMAGE"
docker pull "$API_IMAGE"
docker pull "$WORKER_IMAGE"

# Stop the worker first so in-flight jobs finish (or are redelivered) before the broker goes.
replace_container jellyhook-worker
replace_container jellyhook-api
replace_container rabbitmq

# The container name is what RABBITMQ_HOST resolves to. The fixed hostname keeps the data in
# rabbitmq_data usable after the container is recreated. RABBITMQ_DEFAULT_USER/PASS only take
# effect when the volume is empty.
docker run -d --name rabbitmq --hostname rabbitmq \
    --network jellyhook --restart unless-stopped \
    -v rabbitmq_data:/var/lib/rabbitmq \
    -v "${RABBITMQ_CONF}:/etc/rabbitmq/conf.d/10-jellyhook.conf:ro" \
    -e RABBITMQ_DEFAULT_USER="$RABBITMQ_USER" \
    -e RABBITMQ_DEFAULT_PASS="$RABBITMQ_PASS" \
    "$RABBITMQ_IMAGE"

# Stand-in for Compose's `depends_on: condition: service_healthy`. Not required (the worker
# restarts until the broker answers), but it keeps the first start quiet.
echo "Waiting for RabbitMQ..."
until docker exec rabbitmq rabbitmq-diagnostics -q ping >/dev/null 2>&1; do
    sleep 2
done

docker run -d --name jellyhook-api \
    --network jellyhook --restart unless-stopped \
    -p "${API_PORT}:8000" \
    -e FLASK_ENV=production \
    -e SECRET_KEY="$SECRET_KEY" \
    -e WEBHOOK_TOKEN="$WEBHOOK_TOKEN" \
    -e RABBITMQ_USER="$RABBITMQ_USER" \
    -e RABBITMQ_PASS="$RABBITMQ_PASS" \
    "$API_IMAGE"

# --init forwards signals and reaps ffmpeg/mkvmerge children. --stop-timeout must be at least
# WORKER_SHUTDOWN_GRACE_SECONDS (45) plus 15.
docker run -d --name jellyhook-worker \
    --network jellyhook --restart unless-stopped \
    --init --stop-timeout 60 \
    -v "${CONFIG_DIR}:/config" \
    -v "${MEDIA_DIR}:/data/media" \
    -v "${SCRATCH_DIR}:/data/tmp" \
    -e WORKER_ENV=production \
    -e JELLYFIN_HOST="$JELLYFIN_HOST" \
    -e JELLYFIN_PORT="$JELLYFIN_PORT" \
    -e JELLYFIN_API_KEY="$JELLYFIN_API_KEY" \
    -e JELLYFIN_USER_ID="$JELLYFIN_USER_ID" \
    -e RABBITMQ_USER="$RABBITMQ_USER" \
    -e RABBITMQ_PASS="$RABBITMQ_PASS" \
    "$WORKER_IMAGE"

echo "Jellyhook is running. Point Jellyfin's webhook at http://<this-host>:${API_PORT}/hooks/item_added"
