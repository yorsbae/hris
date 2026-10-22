#!/bin/bash
# Latihan restore OTOMATIS: memulihkan backup TERBARU ke database SEMENTARA (tidak menyentuh database produksi), memverifikasinya, lalu membuangnya.
# Backup yang tidak pernah diuji pulih bukan jaminan. Jalankan bulanan (cron) atau sesudah mengubah skrip backup:
#   0 3 1 * * HRIS_HOME=/opt/hris PY=/opt/hris/venv/bin/python /opt/hris/scripts/restore_drill.sh
# Pemakaian manual: ./restore_drill.sh [/var/backups/hris/hris_XXXX.dump]   (tanpa argumen = dump terbaru di $BACKUP_DIR)
#
# Yang diperiksa: SHA-256 → pg_restore (satu transaksi) ke DB sementara → `manage.py verify_restore` (jumlah baris semua tabel vs manifest, kunci enkripsi + kolom
# terenkripsi terbuka, integritas Payroll) → arsip media/private_media terbaca & aman. Hasil ditulis ke $BACKUP_DIR/restore_drill.json; /health/ memantau umurnya
# (batas DRILL_MAX_AGE_DAYS, bawaan 35 hari) dan gagal = alarm (`manage.py send_alert`) + kode keluar 1.
# Butuh: pengguna DB boleh CREATE DATABASE (di server yang sama dengan DATABASE_URL). Variabel: HRIS_HOME, BACKUP_DIR, PY, DRILL_STATUS_FILE, DRILL_DB (nama DB sementara)
set -uo pipefail
HRIS_HOME=${HRIS_HOME:-/opt/hris}; BACKUP_DIR=${BACKUP_DIR:-/var/backups/hris}; PY=${PY:-$HRIS_HOME/venv/bin/python}; [ -x "$PY" ] || PY=$(command -v python3)
umask 077
set -a; source "$HRIS_HOME/.env"; set +a
STATUS=${DRILL_STATUS_FILE:-$BACKUP_DIR/restore_drill.json}
DUMP=${1:-$(ls -1t "$BACKUP_DIR"/hris_[0-9]*.dump 2>/dev/null | head -1)}
DB=${DRILL_DB:-hris_drill_$(date +%Y%m%d%H%M%S)}
TMP=$(mktemp -d); ERR="$TMP/err"; : > "$ERR"
SRC_DB=$(printf '%s' "$DATABASE_URL" | sed -E 's#^.*/([^/?]+)(\?.*)?$#\1#')
ADMIN_URL=$(printf '%s' "$DATABASE_URL" | sed -E "s#/[^/?]+(\\?.*)?\$#/postgres#"); DRILL_URL=$(printf '%s' "$DATABASE_URL" | sed -E "s#/[^/?]+(\\?.*)?\$#/$DB#")
STARTED=$(date +%s)

write_status() {   # $1 = true|false, $2 = pesan
  local msg; msg=$(printf '%s' "$2" | tr '\n\r\t"\\' '     ' | cut -c1-400)
  printf '{"ok": %s, "finished_at": "%s", "seconds": %s, "dump": "%s", "message": "%s"}\n' "$1" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(( $(date +%s) - STARTED ))" "$(basename "${DUMP:-}")" "$msg" > "$STATUS.tmp" && mv "$STATUS.tmp" "$STATUS"
}
cleanup() { psql "$ADMIN_URL" -qc "drop database if exists \"$DB\"" >/dev/null 2>&1; rm -rf "$TMP"; }
trap cleanup EXIT

drill() {
  [ -n "$DUMP" ] && [ -f "$DUMP" ] || { echo "Tidak ada dump di $BACKUP_DIR (jalankan backup.sh dulu)" >&2; return 1; }
  [ "$DB" != "$SRC_DB" ] || { echo "DRILL_DB sama dengan database produksi ($SRC_DB) — dibatalkan" >&2; return 1; }
  local side="${DUMP%.dump}.sha256" man="${DUMP%.dump}.manifest.json"
  [ -f "$side" ] && { ( cd "$(dirname "$DUMP")" && sha256sum -c --ignore-missing --status "$(basename "$side")" ) || { echo "checksum TIDAK COCOK: backup rusak" >&2; return 1; }; } || echo "peringatan: $side tidak ada" >&2
  psql "$ADMIN_URL" -qc "create database \"$DB\"" || { echo "tidak bisa membuat database sementara $DB (hak CREATE DATABASE?)" >&2; return 1; }
  pg_restore --no-owner --single-transaction --exit-on-error -d "$DRILL_URL" "$DUMP" 2>> "$ERR" || { echo "pg_restore ke DB sementara gagal: $(tail -c 200 "$ERR")" >&2; return 1; }
  local vr=(); [ -f "$man" ] && vr=(--manifest "$man") || echo "peringatan: manifest tidak ada, jumlah baris tidak diverifikasi" >&2
  DATABASE_URL="$DRILL_URL" "$PY" "$HRIS_HOME/manage.py" verify_restore "${vr[@]}" > "$TMP/verify" 2>&1 || { echo "verify_restore GAGAL: $(grep -E 'GAGAL|CommandError' "$TMP/verify" | head -3 | tr '\n' ' ')" >&2; return 1; }
  # arsip pendamping: terbaca, hanya path aman, dan bisa diekstrak
  local f
  for kind in media private; do
    f=$(sed -n "s/.*\"$kind\": \"\\([^\"]*\\)\".*/\\1/p" "$man" 2>/dev/null | head -1); [ -n "$f" ] || continue
    [ -f "$(dirname "$DUMP")/$f" ] || { echo "arsip $kind ($f) tercatat di manifest tetapi hilang" >&2; return 1; }
    tar -tzf "$(dirname "$DUMP")/$f" > "$TMP/list" 2>/dev/null || { echo "arsip $f tidak terbaca" >&2; return 1; }
    if awk '/^\// || /(^|\/)\.\.(\/|$)/ {bad=1} END{exit !bad}' "$TMP/list"; then echo "arsip $f memuat path tidak aman" >&2; return 1; fi
    mkdir "$TMP/x_$kind" && tar -xzf "$(dirname "$DUMP")/$f" -C "$TMP/x_$kind" --no-same-owner || { echo "arsip $f gagal diekstrak" >&2; return 1; }
  done
  cat "$TMP/verify"
}

OUT=$(drill 2>&1); RC=$?
if [ "$RC" -eq 0 ]; then
  write_status true "Restore percobaan berhasil: $(printf '%s' "$OUT" | grep -c '^OK' ) pemeriksaan lulus"; echo "$OUT"; echo "LATIHAN RESTORE BERHASIL (${DUMP##*/})."; exit 0
fi
MSG="Latihan restore HRIS GAGAL di $(hostname) pada $(date '+%F %T'): $(printf '%s' "$OUT" | tail -c 350)"
write_status false "$MSG"; echo "$MSG" >&2
"$PY" "$HRIS_HOME/manage.py" send_alert "Latihan restore HRIS GAGAL" "$MSG" >&2 || logger -t hris-drill "ALARM: $MSG" 2>/dev/null || true
exit 1
