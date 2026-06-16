#!/bin/bash
echo "🚀 Iniciando servidor..."
python -c "from servidor import init_db; init_db()"
echo "▶️  Subindo gunicorn..."
exec gunicorn servidor:app --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 120
