FROM python:3.12-slim

# Dependências do sistema para o Chromium
RUN apt-get update && apt-get install -y \
    wget curl gnupg \
    libnss3 libatk1.0-0t64 libatk-bridge2.0-0t64 \
    libcups2t64 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2t64 \
    fonts-liberation fonts-unifont \
    libappindicator3-1 \
    libx11-xcb1 libxcb1 libxcb-dri3-0 \
    libxshmfence1 libglu1-mesa \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY . .

EXPOSE 8000
CMD ["gunicorn", "servidor:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
