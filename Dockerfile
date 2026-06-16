FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 \
    fonts-liberation fonts-unifont \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY . .

# v4 - força rebuild
RUN echo "build ok"

EXPOSE 8000
CMD bash -c '\
    echo "🚀 PORT=$PORT" && \
    python -u -c "from servidor import init_db; init_db()" && \
    python -u -c "
import os, psycopg2
conn = psycopg2.connect(os.environ[\"DATABASE_URL\"])
cur = conn.cursor()
cur.execute(\"SELECT COUNT(*) FROM historico WHERE ano > 0\")
total = cur.fetchone()[0]
print(f\"Historico: {total} registros\")
if total == 0 and os.path.exists(\"importar_railway.sql\"):
    print(\"Importando SQL...\")
    cur.execute(open(\"importar_railway.sql\", encoding=\"utf-8\").read())
    conn.commit()
    cur.execute(\"SELECT COUNT(*) FROM historico WHERE ano > 0\")
    print(f\"Importado: {cur.fetchone()[0]} registros\")
conn.close()
" && \
    echo "▶️ Gunicorn porta $PORT" && \
    exec gunicorn servidor:app --bind "0.0.0.0:$PORT" --workers 2 --timeout 120'
