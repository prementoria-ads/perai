#!/bin/bash
# Peraí — preparação da VPS. Rodar uma vez, como root, no terminal do hPanel:
#   curl -fsSL <link do arquivo> | bash   (ou colar o conteúdo)
# Registra tudo em /post_install.log. Não contém segredos: chaves de API e senha do
# banco são criadas ou pedidas depois, por `perai-instalar`, e ficam só no servidor.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get -y upgrade
apt-get install -y git python3 python3-venv ufw fail2ban unattended-upgrades ca-certificates

# Atualizações de segurança automáticas
dpkg-reconfigure -f noninteractive unattended-upgrades

# Firewall: só SSH e web
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# Bloqueio de tentativas repetidas de login
systemctl enable --now fail2ban

# Usuário sem privilégios para rodar o Peraí
id perai >/dev/null 2>&1 || useradd -m -s /bin/bash perai
getent group docker >/dev/null && usermod -aG docker perai

# Chave de acesso somente leitura ao repositório privado (deploy key do GitHub)
install -d -m 700 -o perai -g perai /home/perai/.ssh
if [ ! -f /home/perai/.ssh/github_perai ]; then
  sudo -u perai ssh-keygen -q -t ed25519 -N "" -C "perai-vps-deploy" -f /home/perai/.ssh/github_perai
fi
cat > /home/perai/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/github_perai
  IdentitiesOnly yes
EOF
ssh-keyscan -t ed25519 github.com > /home/perai/.ssh/known_hosts 2>/dev/null || true
chown -R perai:perai /home/perai/.ssh
chmod 600 /home/perai/.ssh/config
cp /home/perai/.ssh/github_perai.pub /root/CHAVE-GITHUB-PERAI.txt

# Comando que instala ou atualiza o Peraí
cat > /usr/local/bin/perai-instalar <<'EOF'
#!/bin/bash
# Instala ou atualiza o Peraí. Rode como root: perai-instalar
set -euo pipefail
DIR=/home/perai/perai
if [ ! -d "$DIR/.git" ]; then
  sudo -u perai git clone git@github.com:prementoria-ads/perai.git "$DIR" || {
    echo
    echo "Não consegui baixar o repositório. Cadastre esta chave no GitHub"
    echo "(repositório perai > Settings > Deploy keys > Add deploy key):"
    echo
    cat /home/perai/.ssh/github_perai.pub
    exit 1
  }
else
  sudo -u perai git -C "$DIR" pull --ff-only
fi
cd "$DIR"
if [ ! -f .env ]; then
  sudo -u perai cp .env.example .env
  chmod 600 .env
  SENHA=$(openssl rand -base64 24 | tr -d '/+=')
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$SENHA|" .env
fi
# Variáveis novas do .env.example que ainda não estão no .env
grep -E '^[A-Z_]+=' .env.example | while IFS= read -r LINHA; do
  grep -q "^${LINHA%%=*}=" .env || echo "$LINHA" >> .env
done
for VAR in ANTHROPIC_API_KEY OPENAI_API_KEY GROQ_API_KEY GOOGLE_API_KEY TELEGRAM_BOT_TOKEN PERAI_TELEGRAM_PERMITIDOS; do
  if grep -q "^$VAR=$" .env; then
    read -r -s -p "Cole $VAR (ou Enter para pular): " VALOR; echo
    if [ -n "$VALOR" ]; then
      sed -i "s|^$VAR=.*|$VAR=$VALOR|" .env
    fi
  fi
done
chown perai:perai .env
sudo -u perai docker compose up -d banco
if grep -q "^TELEGRAM_BOT_TOKEN=." .env && grep -q "^PERAI_TELEGRAM_PERMITIDOS=." .env; then
  sudo -u perai docker compose up -d --build bot
else
  echo "Bot do Telegram ainda não configurado: rode perai-instalar de novo quando tiver o token."
fi
echo
echo "Peraí instalado em $DIR:"
sudo -u perai docker compose ps
EOF
chmod 755 /usr/local/bin/perai-instalar

echo "Preparação do Peraí concluída."
