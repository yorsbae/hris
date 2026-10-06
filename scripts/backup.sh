#!/bin/bash
# Cron: 0 1 * * * /opt/hris/scripts/backup.sh   (cadangkan juga ke mesin lain via rsync)
set -euo pipefail
source /opt/hris/.env
DIR=/var/backups/hris; mkdir -p "$DIR"; chmod 700 "$DIR"
F="$DIR/hris_$(date +%Y%m%d_%H%M%S).dump"
pg_dump --format=custom "$DATABASE_URL" -f "$F"
pg_restore --list "$F" > /dev/null          # verifikasi dump terbaca
find "$DIR" -name 'hris_*.dump' -mtime +30 -delete   # rotasi 30 hari
