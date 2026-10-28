FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "psycopg[binary]>=3.2" "mutagen>=1.47" "cryptography>=43"

COPY perai ./perai
COPY teste_comparativo ./teste_comparativo
RUN useradd -m perai && mkdir -p /dados && chown perai /dados /app
USER perai

ENV PERAI_ARQUIVOS=/dados PYTHONUNBUFFERED=1
CMD ["python", "-m", "perai.bot"]
