"""Isi data DEMO Payroll untuk karyawan yang SUDAH ADA (tanpa menghapus/menimpa apa pun) lalu buat & hitung run gaji, supaya slip per karyawan langsung muncul.

  python manage.py seed_payroll_demo                 # skala gaji + profil gaji (yang belum punya) + run bulan lalu & bulan ini, dihitung
  python manage.py seed_payroll_demo --no-runs       # hanya skala + profil
  python manage.py seed_payroll_demo --periods 2026-09 2026-10

Aman diulang: skala/profil yang sudah ada tidak diubah. Angka upah hanya CONTOH — ganti dengan skala resmi (Payroll → Skala Gaji Harian) sebelum dipakai sungguhan.
Grade/tingkat diambil dari data karyawan (grade, grade_tier) bila ada; bila kosong dipetakan dari jabatan."""
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.models import Role, User
from apps.hr.attendance_calc import HOLIDAY_WORK_NOTE
from apps.hr.models import AttendanceDaily, Employee, Shift
from apps.payroll import calc, defaults, engine, services
from apps.payroll.models import EducationAllowance, MealPeriod, MealRate, OvertimeRate, PayProfile, PayrollRun, WageScale

SINCE = date(2020, 1, 1)
GRADE_OF = {"Operator": 3, "Staf": 5, "Teknisi": 6, "Leader": 7, "Supervisor": 8, "Manager": 9, "Perawat": 6, "Bidan": 6}


# Ramadan 2026 (±18 Feb–19 Mar) dan 2027 (±8 Feb–9 Mar): perkiraan, sesuaikan dengan penetapan resmi perusahaan.
RAMADAN = [("Ramadan 1447 H", date(2026, 2, 18), date(2026, 3, 19)), ("Ramadan 1448 H", date(2027, 2, 8), date(2027, 3, 9))]


def demo_wage(grade, tier): return Decimal(60000 + (grade - 3) * 4200 + (2500 if tier != "A" else 0)) + Decimal("0.87") * (grade % 2)


