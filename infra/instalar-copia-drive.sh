#!/bin/bash
# Liga a cópia do Peraí no Google Drive (D-049, etapa 2). Rodar uma vez, como root:
#   bash /home/perai/perai/infra/instalar-copia-drive.sh
# As cópias vão criptografadas (rclone crypt): o Google não lê o conteúdo nem os nomes.
# O acesso ao Drive é "drive.file": o servidor só enxerga a pasta que ele mesmo criou.
set -euo pipefail
CONF=/root/.config/rclone/rclone.conf
command -v rclone >/dev/null || { apt-get update -qq && apt-get install -y -qq rclone; }
install -d -m 700 /root/.config/rclone

echo "1) No seu computador, rode o rclone authorize (veja as instruções) e copie o texto"
echo "   que começa com {\"access_token\" e termina com }."
read -r -s -p "2) Cole aqui (não aparece na tela) e aperte Enter: " BRUTO; echo
TOKEN=$(printf '%s' "$BRUTO" | sed 's/\x1b\[[0-9;]*[~A-Za-z]//g' | tr -d '\r\n' | grep -o '{.*}' || true)
unset BRUTO
if ! printf '%s' "$TOKEN" | grep -q '"refresh_token"'; then
  echo "Não reconheci a autorização (falta refresh_token). Nada foi alterado."; exit 1
fi

if grep -q '^\[perai-cofre\]' "$CONF" 2>/dev/null; then
  echo "Já existe um cofre configurado; mantendo as senhas atuais e trocando só a autorização."
  python3 - "$CONF" "$TOKEN" <<'PY'
import re, sys
conf, token = sys.argv[1], sys.argv[2]
s = open(conf).read()
s = re.sub(r"(\[perai-drive\][^\[]*?token = ).*", lambda m: m.group(1) + token, s, flags=re.S)
open(conf, "w").write(s)
PY
else
  SENHA=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)
  SAL=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)
  umask 077
  cat > "$CONF" <<CONFIG
[perai-drive]
type = drive
scope = drive.file
token = $TOKEN

[perai-cofre]
type = crypt
remote = perai-drive:Perai-Backup
password = $(rclone obscure "$SENHA")
password2 = $(rclone obscure "$SAL")
filename_encryption = standard
directory_name_encryption = true
CONFIG
  chmod 600 "$CONF"
  echo
  echo "=================================================================="
  echo " GUARDE AGORA no seu gerenciador de senhas (não mande para ninguém):"
  echo "   Peraí - cofre do backup - senha: $SENHA"
  echo "   Peraí - cofre do backup - sal:   $SAL"
  echo " Sem elas, as cópias no Drive não podem ser abertas se o servidor se perder."
  echo "=================================================================="
  until [ "${OK:-}" = "SALVEI" ]; do read -r -p "Digite SALVEI depois de guardar: " OK; done
  unset SENHA SAL
  clear || true
fi
unset TOKEN

rclone mkdir perai-cofre: && rclone lsd perai-drive: | grep -q Perai-Backup \
  && echo "Pasta Perai-Backup criada no seu Drive." \
  || { echo "Não consegui criar a pasta no Drive. Confira a autorização."; exit 1; }
install -m 755 /home/perai/perai/infra/backup.sh /usr/local/bin/perai-backup
/usr/local/bin/perai-backup
/usr/local/bin/perai-backup --testar
