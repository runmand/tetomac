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
        print("Tabelas removidas, recriando...")
        sql = open("importar_railway.sql", encoding="utf-8").read()
        cur.execute(sql)
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM localidades")
        print(f"✅ Localidades: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM historico WHERE ano > 0")
        print(f"✅ Historico: {cur.fetchone()[0]} registros")
        cur.execute("SELECT DISTINCT uf FROM localidades ORDER BY uf")
        ufs = [r[0] for r in cur.fetchall()]
        print(f"✅ UFs: {ufs}")
        # Verifica AM
        cur.execute("SELECT valor_total FROM historico WHERE cod_ibge='130000' AND ano=2022")
        am = cur.fetchone()
        print(f"✅ AM 2022: {am}")
    else:
        print("Sem arquivo SQL")

    conn.close()
except Exception as e:
    print(f"Erro: {e}")
    import traceback; traceback.print_exc()
