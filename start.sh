#!/bin/bash
set -e
echo "🚀 PORT=$PORT"
echo "DATABASE_URL: ${DATABASE_URL:+ok}"

python -u setup_db.py

echo "▶️ Gunicorn porta $PORT"
exec gunicorn servidor:app --bind "0.0.0.0:$PORT" --workers 2 --timeout 120
