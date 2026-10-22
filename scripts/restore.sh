#!/bin/bash
# Pemakaian: ./restore.sh /var/backups/hris/hris_XXXX.dump [hris_media_XXXX.tar.gz] [hris_private_XXXX.tar.gz]
# (uji berkala di DB & folder terpisah — otomatis lewat scripts/restore_drill.sh). Argumen ke-2 opsional: memulihkan media/ (dokumen karyawan, lampiran);
# ke-3 opsional: private_media/ (CV pelamar terenkripsi; lokasi tujuan $PRIVATE_MEDIA_ROOT, bawaan $HRIS_HOME/private_media).
#
# Pengaman (putaran 62):
#  1. SHA-256 diverifikasi dari hris_XXXX.sha256 (dibuat backup.sh); berkas rusak/berubah → berhenti sebelum menyentuh database.
#  2. Menampilkan DATABASE TUJUAN (sandi disamarkan) dan minta mengetik RESTORE. Lewati dengan RESTORE_YES=1 (otomasi).
#  3. Membuat cadangan pengaman DB saat ini ($BACKUP_DIR/pre_restore_*.dump) lebih dulu. Lewati dengan SKIP_SAFETY_DUMP=1 (tidak disarankan).
#  4. pg_restore dalam SATU transaksi + berhenti pada galat pertama: gagal di tengah = database tetap seperti semula (tidak setengah-pulih).
#  5. Arsip media diperiksa (hanya media/…, tanpa ../ atau path absolut) dan media/attendance_stage (draft sementara) tidak ikut dipulihkan.
#  6. Setelah pulih: migrate (dump lama → skema terbaru; lewati SKIP_MIGRATE=1), draft impor absensi yang berkas sementaranya tidak ada ditutup,
#     lalu `manage.py verify_restore` membandingkan jumlah baris SEMUA tabel dengan hris_XXXX.manifest.json, memeriksa kunci enkripsi (sidik jari + kolom
#     terenkripsi benar-benar terbuka) dan integritas Payroll. Ada yang GAGAL = keluar dengan kode 6. Restore idempoten: dijalankan ulang menghasilkan keadaan yang sama.
# Variabel: HRIS_HOME=/opt/hris  BACKUP_DIR=/var/backups/hris  PY=$HRIS_HOME/venv/bin/python
set -euo pipefail
DUMP=${1:-}; MEDIA=${2:-}; PRIVARC=${3:-}
[ -n "$DUMP" ] && [ -f "$DUMP" ] || { echo "Pemakaian: $0 hris_XXXX.dump [hris_media_XXXX.tar.gz]" >&2; exit 2; }
[ -z "$MEDIA" ] || [ -f "$MEDIA" ] || { echo "Arsip media tidak ditemukan: $MEDIA" >&2; exit 2; }
[ -z "$PRIVARC" ] || [ -f "$PRIVARC" ] || { echo "Arsip private_media tidak ditemukan: $PRIVARC" >&2; exit 2; }
HRIS_HOME=${HRIS_HOME:-/opt/hris}; BACKUP_DIR=${BACKUP_DIR:-/var/backups/hris}; PY=${PY:-$HRIS_HOME/venv/bin/python}
[ -x "$PY" ] || PY=$(command -v python3)
umask 077
set -a; source "$HRIS_HOME/.env"; set +a

# 1. checksum
SIDE="${DUMP%.dump}.sha256"
if [ -f "$SIDE" ]; then
  ( cd "$(dirname "$DUMP")" && sha256sum -c --ignore-missing "$(basename "$SIDE")" ) || { echo "CHECKSUM TIDAK COCOK: berkas backup rusak/berubah. Restore dibatalkan; database tidak disentuh." >&2; exit 3; }
