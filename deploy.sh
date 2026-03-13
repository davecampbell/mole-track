#!/usr/bin/env bash
# Deploy mole-track to the Pi Zero.
# Usage:
#   ./deploy.sh             # sync files only
#   ./deploy.sh --restart   # sync + restart systemd service

set -euo pipefail

PI_HOST="dave@192.168.86.30"
PI_PATH="/home/dave/mole-track"
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "==> Syncing to ${PI_HOST}:${PI_PATH} ..."
rsync -avz --progress \
  --exclude='.git' \
  --exclude='.claude' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='.DS_Store' \
  "${PROJECT_ROOT}/" \
  "${PI_HOST}:${PI_PATH}/"

if [[ "${1:-}" == "--restart" ]]; then
  echo "==> Stopping old server ..."
  # Killing uvicorn drops the SSH session on Pi Zero — that's normal.
  # We ignore the exit code and wait for the Pi to recover.
  ssh "${PI_HOST}" "sudo pkill -9 -f uvicorn 2>/dev/null; true" || true
  sleep 10   # give Pi Zero time to release the camera device

  echo "==> Starting new server ..."
  # setsid --fork creates a new session fully detached from the SSH pty so sshd
  # closes the channel immediately; the server runs on independently.
  ssh "${PI_HOST}" "cd ${PI_PATH} && setsid --fork python3 -m uvicorn mole_track.main:app --host 0.0.0.0 --port 8000 > /tmp/mole-track.log 2>&1"

  echo "==> Waiting for startup ..."
  sleep 12   # Pi Zero takes ~10s to initialise libcamera

  echo "==> Startup log:"
  ssh "${PI_HOST}" "tail -20 /tmp/mole-track.log"
fi

echo "==> Done."
