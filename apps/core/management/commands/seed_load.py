"""Data sintetis untuk UJI BEBAN (VISION → Uji beban). JANGAN dipakai di produksi.

    python manage.py seed_load                       # 3.000 karyawan, 90 hari rekap absensi, 30 hari log mentah
    python manage.py seed_load --employees 5000 --days 365 --raw-days 60

Mengisi lewat bulk_create (cepat), dalam satu transaksi. Karyawan bernomor `LD00001…`. Berhenti bila data `LD…` sudah ada (kosongkan DB uji dulu).
Membuat dua akun uji untuk `bench_pages`: `load_hrd` (HRD) dan `load_su` (Superadmin), sandi lewat --password."""
import random
from datetime import date, datetime, time, timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from apps.core.models import AuditLog, Role, User
from apps.hr import models as hr

FIRST = ["Budi", "Siti", "Andi", "Rina", "Dewi", "Agus", "Wulan", "Rizky", "Fitri", "Joko", "Sri", "Dimas", "Putri", "Eko", "Ayu", "Hendra", "Lestari", "Bayu", "Nur", "Yusuf", "Maya", "Rudi", "Intan", "Tono"]
LAST = ["Santoso", "Rahmawati", "Pratama", "Amelia", "Wijaya", "Kusuma", "Hidayat", "Saputra", "Utami", "Nugroho", "Handayani", "Setiawan", "Permana", "Sari", "Firmansyah", "Susanto"]
DEPTS = [("PRD", "Produksi", 0.34), ("GDG", "Gudang", 0.14), ("QC", "Quality Control", 0.12), ("PKG", "Packaging", 0.14), ("MTC", "Maintenance", 0.09),
         ("HRD", "HRD", 0.04), ("FIN", "Finance", 0.05), ("POL", "Poliklinik", 0.03), ("GA", "General Affair", 0.05)]
POS = [("Staf", 1), ("Operator", 1), ("Teknisi", 2), ("Leader", 3), ("Supervisor", 4), ("Manager", 5)]
EDU = ["SMA", "SMK", "D3", "S1", "S2"]
BATCH = 5000


