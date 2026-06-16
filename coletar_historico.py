"""
Coleta histórico anual de todos os municípios do SISMAC e salva no banco.
Pode ser interrompido e retomado — pula o que já foi coletado.

Uso:
    python coletar_historico.py --db "postgresql://..." --uf DF
    python coletar_historico.py --db "postgresql://..."
    python coletar_historico.py --db "postgresql://..." --forcar
"""
import os, re, sys, time, warnings, argparse
import psycopg2, psycopg2.extras
import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")

BASE      = "https://sismac.saude.gov.br"
URL_ANUAL = BASE + "/teto_financeiro_anual"
URL_LISTA = BASE + "/teto_financeiro_brasil_por_estado_municipio"
TABELA_ID = "tetoFinanceiroBrasil"
FORM_ID   = "formTemplate"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

DB_URL = ""

def get_conn():
    return psycopg2.connect(DB_URL)

def criar_tabela(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS historico (
                id          SERIAL PRIMARY KEY,
                cod_ibge    TEXT NOT NULL,
                cod_gestao  TEXT NOT NULL,
                ano         INTEGER NOT NULL,
                valor_total NUMERIC,
                var_valor   NUMERIC,
                var_pct     NUMERIC,
                coletado_em TIMESTAMP DEFAULT NOW(),
                UNIQUE(cod_ibge, cod_gestao, ano)
            );
            CREATE INDEX IF NOT EXISTS idx_hist ON historico(cod_ibge, cod_gestao);
        """)
        conn.commit()
    print("✅ Tabela historico OK")

def _parse_brl(txt):
    t = str(txt or "").strip().replace(".", "").replace(",", ".")
    try: return float(t)
    except: return 0.0

# ── SESSÃO HTTP ──

def nova_sessao():
    s = requests.Session()
    s.headers.update(HEADERS)
    try: s.get(BASE + "/inicio", timeout=15)
    except: pass
    return s

def get_vs(soup):
    vs = soup.find("input", {"name": "javax.faces.ViewState"})
    return vs["value"] if vs else ""

def extrair_cdata(xml):
    m = re.search(r"<!\[CDATA\[(.*?)\]\]>", xml, re.DOTALL)
    return m.group(1) if m else xml

# ── COLETA MUNICÍPIOS ──

def carregar_municipios(conn, session, uf_filtro=None, forcar=False):
    """Retorna lista de municípios pendentes do banco."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if forcar:
            q = "SELECT * FROM localidades"
            p = []
            if uf_filtro:
                q += " WHERE uf=%s"
                p = [uf_filtro]
        else:
            q = """SELECT l.* FROM localidades l
                   WHERE NOT EXISTS (
                       SELECT 1 FROM historico h
                       WHERE h.cod_ibge=l.cod_ibge AND h.cod_gestao=l.cod_gestao
                   )"""
            p = []
            if uf_filtro:
                q += " AND l.uf=%s"
                p = [uf_filtro]
        q += " ORDER BY uf, nome"
        cur.execute(q, p)
        return cur.fetchall()

# ── BUSCA HISTÓRICO VIA POST JSF ──

def buscar_historico(session, municipio):
    """
    Usa a página teto_financeiro_anual com POST JSF para buscar
    o histórico de um município.
    Fluxo: carrega página → dispara autocomplete → seleciona → pesquisa → lê tabela
    """
    nome = municipio["nome"]
    uf   = municipio["uf"]
    desc = municipio["desc_gestao"]

    # Para Total UF / Gestão Estadual busca pelo estado, aba Estado
    if desc in ("Total UF", "Gestão Estadual"):
        busca_texto = uf
        tipo = "estado"
    else:
        busca_texto = nome
        tipo = "municipio"

    try:
        # 1. Carrega a página
        r = session.get(URL_ANUAL, timeout=30)
        soup = BeautifulSoup(r.text, "lxml")
        vs = get_vs(soup)
        form = soup.find("form")
        form_id = form.get("id","formFiltro") if form else "formFiltro"

        # Descobre o id do campo autocomplete
        ac_input = soup.find("input", class_="ui-autocomplete-input")
        if not ac_input:
            ac_input = soup.find("input", {"type": "text"})
        ac_id = ac_input.get("id", f"{form_id}:nomeLocalidade") if ac_input else f"{form_id}:nomeLocalidade"
        ac_name = ac_input.get("name", ac_id) if ac_input else ac_id

        # 2. Clica na aba correta (Estado ou Município) via POST
        if tipo == "estado":
            # Tenta clicar na aba Estado
            aba_id = None
            for a in soup.find_all(["a","li","span"]):
                txt = a.get_text(strip=True).lower()
                if txt == "estado":
                    aba_id = a.get("id","")
                    break
            if aba_id:
                r_aba = session.post(URL_ANUAL, timeout=20, data={
                    "javax.faces.partial.ajax": "true",
                    "javax.faces.source": aba_id,
                    "javax.faces.partial.execute": "@all",
                    "javax.faces.partial.render": "@all",
                    aba_id: aba_id,
                    form_id: form_id,
                    "javax.faces.ViewState": vs,
                })
                soup = BeautifulSoup(r_aba.text, "lxml")
                vs2 = get_vs(soup)
                if vs2: vs = vs2

        # 3. Dispara AJAX do autocomplete com os primeiros 5 chars
        query_data = {
            "javax.faces.partial.ajax":    "true",
            "javax.faces.source":          ac_id,
            "javax.faces.partial.execute": ac_id,
            "javax.faces.partial.render":  ac_id,
            f"{ac_id}_query":              busca_texto[:5],
            ac_name:                       busca_texto[:5],
            form_id:                       form_id,
            "javax.faces.ViewState":       vs,
        }
        r_ac = session.post(URL_ANUAL, data=query_data, timeout=20)
        soup_ac = BeautifulSoup(r_ac.text, "lxml")

        # Extrai sugestões do XML de resposta
        cdata = extrair_cdata(r_ac.text)
        soup_sug = BeautifulSoup(cdata, "lxml")

        item_label = None
        item_value = None

        # Tenta encontrar o item que corresponde ao nome buscado
        for item in soup_sug.find_all(["li","item","div"], class_=re.compile("autocomplete|item",re.I)):
            txt = item.get_text(strip=True).upper()
            if busca_texto.upper().split()[0] in txt:
                item_label = item.get_text(strip=True)
                item_value = item.get("data-item-value") or item.get("value") or item_label
                break

        # Se não achou, pega o primeiro
        if not item_value:
            todos = soup_sug.find_all(["li","item"])
            if todos:
                item_label = todos[0].get_text(strip=True)
                item_value = todos[0].get("data-item-value") or item_label

        if not item_value:
            return []

        # 4. Seleciona o item via POST
        select_data = {
            "javax.faces.partial.ajax":    "true",
            "javax.faces.source":          ac_id,
            "javax.faces.partial.execute": ac_id,
            "javax.faces.partial.render":  form_id,
            f"{ac_id}_input":              item_value,
            ac_name:                       item_value,
            form_id:                       form_id,
            "javax.faces.ViewState":       vs,
        }
        r_sel = session.post(URL_ANUAL, data=select_data, timeout=20)
        soup_sel = BeautifulSoup(r_sel.text, "lxml")
        vs3 = get_vs(soup_sel)
        if vs3: vs = vs3

        # 5. Coleta todos os inputs do form para o POST de pesquisa
        post_pesq = {}
        form_el = soup.find("form", id=form_id) or soup.find("form")
        if form_el:
            for inp in form_el.find_all("input"):
                n = inp.get("name","")
                if n: post_pesq[n] = inp.get("value","")
        post_pesq[ac_name]              = item_value
        post_pesq["javax.faces.ViewState"] = vs

        # Adiciona botão de pesquisa
        for btn in (soup.find_all("input", type="submit") + soup.find_all("button")):
            bid = btn.get("id","")
            if "pesquis" in bid.lower() or "buscar" in bid.lower():
                post_pesq[btn.get("name", bid)] = btn.get("value","Pesquisar")
                break

        r_pesq = session.post(URL_ANUAL, data=post_pesq, timeout=30)
        soup_result = BeautifulSoup(r_pesq.text, "lxml")

        return extrair_tabela(soup_result)

    except Exception as e:
        return []


def extrair_tabela(soup):
    """Extrai dados da tabela de histórico anual."""
    dados = []
    for tabela in soup.find_all("table"):
        ths = [th.get_text(strip=True).lower() for th in tabela.find_all("th")]
        if any(k in " ".join(ths) for k in ["refer", "ano", "teto", "financ"]):
            for tr in tabela.find_all("tr")[1:]:
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) < 3: continue
                try: ano = int(re.sub(r"\D","",tds[0]))
                except: continue
                if ano < 2000 or ano > 2100: continue
                # Teto MAC = últimas 3 colunas de 10
                if len(tds) >= 10:
                    vt,vv,vp = _parse_brl(tds[7]),_parse_brl(tds[8]),_parse_brl(tds[9])
                elif len(tds) >= 4:
                    vt,vv,vp = _parse_brl(tds[1]),_parse_brl(tds[2]),_parse_brl(tds[3])
                else: continue
                dados.append({"ano":ano,"valor_total":vt,"var_valor":vv,"var_pct":vp})
            if dados: break
    return sorted(dados, key=lambda x: x["ano"])

