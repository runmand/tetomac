"""
coletar_historico.py — Coleta o histórico anual de todos os municípios
e salva no banco PostgreSQL.

Roda em background no Railway via start.sh ou manualmente.
Pode ser interrompido e retomado — pula municípios já coletados.

Uso:
    python coletar_historico.py
    python coletar_historico.py --uf DF        # só um estado
    python coletar_historico.py --forcar        # recoleta mesmo os já salvos
"""

import os, re, io, sys, time, argparse, warnings
from pathlib import Path

import psycopg2, psycopg2.extras
import openpyxl
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

warnings.filterwarnings("ignore")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
URL_SISMAC   = "https://sismac.saude.gov.br/teto_financeiro_anual"

def get_conn():
    return psycopg2.connect(DATABASE_URL)

# ─────────────────────────────────────────────────────────────
# BANCO
# ─────────────────────────────────────────────────────────────

def criar_tabela_historico(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS historico (
                id           SERIAL PRIMARY KEY,
                cod_ibge     TEXT NOT NULL,
                cod_gestao   TEXT NOT NULL,
                ano          INTEGER NOT NULL,
                valor_total  NUMERIC,
                var_valor    NUMERIC,
                var_pct      NUMERIC,
                coletado_em  TIMESTAMP DEFAULT NOW(),
                UNIQUE(cod_ibge, cod_gestao, ano)
            );
            CREATE INDEX IF NOT EXISTS idx_hist_ibge ON historico(cod_ibge, cod_gestao);
            CREATE INDEX IF NOT EXISTS idx_hist_ano  ON historico(ano);
        """)
        conn.commit()
    print("✅ Tabela historico criada/verificada")

def municipios_pendentes(conn, uf_filtro=None, forcar=False):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if forcar:
            query = "SELECT * FROM localidades"
            params = ()
            if uf_filtro:
                query += " WHERE uf = %s"
                params = (uf_filtro,)
        else:
            # Pula os que já têm histórico coletado
            query = """
                SELECT l.* FROM localidades l
                WHERE NOT EXISTS (
                    SELECT 1 FROM historico h
                    WHERE h.cod_ibge = l.cod_ibge
                    AND h.cod_gestao = l.cod_gestao
                )
            """
            params = ()
            if uf_filtro:
                query += " AND l.uf = %s"
                params = (uf_filtro,)

        query += " ORDER BY l.uf, l.nome"
        cur.execute(query, params)
        return cur.fetchall()

def salvar_historico(conn, cod_ibge, cod_gestao, dados):
    if not dados:
        return 0
    with conn.cursor() as cur:
        for d in dados:
            cur.execute("""
                INSERT INTO historico (cod_ibge, cod_gestao, ano, valor_total, var_valor, var_pct)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (cod_ibge, cod_gestao, ano) DO UPDATE SET
                    valor_total = EXCLUDED.valor_total,
                    var_valor   = EXCLUDED.var_valor,
                    var_pct     = EXCLUDED.var_pct,
                    coletado_em = NOW()
            """, (cod_ibge, cod_gestao, d["ano"], d["valor_total"], d["var_valor"], d["var_pct"]))
    conn.commit()
    return len(dados)

# ─────────────────────────────────────────────────────────────
# PARSE DO EXCEL
# ─────────────────────────────────────────────────────────────

def _parse_num(txt):
    if not txt: return 0.0
    t = re.sub(r"[^\d,\.\-]", "", str(txt).strip())
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    try: return float(t)
    except: return 0.0

def parse_excel(conteudo_bytes):
    wb = openpyxl.load_workbook(io.BytesIO(conteudo_bytes))
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    hi = next((i for i, r in enumerate(rows) if any(r)), 0)
    dados = []
    for row in rows[hi+1:]:
        if not row or not row[1]: continue
        try: ano = int(float(str(row[1])))
        except: continue
        if ano < 2000: continue
        dados.append({
            "ano":        ano,
            "valor_total": float(row[8] or 0),
            "var_valor":   float(row[9] or 0),
            "var_pct":     float(row[10] or 0),
        })
    dados.sort(key=lambda x: x["ano"])
    return dados

def parse_html(page):
    dados = []
    for tr in page.query_selector_all("table tbody tr"):
        tds = tr.query_selector_all("td")
        if len(tds) < 3: continue
        textos = [td.inner_text().strip() for td in tds]
        try: ano = int(re.sub(r"\D","",textos[0]))
        except: continue
        if ano < 2000 or ano > 2100: continue
        if len(textos) >= 10:
            vt = _parse_num(textos[7])
            vv = _parse_num(textos[8])
            vp = _parse_num(textos[9])
        elif len(textos) >= 4:
            vt = _parse_num(textos[1])
            vv = _parse_num(textos[2])
            vp = _parse_num(textos[3])
        else: continue
        dados.append({"ano": ano, "valor_total": vt, "var_valor": vv, "var_pct": vp})
    dados.sort(key=lambda x: x["ano"])
    return dados

# ─────────────────────────────────────────────────────────────
# COLETA VIA PLAYWRIGHT
# ─────────────────────────────────────────────────────────────

def coletar_municipio(page, municipio):
    """Acessa o SISMAC e coleta o histórico de um município."""
    nome      = municipio["nome"]
    uf        = municipio["uf"]
    busca     = nome
    aba       = "Município" if municipio["desc_gestao"] == "Gestão Municipal" else "Estado"

    # Se for Total UF ou Gestão Estadual, busca pelo nome do estado
    if municipio["desc_gestao"] in ("Total UF", "Gestão Estadual"):
        busca = uf
        aba   = "Estado"

    try:
        page.goto(URL_SISMAC, wait_until="networkidle", timeout=90000)
        time.sleep(3)

        # Clica na aba correta
        try:
            page.get_by_role("tab", name=aba).click()
            time.sleep(1)
        except:
            try: page.click(f"text={aba}"); time.sleep(1)
            except: pass

        # Digita no autocomplete
        campo = page.locator("input.ui-autocomplete-input").first
        campo.wait_for(timeout=15000)
        campo.click()
        campo.fill("")
        time.sleep(0.5)
        campo.type(busca[:5], delay=150)
        time.sleep(3)

        # Clica na sugestão
        page.wait_for_selector(".ui-autocomplete-item", timeout=10000)
        sugestoes = page.locator(".ui-autocomplete-item").all()
        clicou = False
        for s in sugestoes:
            texto = s.inner_text().strip().upper()
            if busca.upper().split()[0] in texto:
                s.click()
                clicou = True
                break
        if not clicou and sugestoes:
            sugestoes[0].click()
        time.sleep(1)

        # Pesquisa
        for sel in ["button[id*='pesquis']","span[id*='pesquis']",
                    "a[id*='pesquis']",".fa-search"]:
            btn = page.query_selector(sel)
            if btn: btn.click(); break
        else:
            page.keyboard.press("Enter")

        page.wait_for_load_state("networkidle", timeout=40000)
        time.sleep(4)

        # Tenta Excel
        try:
            with page.expect_download(timeout=8000) as dl:
                btn = page.query_selector(
                    "a img[src*='excel'],img[src*='excel'],"
                    "a[href*='excel'],img[src*='xls']"
                )
                if not btn: raise Exception("sem botão Excel")
                btn.click()
            dados = parse_excel(Path(dl.value.path()).read_bytes())
        except:
            dados = parse_html(page)

        return dados

    except Exception as e:
        print(f"      ⚠️  Erro: {e}")
        return []

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--uf",     default="", help="Coletar só uma UF (ex: DF)")
    parser.add_argument("--forcar", action="store_true", help="Recoleta mesmo os já salvos")
    parser.add_argument("--delay",  type=float, default=1.0, help="Delay entre municípios (seg)")
    args = parser.parse_args()

    if not DATABASE_URL:
        print("❌ DATABASE_URL não definida")
        sys.exit(1)

    conn = get_conn()
    criar_tabela_historico(conn)

    pendentes = municipios_pendentes(conn, args.uf.upper() or None, args.forcar)
    total = len(pendentes)
    print(f"\n📋 {total} municípios para coletar")
    if total == 0:
        print("✅ Tudo já coletado!")
        conn.close()
        return

    print(f"⏱️  Tempo estimado: ~{total * 35 // 60} minutos\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox",
                  "--disable-dev-shm-usage","--disable-gpu"]
        )
        context = browser.new_context(accept_downloads=True)
        page    = context.new_page()
        page.on("dialog", lambda d: d.accept())

        ok = 0; erro = 0; vazio = 0

        for i, m in enumerate(pendentes):
            print(f"[{i+1}/{total}] {m['nome']} ({m['uf']}) — {m['desc_gestao']}", end=" ", flush=True)

            dados = coletar_municipio(page, m)

            if dados:
                n = salvar_historico(conn, m["cod_ibge"], m["cod_gestao"], dados)
                print(f"→ {n} anos ✅")
                ok += 1
            else:
                print("→ sem dados ⚠️")
                # Salva um registro vazio para não tentar de novo
                salvar_historico(conn, m["cod_ibge"], m["cod_gestao"], [
                    {"ano": 0, "valor_total": 0, "var_valor": 0, "var_pct": 0}
                ])
                vazio += 1

            # Progresso a cada 50
            if (i+1) % 50 == 0:
                conn2 = get_conn()
                with conn2.cursor() as cur:
                    cur.execute("SELECT COUNT(DISTINCT cod_ibge||cod_gestao) FROM historico WHERE ano > 0")
                    total_banco = cur.fetchone()[0]
                conn2.close()
                print(f"\n   📊 Progresso: {i+1}/{total} | Com dados: {ok} | Vazios: {vazio} | Banco: {total_banco}\n")

            time.sleep(args.delay)

        browser.close()

    conn.close()
    print(f"\n🎉 Concluído! ✅ {ok} coletados | ⚠️ {vazio} sem dados")

if __name__ == "__main__":
    main()
