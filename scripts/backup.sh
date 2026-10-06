#!/bin/bash
# Cron: 0 1 * * * /opt/hris/scripts/backup.sh   (cadangkan juga ke mesin lain via rsync)
# Mencadangkan: (1) database, (2) berkas di media/ (dokumen karyawan & lampiran pengumuman).
# Catatan: FIELD_ENCRYPTION_KEY (di .env) TIDAK ikut dump ini; simpan salinannya terpisah & aman. Tanpa kunci, kolom sensitif tidak terbaca.
set -euo pipefail
source /opt/hris/.env
DIR=/var/backups/hris; mkdir -p "$DIR"; chmod 700 "$DIR"; umask 077
TS=$(date +%Y%m%d_%H%M%S)
F="$DIR/hris_$TS.dump"
pg_dump --format=custom "$DATABASE_URL" -f "$F"
pg_restore --list "$F" > /dev/null          # verifikasi dump terbaca
M="$DIR/hris_media_$TS.tar.gz"
if [ -d /opt/hris/media ]; then
  tar -czf "$M" -C /opt/hris media
  tar -tzf "$M" > /dev/null                 # verifikasi arsip terbaca
fi
find "$DIR" \( -name 'hris_*.dump' -o -name 'hris_media_*.tar.gz' \) -mtime +30 -delete   # rotasi 30 hari
