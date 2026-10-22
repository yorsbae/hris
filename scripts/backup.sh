#!/bin/bash
# Cron: 0 1 * * * /opt/hris/scripts/backup.sh   (cadangkan juga ke mesin lain via rsync)
# Mencadangkan: (1) database (termasuk log scan mesin, riwayat impor + laporan gagal-impor), (2) berkas di media/ (dokumen karyawan & lampiran pengumuman),
# (3) berkas SHA-256 + manifest (jumlah baris tabel kunci) untuk memverifikasi salinan sebelum & sesudah restore.
# media/attendance_stage/ (berkas impor absensi sementara, dihapus otomatis setelah impor) TIDAK dicadangkan: data sementara dan sudah ada di DB setelah impor selesai.
# Catatan: FIELD_ENCRYPTION_KEY (di .env) TIDAK ikut dump ini; simpan salinannya terpisah & aman. Tanpa kunci, kolom sensitif tidak terbaca.
#
# Pemantauan (putaran 61): setiap run menulis $BACKUP_DIR/status.json ({"ok", "finished_at", "file", "error"}); /health/ dan `manage.py check_health` membacanya,
# sehingga backup yang GAGAL maupun yang TIDAK BERJALAN SAMA SEKALI (cron mati, mesin mati) terdeteksi. Saat gagal skrip juga langsung mengirim alarm
# (`manage.py send_alert` → ALERT_WEBHOOK_URL / ALERT_EMAILS di .env) dan keluar dengan kode 1 (cron mengirim email lokal bila MAILTO diset).
# Putaran 63: (a) manifest memuat jumlah baris SEMUA tabel (termasuk Payroll) + sidik jari FIELD_ENCRYPTION_KEY (kunci salah/hilang terdeteksi saat restore);
# (b) private_media/ (CV pelamar terenkripsi) ikut dicadangkan; (c) salinan di luar mesin: BACKUP_RSYNC_TARGET (gagal salin = backup dianggap GAGAL + alarm).
# Variabel (semua opsional): HRIS_HOME=/opt/hris  BACKUP_DIR=/var/backups/hris  PY=$HRIS_HOME/venv/bin/python  BACKUP_STATUS_FILE=$BACKUP_DIR/status.json
#   PRIVATE_MEDIA_ROOT=$HRIS_HOME/private_media  BACKUP_RSYNC_TARGET=user@mesin-lain:/data/hris-backup/ (butuh rsync + SSH tanpa sandi)  BACKUP_KEEP_DAYS=30
set -uo pipefail
HRIS_HOME=${HRIS_HOME:-/opt/hris}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/hris}
PY=${PY:-$HRIS_HOME/venv/bin/python}
umask 077
set -a; source "$HRIS_HOME/.env"; set +a        # DATABASE_URL, ALERT_*, dst. (diekspor agar terbaca pg_dump/Python)
DIR=$BACKUP_DIR; STATUS=${BACKUP_STATUS_FILE:-$DIR/status.json}
mkdir -p "$DIR" && chmod 700 "$DIR" || { echo "Tidak bisa membuat $DIR" >&2; exit 1; }
TS=$(date +%Y%m%d_%H%M%S)
F="$DIR/hris_$TS.dump"
M="$DIR/hris_media_$TS.tar.gz"
P="$DIR/hris_private_$TS.tar.gz"; PRIV=${PRIVATE_MEDIA_ROOT:-$HRIS_HOME/private_media}
SUM="$DIR/hris_$TS.sha256"; MAN="$DIR/hris_$TS.manifest.json"
ERR=$(mktemp); trap 'rm -f "$ERR"' EXIT

write_status() {   # $1 = true|false, $2 = berkas, $3 = pesan galat (dibersihkan agar JSON valid)
  local msg; msg=$(printf '%s' "$3" | tr '\n\r\t"\\' '     ' | cut -c1-300)
  printf '{"ok": %s, "finished_at": "%s", "file": "%s", "error": "%s"}\n' "$1" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "${2:-}")" "$msg" > "$STATUS.tmp" && mv "$STATUS.tmp" "$STATUS"
}

