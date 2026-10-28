#!/bin/bash
# Peraí — cópia do banco e dos arquivos originais (D-049). Roda como root, todo dia, pelo cron.
# As cópias contêm dados reais do usuário: ficam só no servidor, com acesso apenas do root.
# Uso: perai-backup            (faz a cópia)
#      perai-backup --testar   (restaura a cópia mais recente num banco temporário e confere)
set -euo pipefail
DIR=${PERAI_DIR:-/home/perai/perai}
DEST=${PERAI_BACKUP_DIR:-/var/backups/perai}
DIAS=${PERAI_BACKUP_DIAS:-14}
LOG="$DEST/registro.log"
install -d -m 700 "$DEST"
cd "$DIR"
dc() { docker compose "$@"; }
anotar() { echo "$(date -Is) $*" | tee -a "$LOG"; }

testar() {
  local copia
  copia=$(ls -t "$DEST"/banco-*.sql.gz 2>/dev/null | head -1)
  [ -n "$copia" ] || { anotar "TESTE FALHOU: nenhuma cópia encontrada"; exit 1; }
  dc exec -T banco psql -U perai -d postgres -q -v ON_ERROR_STOP=1 \
    -c "SET client_min_messages = warning" -c "DROP DATABASE IF EXISTS perai_restauracao" \
    -c "CREATE DATABASE perai_restauracao"
  if ! gunzip -c "$copia" | dc exec -T banco psql -U perai -d perai_restauracao -q -v ON_ERROR_STOP=1 >/dev/null; then
    anotar "TESTE FALHOU: erro ao restaurar $(basename "$copia")"
    dc exec -T banco psql -U perai -d postgres -q -c "DROP DATABASE IF EXISTS perai_restauracao"
    exit 1
  fi
  local consulta="SELECT (SELECT count(*) FROM usuarios)||' usuários, '||(SELECT count(*) FROM mensagens)||' mensagens, '||(SELECT count(*) FROM registros)||' registros, '||(SELECT count(*) FROM pendencias)||' pendências'"
  local restaurado atual
  restaurado=$(dc exec -T banco psql -U perai -d perai_restauracao -tAc "$consulta")
  atual=$(dc exec -T banco psql -U perai -d perai -tAc "$consulta")
  dc exec -T banco psql -U perai -d postgres -q -c "DROP DATABASE perai_restauracao"
  anotar "TESTE OK: $(basename "$copia") restaurada com $restaurado (banco atual: $atual)"
}

if [ "${1:-}" = "--testar" ]; then
  testar
  exit 0
fi

DATA=$(date +%Y%m%d-%H%M)
BANCO="$DEST/banco-$DATA.sql.gz"
ARQUIVOS="$DEST/arquivos-$DATA.tar.gz"
# Banco: dump completo (esquema + dados). Grava em .tmp e só renomeia se tudo deu certo.
dc exec -T banco pg_dump -U perai -d perai --no-owner --no-privileges | gzip -9 > "$BANCO.tmp"
mv "$BANCO.tmp" "$BANCO"
# Arquivos originais (áudios, prints, conversas): são a fonte das mensagens (D-040).
dc exec -T bot tar czf - -C /dados . > "$ARQUIVOS.tmp"
mv "$ARQUIVOS.tmp" "$ARQUIVOS"
chmod 600 "$BANCO" "$ARQUIVOS"
# Mantém os últimos $DIAS dias.
find "$DEST" -maxdepth 1 \( -name 'banco-*.sql.gz' -o -name 'arquivos-*.tar.gz' \) -mtime +"$DIAS" -delete
anotar "CÓPIA OK: $(basename "$BANCO") ($(du -h "$BANCO" | cut -f1)), $(basename "$ARQUIVOS") ($(du -h "$ARQUIVOS" | cut -f1))"
