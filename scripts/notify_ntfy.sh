#!/bin/bash
# Send a notification via ntfy.sh (self-hosted or public)
# Configure: set NTFY_URL and NTFY_TOPIC environment variables
# Example: NTFY_URL=https://ntfy.sh NTFY_TOPIC=soulsync

NTFY_URL="${NTFY_URL:-https://ntfy.sh}"
NTFY_TOPIC="${NTFY_TOPIC:-soulsync}"

curl -s -d "SoulSync automation '${SOULSYNC_AUTOMATION}' completed" \
    "${NTFY_URL}/${NTFY_TOPIC}" > /dev/null 2>&1

echo "Notification sent to ${NTFY_URL}/${NTFY_TOPIC}"