do_backup() {
  pg_dump --format=custom "$DATABASE_URL" -f "$F" || { echo "pg_dump gagal" >&2; return 1; }
  pg_restore --list "$F" > /dev/null || { echo "dump tidak terbaca (pg_restore --list gagal)" >&2; return 1; }   # verifikasi dump terbaca
  if [ -d "$HRIS_HOME/media" ]; then
    tar -czf "$M" -C "$HRIS_HOME" --exclude='media/attendance_stage' media || { echo "tar media gagal" >&2; return 1; }
    tar -tzf "$M" > /dev/null || { echo "arsip media tidak terbaca" >&2; return 1; }                              # verifikasi arsip terbaca
  fi
  if [ -d "$PRIV" ]; then
    tar -czf "$P" -C "$(dirname "$PRIV")" "$(basename "$PRIV")" || { echo "tar private_media gagal" >&2; return 1; }
    tar -tzf "$P" > /dev/null || { echo "arsip private_media tidak terbaca" >&2; return 1; }
  fi
  ( cd "$DIR" && { sha256sum "$(basename "$F")"; [ -f "$M" ] && sha256sum "$(basename "$M")"; [ -f "$P" ] && sha256sum "$(basename "$P")"; true; } > "$SUM" && sha256sum -c --status "$SUM" ) || { echo "checksum SHA-256 gagal dibuat/diverifikasi" >&2; return 1; }
  # manifest: jumlah baris SEMUA tabel saat dump dibuat + sidik jari kunci enkripsi (dipakai `manage.py verify_restore`). Gagal membaca = tidak fatal.
  COUNTS=$(psql "$DATABASE_URL" -tAF'|' -c "select table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by 1" 2>/dev/null \
    | awk -F'|' 'NF==2 && $1 ~ /^[a-z0-9_]+$/ && $2 ~ /^[0-9]+$/ {printf "%s\"%s\": \"%s\"", (n++ ? ", " : ""), $1, $2}') || COUNTS=""
  KFP=$(printf '%s' "${FIELD_ENCRYPTION_KEY:-}" | sha256sum | cut -c1-12); [ -n "${FIELD_ENCRYPTION_KEY:-}" ] || KFP=""
  printf '{"created_at": "%s", "dump": "%s", "media": "%s", "private": "%s", "key_fingerprint": "%s", "counts": {%s}}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$F")" "$([ -f "$M" ] && basename "$M")" "$([ -f "$P" ] && basename "$P")" "$KFP" "$COUNTS" > "$MAN" || true
  find "$DIR" \( -name 'hris_*.dump' -o -name 'hris_media_*.tar.gz' -o -name 'hris_private_*.tar.gz' -o -name 'hris_*.sha256' -o -name 'hris_*.manifest.json' \) -mtime +"${BACKUP_KEEP_DAYS:-30}" -delete || echo "rotasi gagal (diabaikan)" >&2
  if [ -n "${BACKUP_RSYNC_TARGET:-}" ]; then   # salinan di luar mesin ini: backup yang hanya ada di satu disk bukan backup
    command -v rsync > /dev/null || { echo "BACKUP_RSYNC_TARGET diisi tetapi rsync tidak terpasang" >&2; return 1; }
    rsync -a --partial "$F" "$SUM" "$MAN" $([ -f "$M" ] && echo "$M") $([ -f "$P" ] && echo "$P") "$BACKUP_RSYNC_TARGET" || { echo "salin ke $BACKUP_RSYNC_TARGET gagal" >&2; return 1; }
  fi
}

do_backup 2> "$ERR"; RC=$?
if [ "$RC" -eq 0 ]; then
  write_status true "$F" ""
  exit 0
fi
rm -f "$F" "$M" "$P" "$SUM" "$MAN"                    # buang sisa berkas setengah jadi
MSG="Backup HRIS gagal di $(hostname) pada $(date '+%F %T'): $(tail -c 300 "$ERR")"
write_status false "" "$MSG"
echo "$MSG" >&2
[ -x "$PY" ] || PY=$(command -v python3 || echo python3)
"$PY" "$HRIS_HOME/manage.py" send_alert "Backup HRIS GAGAL" "$MSG" >&2 || logger -t hris-backup "ALARM backup gagal & alarm tidak terkirim: $MSG" 2>/dev/null || true
exit 1
