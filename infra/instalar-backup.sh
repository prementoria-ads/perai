#!/bin/bash
# Liga a cópia diária do Peraí (D-049). Rodar uma vez, como root:
#   bash /home/perai/perai/infra/instalar-backup.sh
set -euo pipefail
install -m 755 /home/perai/perai/infra/backup.sh /usr/local/bin/perai-backup
# Horários em UTC (o servidor usa UTC): 06:17 UTC = 03:17 em Brasília; teste aos domingos.
cat > /etc/cron.d/perai-backup <<'CRON'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
17 6 * * * root /usr/local/bin/perai-backup >> /var/log/perai-backup.log 2>&1
47 6 * * 0 root /usr/local/bin/perai-backup --testar >> /var/log/perai-backup.log 2>&1
CRON
chmod 644 /etc/cron.d/perai-backup
echo "Primeira cópia e teste de restauração:"
/usr/local/bin/perai-backup
/usr/local/bin/perai-backup --testar
echo
echo "Cópia diária ligada. Cópias em /var/backups/perai (só root), histórico em /var/backups/perai/registro.log"
