# SISMAC — Teto MAC

## Setup no Railway

### 1. Banco PostgreSQL
- No Railway: Add Service → Database → PostgreSQL
- Copie a variável `DATABASE_URL`

### 2. Deploy do app
- Add Service → GitHub Repo → selecione este repositório
- Na aba Variables, confirme que `DATABASE_URL` está disponível

### 3. Popular o banco (uma única vez)
No terminal do serviço no Railway:
```
python popular_banco.py
```
Isso coleta todos os ~6.500 municípios do SISMAC e salva no banco.

### 4. Pronto!
Acesse a URL gerada pelo Railway.
