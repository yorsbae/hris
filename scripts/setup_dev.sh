#!/usr/bin/env bash
# Setup lokal cepat: .env (bila belum ada) -> migrate -> seed_demo. JANGAN dipakai di produksi.
# Pakai: scripts/setup_dev.sh   (DATABASE_URL bawaan SQLite; ubah di .env untuk PostgreSQL)
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
if [ ! -f .env ]; then
  KEY=$($PY -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
  SECRET=$($PY -c "import secrets; print(secrets.token_urlsafe(50))")
  cat > .env <<ENV
DJANGO_SECRET_KEY=$SECRET
DJANGO_DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=sqlite:///$(pwd)/db.sqlite3
FIELD_ENCRYPTION_KEY=$KEY
ENV
  echo ".env dibuat (SQLite + kunci enkripsi baru)."
fi
$PY manage.py migrate
$PY manage.py seed_demo || echo "seed_demo dilewati (data demo sudah ada atau galat di atas)."