else echo "PERINGATAN: $SIDE tidak ada (backup lama) — checksum tidak dapat diverifikasi." >&2; fi
pg_restore --list "$DUMP" > /dev/null || { echo "Dump tidak terbaca (pg_restore --list gagal). Restore dibatalkan." >&2; exit 3; }
if [ -n "$MEDIA" ]; then
  tar -tzf "$MEDIA" > /dev/null || { echo "Arsip media tidak terbaca. Restore dibatalkan." >&2; exit 3; }
  if tar -tzf "$MEDIA" | awk '!/^media(\/|$)/ || /(^|\/)\.\.(\/|$)/ {bad=1} END{exit !bad}'; then echo "Arsip media memuat path tidak aman/di luar media/. Restore dibatalkan." >&2; exit 3; fi
fi

if [ -n "$PRIVARC" ]; then
  tar -tzf "$PRIVARC" > /dev/null || { echo "Arsip private_media tidak terbaca. Restore dibatalkan." >&2; exit 3; }
  if tar -tzf "$PRIVARC" | awk '!/^private_media(\/|$)/ || /(^|\/)\.\.(\/|$)/ {bad=1} END{exit !bad}'; then echo "Arsip private_media memuat path tidak aman. Restore dibatalkan." >&2; exit 3; fi
fi

# 2. konfirmasi
SAFE_URL=$(printf '%s' "$DATABASE_URL" | sed -E 's#(://[^:/@]*):[^@]*@#\1:***@#')
echo "Restore ke: $SAFE_URL"; echo "Dump      : $DUMP"; [ -z "$MEDIA" ] || echo "Media     : $MEDIA"
echo "SEMUA data di database tujuan akan DIGANTI isi dump."
if [ "${RESTORE_YES:-}" != "1" ]; then read -r -p "Ketik RESTORE untuk melanjutkan: " ANS; [ "$ANS" = "RESTORE" ] || { echo "Dibatalkan."; exit 1; }; fi

# 3. cadangan pengaman
if [ "${SKIP_SAFETY_DUMP:-}" != "1" ]; then
  mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"; PRE="$BACKUP_DIR/pre_restore_$(date +%Y%m%d_%H%M%S).dump"
  pg_dump --format=custom "$DATABASE_URL" -f "$PRE" || { echo "Cadangan pengaman gagal; restore dibatalkan (atau SKIP_SAFETY_DUMP=1)." >&2; rm -f "$PRE"; exit 4; }
  echo "Cadangan pengaman: $PRE"
fi

# 4. restore atomik
pg_restore --clean --if-exists --no-owner --single-transaction --exit-on-error -d "$DATABASE_URL" "$DUMP" || { echo "pg_restore GAGAL — database tidak berubah (transaksi dibatalkan)." >&2; exit 5; }

# 5. media
if [ -n "$MEDIA" ]; then tar -xzf "$MEDIA" -C "$HRIS_HOME" --no-same-owner --exclude='media/attendance_stage'; echo "Media dipulihkan."; fi

if [ -n "$PRIVARC" ]; then PD=${PRIVATE_MEDIA_ROOT:-$HRIS_HOME/private_media}; mkdir -p "$(dirname "$PD")"; tar -xzf "$PRIVARC" -C "$(dirname "$PD")" --no-same-owner; echo "private_media dipulihkan ke $PD."; fi

# 6. pasca-restore
[ "${SKIP_MIGRATE:-}" = "1" ] || "$PY" "$HRIS_HOME/manage.py" migrate --noinput
"$PY" "$HRIS_HOME/manage.py" shell -c "from apps.hr import attendance_import as i; print('Draft impor absensi ditutup:', i.purge_stale(0))" || true
MAN="${DUMP%.dump}.manifest.json"; VR=(); [ -f "$MAN" ] && VR=(--manifest "$MAN") || echo "PERINGATAN: $MAN tidak ada — jumlah baris tidak diverifikasi." >&2
"$PY" "$HRIS_HOME/manage.py" verify_restore "${VR[@]}" || { echo "VERIFIKASI PASCA-RESTORE GAGAL (lihat di atas). Database sudah terpulihkan; periksa kunci enkripsi/dump sebelum dipakai." >&2; exit 6; }
echo "Restore selesai. Jalankan: $PY $HRIS_HOME/manage.py check_health"