def salvar(conn, cod_ibge, cod_gestao, dados):
    with conn.cursor() as cur:
        for d in dados:
            cur.execute("""
                INSERT INTO historico (cod_ibge,cod_gestao,ano,valor_total,var_valor,var_pct)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (cod_ibge,cod_gestao,ano) DO UPDATE SET
                    valor_total=EXCLUDED.valor_total,
                    var_valor=EXCLUDED.var_valor,
                    var_pct=EXCLUDED.var_pct,
                    coletado_em=NOW()
            """, (cod_ibge,cod_gestao,d["ano"],d["valor_total"],d["var_valor"],d["var_pct"]))
    conn.commit()

# ── MAIN ──

def main():
    global DB_URL

    parser = argparse.ArgumentParser()
    parser.add_argument("--db",     default=os.environ.get("DATABASE_URL",""), help="URL do PostgreSQL")
    parser.add_argument("--uf",     default="", help="Coletar só uma UF (ex: DF)")
    parser.add_argument("--forcar", action="store_true")
    parser.add_argument("--delay",  type=float, default=1.5)
    args = parser.parse_args()

    if not args.db:
        print("❌ Informe --db 'postgresql://...'")
        sys.exit(1)

    DB_URL = args.db
    print(f"🔌 Conectando ao banco...")
    conn = get_conn()
    criar_tabela(conn)

    session = nova_sessao()
    pendentes = carregar_municipios(conn, session, args.uf.upper() or None, args.forcar)
    total = len(pendentes)
    print(f"📋 {total} municípios pendentes")
    if not total:
        print("✅ Tudo já coletado!"); conn.close(); return

    print(f"⏱️  Estimativa: ~{total*3//60} minutos\n")

    ok=0; sem_dados=0
    for i, m in enumerate(pendentes):
        print(f"[{i+1}/{total}] {m['nome']} ({m['uf']})", end=" ", flush=True)

        dados = buscar_historico(session, m)

        if dados:
            salvar(conn, m["cod_ibge"], m["cod_gestao"], dados)
            print(f"→ {len(dados)} anos ✅")
            ok += 1
        else:
            print("→ sem dados ⚠️")
            sem_dados += 1
            # Marca como tentado para não repetir
            salvar(conn, m["cod_ibge"], m["cod_gestao"],
                   [{"ano":0,"valor_total":0,"var_valor":0,"var_pct":0}])

        if (i+1) % 50 == 0:
            print(f"\n   📊 {i+1}/{total} | ✅ {ok} | ⚠️ {sem_dados}\n")

        time.sleep(args.delay)

    conn.close()
    print(f"\n🎉 Concluído! ✅ {ok} coletados | ⚠️ {sem_dados} sem dados")

if __name__ == "__main__":
    main()
