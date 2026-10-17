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
# Variabel (semua opsional): HRIS_HOME=/opt/hris  BACKUP_DIR=/var/backups/hris  PY=$HRIS_HOME/venv/bin/python  BACKUP_STATUS_FILE=$BACKUP_DIR/status.json
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
  ( cd "$DIR" && { sha256sum "$(basename "$F")"; [ -f "$M" ] && sha256sum "$(basename "$M")"; true; } > "$SUM" && sha256sum -c --status "$SUM" ) || { echo "checksum SHA-256 gagal dibuat/diverifikasi" >&2; return 1; }
  # manifest: jumlah baris tabel kunci saat dump dibuat (dipakai restore.sh untuk memastikan data kembali utuh). Gagal membaca = tidak fatal.
  cnt() { psql "$DATABASE_URL" -tAc "select count(*) from $1" 2>/dev/null | tr -d ' \n' || true; }
  printf '{"created_at": "%s", "dump": "%s", "media": "%s", "counts": {"hr_employee": "%s", "hr_attendanceraw": "%s", "hr_attendanceimport": "%s", "hr_attendancepin": "%s"}}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$F")" "$([ -f "$M" ] && basename "$M")" "$(cnt hr_employee)" "$(cnt hr_attendanceraw)" "$(cnt hr_attendanceimport)" "$(cnt hr_attendancepin)" > "$MAN" || true
  find "$DIR" \( -name 'hris_*.dump' -o -name 'hris_media_*.tar.gz' -o -name 'hris_*.sha256' -o -name 'hris_*.manifest.json' \) -mtime +30 -delete || echo "rotasi 30 hari gagal (diabaikan)" >&2
}

do_backup 2> "$ERR"; RC=$?
if [ "$RC" -eq 0 ]; then
  write_status true "$F" ""
  exit 0
fi
rm -f "$F" "$M" "$SUM" "$MAN"                    # buang sisa berkas setengah jadi
MSG="Backup HRIS gagal di $(hostname) pada $(date '+%F %T'): $(tail -c 300 "$ERR")"
write_status false "" "$MSG"
echo "$MSG" >&2
[ -x "$PY" ] || PY=$(command -v python3 || echo python3)
"$PY" "$HRIS_HOME/manage.py" send_alert "Backup HRIS GAGAL" "$MSG" >&2 || logger -t hris-backup "ALARM backup gagal & alarm tidak terkirim: $MSG" 2>/dev/null || true
exit 1
