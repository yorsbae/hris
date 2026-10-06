#!/bin/bash
# Pemakaian: ./restore.sh /var/backups/hris/hris_XXXX.dump [/var/backups/hris/hris_media_XXXX.tar.gz]
# (uji berkala di DB & folder terpisah!). Argumen ke-2 opsional: memulihkan media/ (dokumen karyawan, lampiran).
set -euo pipefail
source /opt/hris/.env
pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" "$1"
if [ -n "${2:-}" ]; then tar -xzf "$2" -C /opt/hris; fi
