#!/usr/bin/env bash
# Update a Cascade SDR deployment: pull, rebuild, restart the service.
#
# Pulls the latest code, reinstalls dependencies only if they changed, rebuilds
# the frontend, and restarts the systemd service. Run it from anywhere — it finds
# the checkout from its own location.
#
#   ~/Cascade-SDR/deploy/update.sh
#
# Override the service name if you named it differently:
#   SERVICE=my-cascade ~/Cascade-SDR/deploy/update.sh
set -euo pipefail

SERVICE="${SERVICE:-cascade-sdr}"
# Repo root = parent of this script's directory (deploy/), resolved absolutely.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

say "Updating checkout in $ROOT"
OLD="$(git rev-parse HEAD)"
git pull --ff-only
NEW="$(git rev-parse HEAD)"

if [ "$OLD" = "$NEW" ]; then
  echo "Already up to date ($NEW) — rebuilding and restarting anyway."
  CHANGED=""
else
  CHANGED="$(git diff --name-only "$OLD" "$NEW")"
  echo "Updated $OLD -> $NEW"
fi

# Backend deps: only reinstall when requirements changed (and a venv exists).
if [ -z "$CHANGED" ] || grep -q '^backend/requirements.txt$' <<<"$CHANGED"; then
  if [ -x backend/.venv/bin/pip ]; then
    say "Installing backend dependencies"
    backend/.venv/bin/pip install -q -r backend/requirements.txt
  fi
fi

# Frontend deps: only reinstall when the lockfile/manifest changed.
if [ -z "$CHANGED" ] || grep -qE '^frontend/package(-lock)?\.json$' <<<"$CHANGED"; then
  say "Installing frontend dependencies"
  ( cd frontend && npm ci )
fi

say "Building frontend"
( cd frontend && npm run build )

say "Restarting $SERVICE"
sudo systemctl restart "$SERVICE"
sleep 1
systemctl --no-pager --lines=0 status "$SERVICE" || true

say "Done."
