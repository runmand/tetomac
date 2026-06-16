import os, psycopg2
from servidor import init_db

print("Iniciando banco...")
init_db()  # Garante que as tabelas existem

db = os.environ.get("DATABASE_URL", "")
if not db:
    print("Sem DATABASE_URL"); exit()

if not os.path.exists("importar_railway.sql"):
    print("Sem arquivo SQL"); exit()

try:
    conn = psycopg2.connect(db)
    cur = conn.cursor()

    # Limpa só os dados, mantém a estrutura
    print("Limpando dados antigos...")
    cur.execute("DELETE FROM historico")
    cur.execute("DELETE FROM localidades")
    conn.commit()

    # Importa novo SQL
    print("Importando dados novos...")
    sql = open("importar_railway.sql", encoding="utf-8").read()
    cur.execute(sql)
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM localidades")
    print(f"✅ Localidades: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM historico WHERE ano > 0")
    print(f"✅ Historico: {cur.fetchone()[0]} registros")

    conn.close()
except Exception as e:
    print(f"Erro: {e}")
    import traceback; traceback.print_exc()
