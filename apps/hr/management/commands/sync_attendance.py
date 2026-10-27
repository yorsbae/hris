"""Tarik scanlog dari mesin Fingerspot lewat jaringan lokal (cron tiap 15–60 menit; idempoten).
  python manage.py sync_attendance                     # semua mesin 'Tarik otomatis': hanya data TERBARU (sejak scan terakhir tersimpan)
  python manage.py sync_attendance --all               # seluruh log di mesin (sekali di awal / pemulihan)
  python manage.py sync_attendance --device FP01 --from 2026-10-01 --to 2026-10-28
  python manage.py sync_attendance --dry-run           # hanya uji koneksi & hitung, tidak menyimpan"""
from datetime import date
from django.core.management.base import BaseCommand, CommandError
from apps.hr.attendance_sync import probe_many, sync_many
from apps.hr.models import AttendanceDevice


class Command(BaseCommand):
    help = "Unduh scanlog dari mesin Fingerspot (jaringan lokal) ke log absensi."

    def add_arguments(self, p):
        p.add_argument("--device"); p.add_argument("--from", dest="d1"); p.add_argument("--to", dest="d2"); p.add_argument("--all", action="store_true", help="unduh SELURUH log di mesin (bawaan: hanya yang terbaru sejak scan terakhir tersimpan)"); p.add_argument("--dry-run", action="store_true"); p.add_argument("--probe", action="store_true", help="hanya tes koneksi (port terbuka, SN mesin)")

    def handle(self, *a, **o):
        try: d1 = date.fromisoformat(o["d1"]) if o["d1"] else None; d2 = date.fromisoformat(o["d2"]) if o["d2"] else None
        except ValueError: raise CommandError("Tanggal harus berformat YYYY-MM-DD.")
        qs = AttendanceDevice.objects.filter(active=True, sync_enabled=True)
        if o["device"]: qs = AttendanceDevice.objects.filter(code=o["device"])
        if not qs: raise CommandError("Tidak ada mesin dengan 'Tarik otomatis' aktif (atau kode mesin tidak ditemukan).")
        res = probe_many(qs) if o["probe"] else sync_many(qs, d1, d2, commit=not o["dry_run"], mode="all" if o["all"] else "range" if (d1 or d2) else "latest")
        names = {d.code: d.name for d in qs}; bad = 0
        for r in res:
            bad += not r["ok"]; (self.stdout if r["ok"] else self.stderr).write(f"- {names.get(r['device'], r['device'])} [{r['device']}]: {'OK' if r['ok'] else 'GAGAL'} — {r['note']}")
        self.stdout.write(f"{len(res) - bad}/{len(res)} mesin berhasil.")
        if bad: raise CommandError(f"{bad} mesin gagal.")
