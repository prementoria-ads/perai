FROM python:3.12-slim

WORKDIR /app
# Faixas fechadas: uma versão nova com mudança grande não entra sem querer no próximo build.
RUN pip install --no-cache-dir "psycopg[binary]>=3.2,<3.4" "mutagen>=1.47,<1.48" "cryptography>=43,<50"

COPY perai ./perai
COPY teste_comparativo ./teste_comparativo
RUN useradd -m perai && mkdir -p /dados && chown perai /dados /app
USER perai

ENV PERAI_ARQUIVOS=/dados PYTHONUNBUFFERED=1
CMD ["python", "-m", "perai.bot"]
