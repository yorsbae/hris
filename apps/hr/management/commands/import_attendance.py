"""Impor berkas Fingerspot dari server (berkas besar atau cron dari folder bersama). Idempoten: scan yang sama tidak digandakan.
  python manage.py import_attendance /data/fingerspot/scan_okt.xlsx            # pratinjau saja
  python manage.py import_attendance /data/fingerspot/scan_okt.xlsx --commit   # simpan + olah rekap
  python manage.py import_attendance /data/fingerspot/ --commit                # semua berkas di folder (yang belum pernah diimpor)"""
import hashlib, io, os
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from apps.hr import attendance_calc as calc, attendance_import as imp
from apps.hr.models import AttendanceImport

EXT = (".xlsx", ".xls", ".csv", ".txt", ".dat", ".log")


class _F(io.BytesIO):
    def __init__(self, raw, name): super().__init__(raw); self.name = name


class Command(BaseCommand):
    help = "Impor berkas log absensi Fingerspot (xlsx/xls/csv/txt/dat) dari path atau folder."

    def add_arguments(self, p):
        p.add_argument("path"); p.add_argument("--commit", action="store_true"); p.add_argument("--ignore-invalid", action="store_true")

    def handle(self, *a, **o):
        path = o["path"]
        files = sorted(os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith(EXT)) if os.path.isdir(path) else [path]
        if not files or not all(os.path.exists(f) for f in files): raise CommandError("Berkas/folder tidak ditemukan atau kosong.")
        lo = hi = None
        for f in files:
            raw = open(f, "rb").read(); sha = hashlib.sha256(raw).hexdigest()
            if o["commit"] and AttendanceImport.objects.filter(sha256=sha, state="selesai").exists(): self.stdout.write(f"- {os.path.basename(f)}: sudah pernah diimpor, dilewati."); continue
            try:
                i = imp.stage_upload(_F(raw, os.path.basename(f)), None); imp.analyze(i)
                self.stdout.write(f"- {i.original_name}: {i.taps_valid} scan ({i.taps_new} baru, {i.taps_duplicate} sudah ada → dilewati), {i.rows_invalid} baris tak terbaca, {i.pins_unmapped}/{i.pins_total} PIN belum dikenal, {i.date_min}–{i.date_max}")
                for g in (i.report or {}).get("groups", []): self.stdout.write(f"    ! {g['n']}× {g['label']} — {g['hint']}")
                for r in ((i.report or {}).get("rows") or [])[:10]: self.stdout.write(f"      baris {r['row']}: {r['msg']}")
                if o["commit"]:
                    imp.commit(i, None, ignore_invalid=o["ignore_invalid"])
                    if i.state == "selesai": lo = min(lo, i.date_min) if lo else i.date_min; hi = max(hi, i.date_max) if hi else i.date_max
                    else: self.stdout.write(f"    {i.note}")
                else: imp.cancel(i)
            except ValueError as e: self.stderr.write(f"- {os.path.basename(f)}: GAGAL — {e}")
        if o["commit"] and lo:
            r = calc.process(lo - timedelta(days=1), hi + timedelta(days=1)); self.stdout.write(f"Rekap diolah: {r['rows']} baris ({r['changed']} berubah).")
        elif not o["commit"]: self.stdout.write("Pratinjau saja (tambahkan --commit untuk menyimpan).")
