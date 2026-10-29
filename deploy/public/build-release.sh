#!/usr/bin/env bash
# Bangun rilis HOST PUBLIK dari repo yang sama: hanya berkas yang dibutuhkan (tanpa Payroll/Poli/HRD/scripts/docs).
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT="${1:-wadimor-release.tar.gz}"
tar --exclude='__pycache__' --exclude='*.pyc' --exclude='*/test*.py' -czf "$OUT" \
  manage.py requirements-public.txt config/__init__.py config/settings.py config/settings_public.py config/urls_public.py config/wsgi_public.py \
  apps/__init__.py apps/core apps/hr apps/company templates
echo "Rilis: $OUT (sha256: $(sha256sum "$OUT" | cut -d' ' -f1))"
