import io, re, time, warnings, os
from flask import Flask, request, send_file, jsonify
import psycopg2, psycopg2.extras
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from playwright.sync_api import sync_playwright

warnings.filterwarnings("ignore")

app = Flask(__name__)
URL_SISMAC = "https://sismac.saude.gov.br/teto_financeiro_anual"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ── BANCO ──

def get_conn():
    return psycopg2.connect(DATABASE_URL)

# ── EXCEL / ESTILOS ──

COR_H = "D5DDE5"; COR_P = "EEF2F5"; COR_I = "FFFFFF"; COR_T = "1A2A3A"

def _borda():
    s = Side(style="thin", color="B5C3CC")
    return Border(left=s, right=s, top=s, bottom=s)

def _header(ws, row, cols):
    fill = PatternFill("solid", fgColor=COR_H)
    font = Font(name="Arial", bold=True, size=9, color=COR_T)
    for col in range(1, cols+1):
        c = ws.cell(row=row, column=col)
        c.fill=fill; c.font=font; c.border=_borda()
        c.alignment=Alignment(horizontal="center", vertical="center", wrap_text=True)

def _pct(a, b):
    return (b - a) / a * 100 if a else None

def _parse_num(txt):
    if not txt: return 0.0
    t = re.sub(r"[^\d,\.\-]", "", str(txt).strip())
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    try: return float(t)
    except: return 0.0

def _parse_excel(conteudo, anos_alvo):
    wb = openpyxl.load_workbook(io.BytesIO(conteudo))
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    hi = next((i for i, r in enumerate(rows) if any(r)), 0)
    dados = []
    for row in rows[hi+1:]:
        if not row or not row[1]: continue
        try: ano = int(float(str(row[1])))
        except: continue
        if ano < 2000: continue
        if anos_alvo and ano not in anos_alvo: continue
        dados.append({
            "ano": ano,
            "valor_total":    float(row[8] or 0),
            "variacao_valor": float(row[9] or 0),
            "variacao_pct":   float(row[10] or 0),
        })
    dados.sort(key=lambda x: x["ano"])
    return dados

def _ler_html(page, anos_alvo):
    dados = []
    for tr in page.query_selector_all("table tbody tr"):
        tds = tr.query_selector_all("td")
        if len(tds) < 3: continue
        textos = [td.inner_text().strip() for td in tds]
        try: ano = int(re.sub(r"\D","",textos[0]))
        except: continue
        if ano < 2000 or ano > 2100: continue
        if anos_alvo and ano not in anos_alvo: continue
        if len(textos) >= 10:
            vt,vv,vp = _parse_num(textos[7]),_parse_num(textos[8]),_parse_num(textos[9])
        elif len(textos) >= 4:
            vt,vv,vp = _parse_num(textos[1]),_parse_num(textos[2]),_parse_num(textos[3])
        else: continue
        dados.append({"ano":ano,"valor_total":vt,"variacao_valor":vv,"variacao_pct":vp})
    dados.sort(key=lambda x: x["ano"])
    return dados

# ── COLETA SISMAC ──

def coletar_sismac(busca, aba, anos_alvo):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--no-sandbox","--disable-setuid-sandbox",
            "--disable-dev-shm-usage","--disable-gpu",
        ])
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.on("dialog", lambda d: d.accept())

        page.goto(URL_SISMAC, wait_until="networkidle", timeout=60000)
        time.sleep(2)

        try:
            page.get_by_role("tab", name=aba).click(); time.sleep(0.8)
        except:
            try: page.click(f"text={aba}"); time.sleep(0.8)
            except: pass

        campo = page.locator("input.ui-autocomplete-input").first
        campo.wait_for(timeout=5000)
        campo.click(); campo.fill(""); time.sleep(0.3)
        campo.type(busca[:5], delay=100); time.sleep(2)

        page.wait_for_selector(".ui-autocomplete-item", timeout=5000)
        sugestoes = page.locator(".ui-autocomplete-item").all()
        for s in sugestoes:
            if busca.upper().split()[0] in s.inner_text().strip().upper():
                s.click(); break
        else:
            if sugestoes: sugestoes[0].click()
        time.sleep(1)

        for sel in ["button[id*='pesquis']","span[id*='pesquis']",
                    "a[id*='pesquis']",".fa-search"]:
            btn = page.query_selector(sel)
            if btn: btn.click(); break
        else:
            page.keyboard.press("Enter")

        page.wait_for_load_state("networkidle", timeout=20000)
        time.sleep(3)

        try:
            with page.expect_download(timeout=8000) as dl:
                btn = page.query_selector(
                    "a img[src*='excel'],img[src*='excel'],"
                    "a[href*='excel'],img[src*='xls']"
                )
                if not btn: raise Exception("sem botão")
                btn.click()
            from pathlib import Path
            dados = _parse_excel(Path(dl.value.path()).read_bytes(), anos_alvo)
        except:
            dados = _ler_html(page, anos_alvo)

        browser.close()
    return dados

