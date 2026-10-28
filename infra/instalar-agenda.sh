#!/bin/bash
# Liga o HTTPS (perai.prementoria.com) e a conexão com o Google Agenda (D-051).
# Rodar como root:  bash /home/perai/perai/infra/instalar-agenda.sh
# Pode rodar de novo: só pergunta o que ainda está vazio.
set -euo pipefail
DIR=/home/perai/perai
cd "$DIR"
sudo -u perai git pull --ff-only
# Variáveis novas do .env.example que ainda não estão no .env
grep -E '^[A-Z_]+=' .env.example | while IFS= read -r LINHA; do
  grep -q "^${LINHA%%=*}=" .env || echo "$LINHA" >> .env
done
limpar() { sed 's/\x1b\[[0-9;]*[~A-Za-z]//g' | tr -cd 'A-Za-z0-9._-'; }
definir() { sed -i "s|^$1=.*|$1=$2|" .env; }

if grep -q '^PERAI_CHAVE_CIFRA=$' .env; then
  definir PERAI_CHAVE_CIFRA "$(openssl rand -base64 32 | tr '+/' '-_')"
  echo "Chave de cifra criada (fica só no .env deste servidor)."
fi
if grep -q '^GOOGLE_CLIENT_ID=$' .env; then
  read -r -p "Cole o ID do cliente do Google (termina em .apps.googleusercontent.com), ou Enter para pular: " BRUTO
  ID=$(printf '%s' "$BRUTO" | limpar); unset BRUTO
  if [ -n "$ID" ]; then
    [[ "$ID" == *.apps.googleusercontent.com ]] || { echo "Não parece um ID de cliente do Google. Nada foi alterado."; exit 1; }
    definir GOOGLE_CLIENT_ID "$ID"
  fi
fi
if grep -q '^GOOGLE_CLIENT_SECRET=$' .env && grep -q '^GOOGLE_CLIENT_ID=.' .env; then
  read -r -s -p "Cole a chave secreta do cliente (não aparece na tela): " BRUTO; echo
  SEGREDO=$(printf '%s' "$BRUTO" | limpar); unset BRUTO
  [ ${#SEGREDO} -ge 20 ] || { echo "Chave secreta curta demais. Nada foi alterado."; exit 1; }
  definir GOOGLE_CLIENT_SECRET "$SEGREDO"; unset SEGREDO
fi
chown perai:perai .env; chmod 600 .env

# O modelo "Ubuntu com Docker" da Hostinger traz um Traefik nas portas 80/443: desliga (reversível
# com: docker update --restart=unless-stopped traefik-traefik-1 && docker start traefik-traefik-1).
if docker ps --format '{{.Names}}' | grep -qx traefik-traefik-1; then
  docker update --restart=no traefik-traefik-1 >/dev/null && docker stop traefik-traefik-1 >/dev/null
  echo "Traefik da Hostinger desligado (liberou as portas 80 e 443)."
fi
sudo -u perai docker compose up -d --build bot web
sudo -u perai docker compose up -d --force-recreate caddy
echo "Aguardando o certificado HTTPS..."
for i in $(seq 1 30); do
  if curl -fsS --max-time 5 https://perai.prementoria.com/saude >/dev/null 2>&1; then
    echo "HTTPS OK: https://perai.prementoria.com"; break
  fi
  sleep 4
  [ "$i" = 30 ] && { echo "O HTTPS ainda não respondeu. Mande: docker compose logs --tail 30 caddy"; }
done
sudo -u perai docker compose logs --tail 5 bot | grep -i "agenda\|migra" || true
