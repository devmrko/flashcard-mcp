#!/bin/sh
# Mac mini: create tables, check DB, start/restart under pm2, check local health.
set -e
cd "$(dirname "$0")/.."
.venv/bin/flashcard init-db
.venv/bin/flashcard check-db
if pm2 describe flashcard >/dev/null 2>&1; then pm2 restart flashcard; else pm2 start ecosystem.config.cjs; fi
pm2 save
sleep 3
curl -fsS http://127.0.0.1:8810/healthz && echo
