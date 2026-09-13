#!/bin/bash
# Runs as root, invoked ONLY via the narrow sudoers rule granted to the CI
# deploy service account:
#   sa_106943929734035571006 ALL=(root) NOPASSWD: /opt/karara-app/deploy.sh
#
# Usage: deploy.sh <release-id>
# Expects the new release's files already sitting at /opt/karara-app/incoming/<release-id>/
# (placed there by the CI job via scp into the sticky-bit shared "incoming" dir).
set -euo pipefail

RELEASE_ID="${1:?usage: deploy.sh <release-id>}"
APP_ROOT="/opt/karara-app"
INCOMING="$APP_ROOT/incoming/$RELEASE_ID"
RELEASE_DIR="$APP_ROOT/releases/$RELEASE_ID"
CURRENT_LINK="$APP_ROOT/current"

if [ ! -d "$INCOMING" ]; then
  echo "deploy.sh: nothing staged at $INCOMING" >&2
  exit 1
fi

echo "==> promoting $RELEASE_ID"
rm -rf "$RELEASE_DIR"
mv "$INCOMING" "$RELEASE_DIR"
chown -R root:root "$RELEASE_DIR"
chmod -R go-w "$RELEASE_DIR"

echo "==> building venv"
python3 -m venv "$RELEASE_DIR/venv"
"$RELEASE_DIR/venv/bin/pip" install --quiet --upgrade pip
"$RELEASE_DIR/venv/bin/pip" install --quiet -r "$RELEASE_DIR/app/requirements.txt"

PREVIOUS_TARGET=""
if [ -L "$CURRENT_LINK" ]; then
  PREVIOUS_TARGET="$(readlink -f "$CURRENT_LINK")"
fi

echo "==> switching current -> $RELEASE_DIR"
ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"
systemctl restart karara-app karara-worker

echo "==> health check"
sleep 2
if curl -fsS --max-time 8 http://127.0.0.1:8080/health > /dev/null; then
  echo "==> deploy $RELEASE_ID OK"
else
  echo "==> health check FAILED" >&2
  if [ -n "$PREVIOUS_TARGET" ] && [ -d "$PREVIOUS_TARGET" ]; then
    echo "==> rolling back to $PREVIOUS_TARGET" >&2
    ln -sfn "$PREVIOUS_TARGET" "$CURRENT_LINK"
    systemctl restart karara-app karara-worker
  fi
  exit 1
fi

echo "==> pruning old releases (keeping last 5)"
cd "$APP_ROOT/releases"
ls -1t | tail -n +6 | xargs -r rm -rf

echo "==> done"
