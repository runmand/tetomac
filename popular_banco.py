"""
popular_banco.py — Roda UMA VEZ para popular o banco com todos os
estados e municípios do SISMAC.

Uso local:
    pip install playwright psycopg2-binary
    python popular_banco.py --db "postgresql://user:pass@host:5432/railway"

No Railway (via terminal do serviço):
    python popular_banco.py
    (usa automaticamente a variável DATABASE_URL)
"""

import os, re, sys, time, argparse
import psycopg2
from playwright.sync_api import sync_playwright

URL_LISTA = "https://sismac.saude.gov.br/teto_financeiro_brasil_por_estado_municipio"
TABELA_ID = "tetoFinanceiroBrasil"
FORM_ID   = "formTemplate"

def criar_tabelas(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS localidades (
                id           SERIAL PRIMARY KEY,
                regiao       TEXT,
                uf           TEXT,
                cod_ibge     TEXT,
                nome         TEXT,
                cod_gestao   TEXT,
                desc_gestao  TEXT,
                teto_atual   NUMERIC,
                atualizado   TIMESTAMP DEFAULT NOW(),
                UNIQUE(cod_ibge, cod_gestao)
            );
            CREATE INDEX IF NOT EXISTS idx_uf   ON localidades(uf);
            CREATE INDEX IF NOT EXISTS idx_nome ON localidades(nome);
        """)
        conn.commit()
    print("✅ Tabelas criadas/verificadas")

def extrair_cdata(xml):
    m = re.search(r"<!\[CDATA\[(.*?)\]\]>", xml, re.DOTALL)
    return m.group(1) if m else xml

def parse_brl(txt):
    t = str(txt).strip().replace(".", "").replace(",", ".")
    try: return float(t)
    except: return 0.0

def coletar_municipios():
    print("🌐 Coletando municípios do SISMAC...")
    municipios = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--no-sandbox","--disable-setuid-sandbox",
            "--disable-dev-shm-usage","--disable-gpu"
        ])
        page = browser.new_page()
        page.on("dialog", lambda d: d.accept())

        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Accept-Language": "pt-BR,pt;q=0.9",
        }
        session = requests.Session()
        session.headers.update(headers)
        session.get("https://sismac.saude.gov.br/inicio", timeout=15)

        r = session.get(URL_LISTA, timeout=30)
        soup = BeautifulSoup(r.text, "lxml")
        vs_inp = soup.find("input", {"name": "javax.faces.ViewState"})
        vs = vs_inp["value"] if vs_inp else ""

        def _add(html_frag):
            s = BeautifulSoup(html_frag, "lxml")
            for tr in s.find_all("tr"):
                spans = tr.find_all("span", title=True)
                if len(spans) >= 7:
                    vals = [sp["title"] for sp in spans[:7]]
                else:
                    tds = tr.find_all("td")
                    if len(tds) < 7: continue
                    vals = [td.get_text(strip=True) for td in tds[:7]]
                regiao, uf, cod_ibge, nome, cod_gestao, desc_gestao, teto = vals
                if not re.match(r"^\d{6}$", cod_ibge): continue
                k = cod_ibge + "_" + cod_gestao
                municipios[k] = {
                    "regiao": regiao, "uf": uf, "cod_ibge": cod_ibge,
                    "nome": nome, "cod_gestao": cod_gestao,
                    "desc_gestao": desc_gestao, "teto_atual": parse_brl(teto)
                }

        _add(r.text)
        print(f"   Página 1: {len(municipios)} registros")

        # Muda para 1000/página
        r2 = session.post(URL_LISTA, timeout=60, data={
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": TABELA_ID,
            "javax.faces.partial.execute": TABELA_ID,
            "javax.faces.partial.render": TABELA_ID,
            f"{TABELA_ID}_encodeFeature": "true",
            f"{TABELA_ID}_rppDD": "1000",
            FORM_ID: FORM_ID,
            "javax.faces.ViewState": vs,
        })
        _add(extrair_cdata(r2.text))
        rows_pp = 1000
        sv2 = BeautifulSoup(r2.text, "lxml").find("input", {"name": "javax.faces.ViewState"})
        if sv2: vs = sv2["value"]
        print(f"   Após 1000/pág: {len(municipios)} registros")

        # Pagina restante
        pagina = 2
        sem_nov = 0
        while pagina <= 100:
            first = (pagina - 1) * rows_pp
            try:
                resp = session.post(URL_LISTA, timeout=30, data={
                    "javax.faces.partial.ajax": "true",
                    "javax.faces.source": TABELA_ID,
                    "javax.faces.partial.execute": TABELA_ID,
                    "javax.faces.partial.render": TABELA_ID,
                    f"{TABELA_ID}_pagination": "true",
                    f"{TABELA_ID}_first": str(first),
                    f"{TABELA_ID}_rows": str(rows_pp),
                    f"{TABELA_ID}_skipChildren": "true",
                    f"{TABELA_ID}_encodeFeature": "true",
                    FORM_ID: FORM_ID,
                    "javax.faces.ViewState": vs,
                })
                antes = len(municipios)
                _add(extrair_cdata(resp.text))
                novos = len(municipios) - antes
                print(f"   Página {pagina}: +{novos} | total: {len(municipios)}")
                sv = BeautifulSoup(resp.text, "lxml").find("input", {"name": "javax.faces.ViewState"})
                if sv: vs = sv["value"]
                sem_nov = 0 if novos else sem_nov + 1
                if sem_nov >= 2: break
            except Exception as e:
                print(f"   ⚠️ Erro p.{pagina}: {e}")
                sem_nov += 1
                if sem_nov >= 2: break
            pagina += 1
            time.sleep(0.3)

        browser.close()

    return list(municipios.values())


def salvar_banco(conn, municipios):
    print(f"\n💾 Salvando {len(municipios)} registros no banco...")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM localidades")
        for m in municipios:
            cur.execute("""
                INSERT INTO localidades
                    (regiao, uf, cod_ibge, nome, cod_gestao, desc_gestao, teto_atual)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (cod_ibge, cod_gestao) DO UPDATE SET
                    teto_atual = EXCLUDED.teto_atual,
                    atualizado = NOW()
            """, (m["regiao"], m["uf"], m["cod_ibge"], m["nome"],
                  m["cod_gestao"], m["desc_gestao"], m["teto_atual"]))
        conn.commit()
    print(f"✅ {len(municipios)} registros salvos!")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL",""),
                        help="URL do PostgreSQL")
    args = parser.parse_args()

    if not args.db:
        print("❌ Informe a URL do banco: --db 'postgresql://...' ou DATABASE_URL")
        sys.exit(1)

    print(f"🔌 Conectando ao banco...")
    conn = psycopg2.connect(args.db)
    criar_tabelas(conn)

    municipios = coletar_municipios()
    salvar_banco(conn, municipios)
    conn.close()
    print("\n🎉 Banco populado com sucesso!")


if __name__ == "__main__":
    main()
