#!/bin/bash
echo "=== START ==="
echo "PORT=$PORT"
echo "DATABASE_URL=${DATABASE_URL:+definida}"

echo "--- Rodando setup_db.py ---"
python -u setup_db.py || echo "setup_db.py falhou mas continuando..."

echo "--- Subindo gunicorn porta $PORT ---"
exec gunicorn servidor:app --bind "0.0.0.0:$PORT" --workers 2 --timeout 120