class Command(BaseCommand):
    help = "Isi data sintetis ±3.000 karyawan + riwayat absensi untuk uji beban. Bukan untuk produksi."

    def add_arguments(self, p):
        p.add_argument("--employees", type=int, default=3000)
        p.add_argument("--days", type=int, default=90, help="hari rekap absensi harian ke belakang")
        p.add_argument("--raw-days", type=int, default=30, help="hari log mentah mesin (2 scan/hari) ke belakang")
        p.add_argument("--password", default="Load#HRIS-2026-uji")
        p.add_argument("--seed", type=int, default=7)

    @transaction.atomic
    def handle(self, *a, **o):
        if not settings.FIELD_ENCRYPTION_KEY: raise CommandError("FIELD_ENCRYPTION_KEY belum diisi (data sensitif terenkripsi).")
        if hr.Employee.all_objects.filter(nik__startswith="LD").exists(): raise CommandError("Data uji beban (NIK LD…) sudah ada; kosongkan database uji dulu.")
        rnd, n, today = random.Random(o["seed"]), o["employees"], timezone.localdate()
        say = lambda m: self.stdout.write(m)

        depts = [hr.Department.objects.get_or_create(code=c, defaults={"name": nm})[0] for c, nm, _w in DEPTS]
        weights = [w for _c, _n, w in DEPTS]
        poss = [hr.Position.objects.get_or_create(name=nm, defaults={"level": lv})[0] for nm, lv in POS]
        shifts = [hr.Shift.objects.get_or_create(code=c, defaults={"name": nm, "start": s, "end": e, "crosses_midnight": cm, "is_gs": gs})[0] for c, nm, s, e, cm, gs in
                  [("PAGI", "Shift Pagi", time(6), time(14), False, False), ("SIANG", "Shift Siang", time(14), time(22), False, False), ("GS-16", "General Shift 16", time(8), time(16), False, True)]]
        groups = [hr.ShiftGroup.objects.get_or_create(code=c, defaults={"pattern": hr.ShiftGroup.P2})[0] for c in "ABCDEFG"]

        say(f"Karyawan: {n} …")
        emps = []
        for i in range(1, n + 1):
            d = rnd.choices(depts, weights)[0]
            gs = d.code not in ("PRD", "GDG", "MTC", "PKG")
            emps.append(hr.Employee(nik=f"LD{i:05d}", name=f"{rnd.choice(FIRST)} {rnd.choice(LAST)} {i}", gender=rnd.choice("LP"), department=d, position=rnd.choice(poss),
                                    status="aktif" if rnd.random() < 0.93 else "nonaktif", join_date=today - timedelta(days=rnd.randint(30, 365 * 12)),
                                    birth_date=today - timedelta(days=rnd.randint(365 * 19, 365 * 55)), education=rnd.choice(EDU), phone=f"08{rnd.randint(10**9, 10**10 - 1)}",
                                    nik_ktp=f"33{rnd.randint(10**13, 10**14 - 1)}", bank_name="BCA", bank_account=str(rnd.randint(10**9, 10**10 - 1)),
                                    shift=shifts[2] if gs else None, shift_group=None if gs else rnd.choice(groups)))
        hr.Employee.objects.bulk_create(emps, batch_size=BATCH)
        emps = list(hr.Employee.objects.filter(nik__startswith="LD").order_by("pk"))

        say("Kontrak …")
        hr.Contract.objects.bulk_create([hr.Contract(employee=e, number=f"LDK{e.pk}", kind=rnd.choice(["PKWT", "PKWTT", "percobaan"]), start=e.join_date, status="aktif",
                                                     end=None if rnd.random() < .4 else today + timedelta(days=rnd.randint(-30, 365))) for e in emps if rnd.random() < .85], batch_size=BATCH)

        hrd = User.objects.create_user("load_hrd", password=o["password"], role=Role.HRD)
        User.objects.create_user("load_su", password=o["password"], role=Role.SUPERADMIN)

        say(f"Rekap absensi harian: {o['days']} hari × {n} karyawan …")
        st = ["hadir"] * 17 + ["libur", "izin", "sakit", "alfa", "cuti", "tanpa_scan"]
        rows, total = [], 0
        for e in emps:
            for k in range(o["days"]):
                dt = today - timedelta(days=k + 1)
                s = "libur" if dt.weekday() == 6 else rnd.choice(st)
                late = rnd.randint(0, 25) if s == "hadir" and rnd.random() < .15 else 0
                rows.append(hr.AttendanceDaily(employee=e, date=dt, department=e.department, shift=e.shift, status=s, source="mesin", scan_count=2 if s == "hadir" else 0, late_minutes=late))
            if len(rows) >= BATCH: hr.AttendanceDaily.objects.bulk_create(rows, batch_size=BATCH); total += len(rows); rows = []
        if rows: hr.AttendanceDaily.objects.bulk_create(rows, batch_size=BATCH); total += len(rows)
        say(f"  {total} baris rekap")

        say(f"Log mentah mesin: {o['raw_days']} hari × 2 scan …")
        rows, total, tz = [], 0, timezone.get_current_timezone()
        for e in emps:
            for k in range(o["raw_days"]):
                d0 = today - timedelta(days=k + 1)
                if d0.weekday() == 6: continue
                for hh, mm in ((6, rnd.randint(0, 20)), (14, rnd.randint(0, 20))):
                    rows.append(hr.AttendanceRaw(employee=e, pin=e.nik, device_id="D1", punched_at=timezone.make_aware(datetime.combine(d0, time(hh, mm, rnd.randint(0, 59))), tz)))
            if len(rows) >= BATCH: hr.AttendanceRaw.objects.bulk_create(rows, batch_size=BATCH, ignore_conflicts=True); total += len(rows); rows = []
        if rows: hr.AttendanceRaw.objects.bulk_create(rows, batch_size=BATCH, ignore_conflicts=True); total += len(rows)
        say(f"  {total} scan")

        say("Pengajuan & audit …")
        types, stat = ["izin", "cuti", "sakit", "lembur"], ["draft", "pending", "approved", "rejected", "executed", "executed", "executed"]
        hr.ChangeRequest.objects.bulk_create([hr.ChangeRequest(type=rnd.choice(types), employee=e, department=e.department, status=rnd.choice(stat), requested_by=hrd,
                                                               payload={"start_date": str(today - timedelta(days=rnd.randint(0, 200)))}) for e in emps for _ in range(2)], batch_size=BATCH)
        AuditLog.objects.bulk_create([AuditLog(user=hrd, module="hr", action=rnd.choice(["view_sensitive", "employee_update", "employee_export"]), object_type="Employee",
                                               object_id=str(rnd.choice(emps).pk)) for _ in range(min(50000, n * 10))], batch_size=BATCH)
        say(self.style.SUCCESS(f"Selesai. Akun uji: load_hrd, load_su (sandi: {o['password']}). Lanjut: python manage.py bench_pages"))
