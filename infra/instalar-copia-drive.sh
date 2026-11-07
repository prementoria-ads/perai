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
echo "   entre \"Paste the following\" e \"End paste\" (ou o que começa com {\"access_token\")."
read -r -s -p "2) Cole aqui (não aparece na tela) e aperte Enter: " BRUTO; echo
LIMPO=$(printf '%s' "$BRUTO" | sed 's/\x1b\[[0-9;]*[~A-Za-z]//g' | tr -d '\r\n ')
unset BRUTO
if printf '%s' "$LIMPO" | grep -q '{'; then
  # Formato antigo do rclone: o JSON {"access_token":...} direto.
  TOKEN=$(printf '%s' "$LIMPO" | grep -o '{.*}' || true)
else
  # Formato novo (rclone 1.70+): um código base64 com {"token": "{...}"} dentro.
  TOKEN=$(printf '%s' "$LIMPO" | python3 -c '
import base64, json, sys
b = sys.stdin.read().strip().replace("-", "+").replace("_", "/")
try:
    print(json.loads(base64.b64decode(b + "=" * (-len(b) % 4)))["token"])
except Exception:
    pass' || true)
fi
unset LIMPO
if ! printf '%s' "$TOKEN" | grep -q '"refresh_token"'; then
  echo "Não reconheci a autorização (falta refresh_token). Nada foi alterado."; exit 1
fi

if grep -q '^\[perai-cofre\]' "$CONF" 2>/dev/null && grep -q '^\[perai-drive\]' "$CONF"; then
  echo "Já existe um cofre configurado; mantendo as senhas atuais e trocando só a autorização."
  python3 - "$CONF" "$TOKEN" <<'PY'
import re, sys
conf, token = sys.argv[1], sys.argv[2]
s = open(conf).read()
# Troca só a linha "token = ..." da seção [perai-drive]; o resto do arquivo (o cofre) fica igual.
linhas, secao = s.splitlines(), None
for i, l in enumerate(linhas):
    if l.startswith("["):
        secao = l.strip()
    elif secao == "[perai-drive]" and l.startswith("token = "):
        linhas[i] = "token = " + token
s = "\n".join(linhas) + "\n"
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