# ── GERA EXCEL ──

def gerar_excel(nome, dados):
    wb = openpyxl.Workbook()
    ws = wb.active; ws.title = "Evolução Teto MAC"
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 30
    ws.column_dimensions["D"].width = 14

    row = 1
    c = ws.cell(row=row, column=1, value=f"EVOLUÇÃO DO TETO MAC – {nome.upper()}")
    c.font = Font(name="Arial", bold=True, size=11, color=COR_T)
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    ws.row_dimensions[row].height = 22; row += 1

    for col, h in enumerate(["ANO","VALOR TOTAL – TETO MAC (R$)",
                               "VALOR DA VARIAÇÃO – TETO MAC (R$)","VARIAÇÃO (%)"], 1):
        ws.cell(row=row, column=col, value=h)
    _header(ws, row, 4); ws.row_dimensions[row].height = 28; row += 1

    for i, d in enumerate(dados):
        bg = COR_I if i%2==0 else COR_P
        fill = PatternFill("solid", fgColor=bg); fn = Font(name="Arial", size=9)
        c1 = ws.cell(row=row+i, column=1, value=str(d["ano"]))
        c1.font=Font(name="Arial",bold=True,size=9); c1.fill=fill; c1.border=_borda()
        c2 = ws.cell(row=row+i, column=2, value=d["valor_total"])
        c2.font=fn; c2.fill=fill; c2.border=_borda()
        c2.number_format='#,##0.00'; c2.alignment=Alignment(horizontal="right")
        c3 = ws.cell(row=row+i, column=3, value=d["variacao_valor"])
        c3.font=fn; c3.fill=fill; c3.border=_borda()
        c3.number_format='#,##0.00'; c3.alignment=Alignment(horizontal="right")
        pct = d.get("variacao_pct")
        c4 = ws.cell(row=row+i, column=4, value=(pct/100 if pct else None))
        c4.font=fn; c4.fill=fill; c4.border=_borda()
        c4.number_format='0.00%'; c4.alignment=Alignment(horizontal="right")

    row += len(dados)
    if len(dados) >= 2:
        vt = _pct(dados[0]["valor_total"], dados[-1]["valor_total"])
        if vt:
            txt = f"VARIAÇÃO {dados[0]['ano']} – {dados[-1]['ano']}: {vt:.2f}%".replace(".",",")
            c = ws.cell(row=row, column=1, value=txt)
            c.font = Font(name="Arial", bold=True, size=9)
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)

    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    return buf

# ── ROTAS ──

@app.route("/")
def index():
    return open("index.html", encoding="utf-8").read()

@app.route("/localidades")
def localidades():
    """Retorna lista de estados e municípios do banco para o autocomplete."""
    q = request.args.get("q", "").strip().upper()
    uf = request.args.get("uf", "").strip().upper()
    try:
        conn = get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if q:
                cur.execute("""
                    SELECT uf, cod_ibge, nome, desc_gestao
                    FROM localidades
                    WHERE (nome ILIKE %s OR uf ILIKE %s)
                      AND (%s = '' OR uf = %s)
                    ORDER BY uf, nome
                    LIMIT 50
                """, (f"%{q}%", f"%{q}%", uf, uf))
            else:
                cur.execute("""
                    SELECT DISTINCT uf, nome,
                           MIN(cod_ibge) as cod_ibge,
                           MIN(desc_gestao) as desc_gestao
                    FROM localidades
                    WHERE (%s = '' OR uf = %s)
                    GROUP BY uf, nome
                    ORDER BY uf, nome
                    LIMIT 200
                """, (uf, uf))
            rows = cur.fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/ufs")
def ufs():
    """Retorna lista de UFs únicas."""
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT uf FROM localidades ORDER BY uf")
            rows = [r[0] for r in cur.fetchall()]
        conn.close()
        return jsonify(rows)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/buscar", methods=["POST"])
def buscar():
    d = request.json
    busca = d.get("busca", "").strip()
    aba   = d.get("aba", "Estado")
    anos  = d.get("anos", [2022,2023,2024,2025])
    anos_alvo = set(anos) if anos else None
    if not busca:
        return jsonify({"erro": "Informe o nome"}), 400
    try:
        dados = coletar_sismac(busca, aba, anos_alvo)
        if not dados:
            return jsonify({"erro": "Nenhum dado encontrado."}), 404
        return jsonify({"dados": dados, "nome": busca})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route("/exportar", methods=["POST"])
def exportar():
    d = request.json
    nome  = d.get("nome","LOCAL")
    dados = d.get("dados",[])
    if not dados:
        return jsonify({"erro": "Sem dados"}), 400
    buf = gerar_excel(nome, dados)
    fname = f"teto_mac_{nome.lower().replace(' ','_')}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
