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
    
    # Mostra o que tem no histórico
    cur.execute("SELECT cod_ibge, cod_gestao, COUNT(*) FROM historico WHERE ano > 0 GROUP BY cod_ibge, cod_gestao")
    rows = cur.fetchall()
    print(f"Historico grupos: {rows}")
    
    # Mostra localidades do DF
    cur.execute("SELECT cod_ibge, cod_gestao, nome, uf, desc_gestao FROM localidades WHERE uf='DF'")
    locs = cur.fetchall()
    print(f"Localidades DF: {locs}")
    
    # Simula a busca
    cur.execute("""SELECT cod_ibge, cod_gestao, nome FROM localidades
                  WHERE (nome ILIKE %s OR uf ILIKE %s)
                  AND desc_gestao IN ('Total UF','Gestão Estadual')
                  ORDER BY desc_gestao DESC LIMIT 1""", ("%DF%", "DF"))
    loc = cur.fetchone()
    print(f"Busca por 'DF': {loc}")
    
    if loc:
        cur.execute("SELECT ano, valor_total FROM historico WHERE cod_ibge=%s AND cod_gestao=%s AND ano>0 ORDER BY ano",
                   (loc[0], loc[1]))
        hist = cur.fetchall()
        print(f"Histórico encontrado: {hist}")
    
    # Importa se vazio
    cur.execute("SELECT COUNT(*) FROM historico WHERE ano > 0")
    total = cur.fetchone()[0]
    if total == 0 and os.path.exists("importar_railway.sql"):
        print("Importando SQL...")
        cur.execute(open("importar_railway.sql", encoding="utf-8").read())
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM historico WHERE ano > 0")
        print(f"Importado: {cur.fetchone()[0]} registros")
    
    conn.close()
except Exception as e:
    print(f"Erro: {e}")
    import traceback; traceback.print_exc()
