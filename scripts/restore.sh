#!/bin/bash
# Pemakaian: ./restore.sh /var/backups/hris/hris_XXXX.dump   (uji berkala di DB terpisah!)
set -euo pipefail
source /opt/hris/.env
pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" "$1"
