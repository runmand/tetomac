import os, psycopg2
from servidor import init_db

print("Iniciando banco...")
init_db()

db = os.environ.get("DATABASE_URL", "")
if not db:
    print("Sem DATABASE_URL"); exit()

try:
    conn = psycopg2.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM historico WHERE ano > 0")
    total = cur.fetchone()[0]
    print(f"Historico: {total} registros")
    if total == 0 and os.path.exists("importar_railway.sql"):
        print("Importando SQL...")
        sql = open("importar_railway.sql", encoding="utf-8").read()
        cur.execute(sql)
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM historico WHERE ano > 0")
        print(f"Importado: {cur.fetchone()[0]} registros")
    conn.close()
except Exception as e:
    print(f"Erro: {e}")
