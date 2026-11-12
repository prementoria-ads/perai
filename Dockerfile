FROM python:3.12-slim

WORKDIR /app
# ffmpeg: converte a resposta falada (/voz) para o formato de mensagem de voz do Telegram.
# poppler-utils (pdftotext): texto bruto das propostas, para o código conferir (D-091).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg poppler-utils && rm -rf /var/lib/apt/lists/*
# Faixas fechadas: uma versão nova com mudança grande não entra sem querer no próximo build.
# zxing-cpp e Pillow: leitura do QR code da NFC-e, sem IA (D-111).
RUN pip install --no-cache-dir "psycopg[binary]>=3.2,<3.4" "mutagen>=1.47,<1.48" "cryptography>=43,<50" \
    "zxing-cpp>=2.2,<4" "Pillow>=10,<13"

COPY perai ./perai
COPY teste_comparativo ./teste_comparativo
RUN useradd -m perai && mkdir -p /dados && chown perai /dados /app
USER perai

ENV PERAI_ARQUIVOS=/dados PYTHONUNBUFFERED=1
CMD ["python", "-m", "perai.bot"]
