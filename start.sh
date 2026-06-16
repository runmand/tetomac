#!/bin/bash
echo "🚀 Iniciando..."
echo "PORT: ${PORT}"
echo "DATABASE_URL definida: ${DATABASE_URL:+sim}"

python -u -c "
from servidor import init_db
init_db()
"

echo "▶️  Subindo gunicorn na porta ${PORT}..."
exec gunicorn servidor:app --bind "0.0.0.0:${PORT}" --workers 2 --timeout 120