class Command(BaseCommand):
    help = "Isi skala gaji + profil gaji contoh untuk karyawan aktif, lalu buat & hitung run gaji."

    def add_arguments(self, p):
        p.add_argument("--no-runs", action="store_true", help="Hanya skala + profil; jangan buat run.")
        p.add_argument("--periods", nargs="*", help="Periode YYYY-MM (bawaan: bulan lalu + bulan ini).")

    def handle(self, *a, **o):
        actor = User.objects.filter(role=Role.SUPERADMIN, is_active=True).order_by("id").first() or User.objects.filter(is_superuser=True).order_by("id").first()
        if actor is None: raise CommandError("Belum ada akun superadmin. Jalankan: python manage.py createsuperuser")
        n_comp = defaults.ensure_components()   # komponen slip dibuat migrasi; hilang setelah `manage.py flush`
        if n_comp: self.stdout.write(self.style.WARNING(f"Komponen slip dibuat ulang: {n_comp} (hilang karena flush/pembersihan data)."))
        today = timezone.localdate()
        emps = list(Employee.objects.filter(status="aktif").select_related("position", "department"))
        if not emps: raise CommandError("Tidak ada karyawan aktif. Tambah karyawan dulu (Data Karyawan / impor CSV).")
        have = engine.current_profiles(today)
        plan = []
        for e in emps:
            if e.pk in have: continue
            g = e.grade if e.grade else GRADE_OF.get(e.position.name if e.position_id else "", 4)
            t = (e.grade_tier or ("A" if e.pk % 3 else "B")).strip().upper()[:2]
            plan.append((e, g, t))
        # skala: 3–9 × A/B + kombinasi grade/tingkat yang dipakai karyawan; hanya yang belum ada
        need = {(g, t) for g in range(3, 10) for t in ("A", "B")} | {(g, t) for _, g, t in plan}
        existing = set(engine.wage_scale(today))
        n_scale = 0
        for g, t in sorted(need - existing):
            WageScale.objects.create(grade=g, tier=t, daily_wage=demo_wage(g, t), effective_from=SINCE, note="Contoh demo — ganti dengan skala resmi", created_by=actor); n_scale += 1
        for e, g, t in plan:
            PayProfile.objects.create(employee=e, effective_from=SINCE, grade=g, tier=t, allowance_position=(g - 5) * 50000 if g > 5 else 0,
                                      allowance_education=60000 if e.education in ("D3", "S1") else 0, meal_allowance=True, pay_type="bulanan" if g >= 7 else "harian", note="Profil demo", created_by=actor)
        self.stdout.write(f"Skala gaji ditambah: {n_scale}. Profil gaji dibuat: {len(plan)} dari {len(emps)} karyawan aktif.")
        self.seed_masters(actor); n_att = self.seed_attendance(o, today, actor)
        self.stdout.write(f"Absensi demo (hadir, lembur, stand by, masuk hari libur): {n_att} baris ditambah.")
        if o["no_runs"]: return
        prev = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        for per in self.periods(o, today):
            if not calc.valid_period(per): raise CommandError(f"Periode tidak valid: {per}")
            run = PayrollRun.objects.filter(period=per).exclude(status=PayrollRun.CANCELLED).first()
            try:
                if run is None: run = services.create_run(per, actor)
                if run.status == PayrollRun.DRAFT: run = services.hand_over_run(run.pk, actor)
                if run.status in (PayrollRun.HANDED, PayrollRun.CALCULATED): run = services.calculate_run(run.pk, actor)
                self.stdout.write(self.style.SUCCESS(f"Run {per}: {run.get_status_display()}, {run.n_slips} slip, total bersih Rp {run.total_net:,.0f}".replace(",", ".")))
                for w in run.warnings: self.stdout.write(self.style.WARNING(f"  ⚠ {w}"))
            except ValueError as ex: self.stdout.write(self.style.ERROR(f"Run {per}: {ex}"))

    def periods(self, o, today):
        prev = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        return o["periods"] or ["2026-03", prev, today.strftime("%Y-%m")]   # 2026-03 = bulan puasa (menunjukkan uang makan)

    def seed_masters(self, actor):
        """Master Lembur & Uang Makan CONTOH (hanya bila masih kosong): periode puasa, tarif makan per tipe gaji/shift, tarif lembur per jam."""
        if not MealPeriod.objects.filter(voided_at__isnull=True).exists():
            for name, a, b in RAMADAN: MealPeriod.objects.create(name=name, start_date=a, end_date=b, note="Contoh demo — sesuaikan", created_by=actor)
        if not MealRate.objects.filter(voided_at__isnull=True).exists():
            for t, amt in (("harian", 10000), ("bulanan", 15000)): MealRate.objects.create(pay_type=t, amount=amt, effective_from=SINCE, note="Contoh demo: tarif umum", created_by=actor)
            for sh in Shift.objects.filter(active=True):   # shift Pagi tidak dapat uang makan (contoh "kadang ada kadang tidak"); shift lain mengikuti tarif umum
                if "PAGI" in (sh.code or sh.name).upper() or sh.is_gs: 
                    for t in ("harian", "bulanan"): MealRate.objects.create(pay_type=t, shift=sh, amount=0, effective_from=SINCE, note="Contoh demo: tidak dapat", created_by=actor)
        if not OvertimeRate.objects.filter(voided_at__isnull=True).exists():
            for t, amt in (("harian", 15000), ("bulanan", 20000)): OvertimeRate.objects.create(kind="lembur", pay_type=t, amount=amt, effective_from=SINCE, note="Contoh demo", created_by=actor)
            for t, amt in (("harian", 8000), ("bulanan", 10000)): OvertimeRate.objects.create(kind="standby", pay_type=t, amount=amt, effective_from=SINCE, note="Contoh demo: kerja saat jam istirahat", created_by=actor)
        if not EducationAllowance.objects.filter(voided_at__isnull=True).exists():
            for edu, amt in (("SMA", 30000), ("D3", 60000), ("S1", 90000)): EducationAllowance.objects.create(education=edu, amount=amt, effective_from=SINCE, note="Contoh demo", created_by=actor)

    def seed_attendance(self, o, today, actor):
        """Rekap absensi CONTOH untuk karyawan berprofil yang belum punya rekap di periode itu: hadir Sen–Sab (Minggu libur), lembur berkala, stand by, 1 hari masuk di hari libur."""
        n = 0
        for per in self.periods(o, today):
            start, end = calc.period_bounds(per); end = min(end, today); profs = engine.current_profiles(end)
            have = set(AttendanceDaily.objects.filter(date__gte=start, date__lte=end).values_list("employee_id", flat=True))
            rows = []
            for e in Employee.objects.filter(status="aktif", join_date__lte=end).select_related("department"):
                if e.pk not in profs or e.pk in have: continue
                d = max(start, e.join_date)
                while d <= end:
                    if d.weekday() != 6:
                        k = (e.pk + d.day) % 9
                        holiday = d.day == 17 and e.pk % 2 == 0
                        rows.append(AttendanceDaily(employee=e, date=d, department=e.department, shift_id=e.shift_id, status="hadir", source="demo", scan_count=2,
                                                    overtime_minutes=(0 if holiday else (60, 90, 120, 45)[k] if k < 4 else 0), standby_minutes=30 if k == 7 else 0,
                                                    notes=HOLIDAY_WORK_NOTE + " (demo)" if holiday else ""))
                    d += timedelta(days=1)
            AttendanceDaily.objects.bulk_create(rows, batch_size=1000, ignore_conflicts=True); n += len(rows)
        return n
