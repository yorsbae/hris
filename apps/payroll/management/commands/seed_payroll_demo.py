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
from apps.hr.models import Employee
from apps.payroll import calc, defaults, engine, services
from apps.payroll.models import PayProfile, PayrollRun, WageScale

SINCE = date(2020, 1, 1)
GRADE_OF = {"Operator": 3, "Staf": 5, "Teknisi": 6, "Leader": 7, "Supervisor": 8, "Manager": 9, "Perawat": 6, "Bidan": 6}


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
                                      allowance_education=60000 if e.education in ("D3", "S1") else 0, meal_allowance=e.department.code in ("PRD", "PKG", "GDG"), note="Profil demo", created_by=actor)
        self.stdout.write(f"Skala gaji ditambah: {n_scale}. Profil gaji dibuat: {len(plan)} dari {len(emps)} karyawan aktif.")
        if o["no_runs"]: return
        prev = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        for per in (o["periods"] or [prev, today.strftime("%Y-%m")]):
            if not calc.valid_period(per): raise CommandError(f"Periode tidak valid: {per}")
            run = PayrollRun.objects.filter(period=per).exclude(status=PayrollRun.CANCELLED).first()
            try:
                if run is None: run = services.create_run(per, actor)
                if run.status == PayrollRun.DRAFT: run = services.hand_over_run(run.pk, actor)
                if run.status in (PayrollRun.HANDED, PayrollRun.CALCULATED): run = services.calculate_run(run.pk, actor)
                self.stdout.write(self.style.SUCCESS(f"Run {per}: {run.get_status_display()}, {run.n_slips} slip, total bersih Rp {run.total_net:,.0f}".replace(",", ".")))
                for w in run.warnings: self.stdout.write(self.style.WARNING(f"  ⚠ {w}"))
            except ValueError as ex: self.stdout.write(self.style.ERROR(f"Run {per}: {ex}"))
