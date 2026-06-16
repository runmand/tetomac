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

    if os.path.exists("importar_railway.sql"):
        print("Limpando banco completamente...")
        cur.execute("DROP TABLE IF EXISTS historico CASCADE")
        cur.execute("DROP TABLE IF EXISTS localidades CASCADE")
        conn.commit()
        print("Recriando com dados novos...")
        sql = open("importar_railway.sql", encoding="utf-8").read()
        cur.execute(sql)
        conn.commit()
        # Só conta depois do commit
        cur.execute("SELECT COUNT(*) FROM localidades")
        print(f"✅ Localidades: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM historico WHERE ano > 0")
        print(f"✅ Historico: {cur.fetchone()[0]} registros")
    else:
        print("Sem arquivo SQL")

    conn.close()
except Exception as e:
    print(f"Erro: {e}")
    import traceback; traceback.print_exc()
