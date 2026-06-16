"""
Coleta histórico anual via requests (sem navegador).
Acessa a página de detalhes do SISMAC via POST JSF.
"""
import os, re, io, sys, time, warnings
from pathlib import Path
import psycopg2
import requests
from bs4 import BeautifulSoup
import openpyxl

warnings.filterwarnings("ignore")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
BASE = "https://sismac.saude.gov.br"
URL_ANUAL = BASE + "/teto_financeiro_anual"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": BASE + "/inicio",
}

def get_conn():
    return psycopg2.connect(DATABASE_URL)

def criar_tabela(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS historico (
                id SERIAL PRIMARY KEY,
                cod_ibge TEXT, cod_gestao TEXT, ano INTEGER,
                valor_total NUMERIC, var_valor NUMERIC, var_pct NUMERIC,
                coletado_em TIMESTAMP DEFAULT NOW(),
                UNIQUE(cod_ibge, cod_gestao, ano)
            );
            CREATE INDEX IF NOT EXISTS idx_hist ON historico(cod_ibge, cod_gestao);
        """)
        conn.commit()

def _parse_brl(txt):
    t = str(txt or "").strip().replace(".", "").replace(",", ".")
    try: return float(t)
    except: return 0.0

def nova_sessao():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get(BASE + "/inicio", timeout=15)
    return s

def get_page_state(session):
    """Carrega a página e retorna (soup, form_id, view_state)."""
    r = session.get(URL_ANUAL, timeout=30)
    soup = BeautifulSoup(r.text, "lxml")
    form = soup.find("form")
    form_id = form.get("id", "formFiltro") if form else "formFiltro"
    vs = soup.find("input", {"name": "javax.faces.ViewState"})
    view_state = vs["value"] if vs else ""
    return soup, form_id, view_state

def buscar_por_ibge(session, cod_ibge, nome, soup, form_id, view_state):
    """
    Tenta selecionar o município via AJAX do autocomplete JSF
    e retorna os dados históricos.
    """
    # O autocomplete do SISMAC usa um componente PrimeFaces
    # Vamos tentar via AJAX query do autocomplete
    ajax_data = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": f"{form_id}:autoCompleteMunicipio",
        "javax.faces.partial.execute": f"{form_id}:autoCompleteMunicipio",
        "javax.faces.partial.render": f"{form_id}:autoCompleteMunicipio",
        f"{form_id}:autoCompleteMunicipio_query": nome[:5],
        form_id: form_id,
        "javax.faces.ViewState": view_state,
    }

    try:
        r = session.post(URL_ANUAL, data=ajax_data, timeout=30)
        soup_ajax = BeautifulSoup(r.text, "lxml")
        
        # Procura sugestões
        itens = soup_ajax.find_all("li", class_=re.compile("ui-autocomplete"))
        if not itens:
            # Tenta outro formato
            itens = soup_ajax.find_all("item")
        
        item_val = None
        for item in itens:
            texto = item.get_text(strip=True).upper()
            if nome.upper().split()[0] in texto:
                item_val = item.get("data-item-value") or item.get("value") or item.get_text(strip=True)
                break
        
        if not item_val and itens:
            item_val = itens[0].get("data-item-value") or itens[0].get_text(strip=True)

        if not item_val:
            return []

        # Seleciona o item e pesquisa
        select_data = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": f"{form_id}:autoCompleteMunicipio",
            "javax.faces.partial.execute": f"{form_id}:autoCompleteMunicipio",
            "javax.faces.partial.render": form_id,
            f"{form_id}:autoCompleteMunicipio_input": item_val,
            f"{form_id}:autoCompleteMunicipio": item_val,
            form_id: form_id,
            "javax.faces.ViewState": view_state,
        }
        r2 = session.post(URL_ANUAL, data=select_data, timeout=30)
        
        # Atualiza view state
        soup2 = BeautifulSoup(r2.text, "lxml")
        vs2 = soup2.find("input", {"name": "javax.faces.ViewState"})
        if vs2: view_state = vs2["value"]

        # Clica em pesquisar
        pesq_data = {
            form_id: form_id,
            f"{form_id}:autoCompleteMunicipio_input": item_val,
            "javax.faces.ViewState": view_state,
        }
        # Adiciona botão de pesquisa
        for inp in soup.find_all("input", type="submit"):
            pesq_data[inp.get("name","")] = inp.get("value","")
        for btn in soup.find_all("button"):
            if "pesquis" in btn.get("id","").lower():
                pesq_data[btn.get("name","btn")] = btn.get("value","")

        r3 = session.post(URL_ANUAL, data=pesq_data, timeout=30)
        return _extrair_tabela(BeautifulSoup(r3.text, "lxml"))

    except Exception as e:
        return []

def _extrair_tabela(soup):
    dados = []
    for tabela in soup.find_all("table"):
        ths = [th.get_text(strip=True).lower() for th in tabela.find_all("th")]
        if any("refer" in h or "ano" in h for h in ths):
            for tr in tabela.find_all("tr")[1:]:
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) < 3: continue
                try: ano = int(re.sub(r"\D","",tds[0]))
                except: continue
                if ano < 2000 or ano > 2100: continue
                if len(tds) >= 10:
                    vt,vv,vp = _parse_brl(tds[7]),_parse_brl(tds[8]),_parse_brl(tds[9])
                elif len(tds) >= 4:
                    vt,vv,vp = _parse_brl(tds[1]),_parse_brl(tds[2]),_parse_brl(tds[3])
                else: continue
                dados.append({"ano":ano,"valor_total":vt,"var_valor":vv,"var_pct":vp})
            if dados: break
    return dados

def salvar(conn, cod_ibge, cod_gestao, dados):
    with conn.cursor() as cur:
        for d in dados:
            cur.execute("""
                INSERT INTO historico (cod_ibge,cod_gestao,ano,valor_total,var_valor,var_pct)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (cod_ibge,cod_gestao,ano) DO UPDATE SET
                    valor_total=EXCLUDED.valor_total, var_valor=EXCLUDED.var_valor,
                    var_pct=EXCLUDED.var_pct, coletado_em=NOW()
            """, (cod_ibge,cod_gestao,d["ano"],d["valor_total"],d["var_valor"],d["var_pct"]))
    conn.commit()

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--uf", default="")
    parser.add_argument("--forcar", action="store_true")
    parser.add_argument("--delay", type=float, default=1.5)
    args = parser.parse_args()

    conn = get_conn()
    criar_tabela(conn)

    query = "SELECT * FROM localidades"
    params = []
    if args.uf:
        query += " WHERE uf=%s"
        params = [args.uf.upper()]
    if not args.forcar:
        sub = " AND" if args.uf else " WHERE"
        query += f"{sub} NOT EXISTS (SELECT 1 FROM historico h WHERE h.cod_ibge=localidades.cod_ibge AND h.cod_gestao=localidades.cod_gestao)"
    query += " ORDER BY uf, nome"

    import psycopg2.extras
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        pendentes = cur.fetchall()

    total = len(pendentes)
    print(f"📋 {total} municípios pendentes")
    if not total:
        print("✅ Tudo coletado!"); conn.close(); return

    session = nova_sessao()
    soup, form_id, vs = get_page_state(session)
    print(f"   form_id: {form_id}")

    ok=0; erro=0
    for i, m in enumerate(pendentes):
        print(f"[{i+1}/{total}] {m['nome']} ({m['uf']})", end=" ", flush=True)

        dados = buscar_por_ibge(session, m["cod_ibge"], m["nome"], soup, form_id, vs)

        if dados:
            salvar(conn, m["cod_ibge"], m["cod_gestao"], dados)
            print(f"→ {len(dados)} anos ✅")
            ok += 1
        else:
            print("→ sem dados ⚠️")
            erro += 1
            # Renova sessão a cada 50 erros
            if erro % 50 == 0:
                session = nova_sessao()
                soup, form_id, vs = get_page_state(session)

        if (i+1) % 100 == 0:
            print(f"\n   📊 {i+1}/{total} | OK:{ok} | Erro:{erro}\n")

        time.sleep(args.delay)

    conn.close()
    print(f"\n🎉 Concluído! OK:{ok} | Sem dados:{erro}")

if __name__ == "__main__":
    main()
