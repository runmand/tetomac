#!/bin/bash
set -e
echo "🚀 Iniciando..."
echo "DATABASE_URL definida: ${DATABASE_URL:+sim}"
python -u -c "
import os
print('Python OK')
print('DATABASE_URL:', 'sim' if os.environ.get('DATABASE_URL') else 'NAO DEFINIDA')
from servidor import init_db
print('Chamando init_db...')
init_db()
print('init_db concluido')
"
echo "▶️  Subindo gunicorn..."
exec gunicorn servidor:app --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 120
