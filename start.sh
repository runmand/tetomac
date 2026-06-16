#!/bin/bash
echo "🚀 Iniciando..."
echo "PORT: ${PORT}"
echo "DATABASE_URL definida: ${DATABASE_URL:+sim}"

python -u -c "
from servidor import init_db
init_db()
"

# Importa histórico se o arquivo existir e o banco estiver vazio
python -u -c "
import os, psycopg2
db = os.environ.get('DATABASE_URL','')
if not db:
    print('Sem DATABASE_URL')
    exit()
try:
    conn = psycopg2.connect(db)
    cur = conn.cursor()
    cur.execute(\"SELECT COUNT(*) FROM historico WHERE ano > 0\")
    total = cur.fetchone()[0]
    print(f'Historico no banco: {total} registros')
    if total == 0 and os.path.exists('importar_railway.sql'):
        print('Importando historico do SQL...')
        sql = open('importar_railway.sql', encoding='utf-8').read()
        cur.execute(sql)
        conn.commit()
        cur.execute(\"SELECT COUNT(*) FROM historico WHERE ano > 0\")
        total2 = cur.fetchone()[0]
        print(f'Importado! {total2} registros')
    conn.close()
except Exception as e:
    print(f'Erro historico: {e}')
"

echo "▶️  Subindo gunicorn na porta ${PORT}..."
exec gunicorn servidor:app --bind "0.0.0.0:${PORT}" --workers 2 --timeout 120
