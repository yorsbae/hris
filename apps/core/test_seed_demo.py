"""Putaran 16: perintah seed_demo (data uji coba) berjalan, konsisten, dan tidak bisa berjalan dua kali."""
from io import StringIO
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from apps.core.models import Role, User
from apps.hr import schedule
from apps.hr.models import ChangeRequest, Employee, LeaveLedger, Shift, ShiftAssignment, ShiftGroup, ShiftRotation
from apps.poli.models import Medicine, MedicalRecord


class SeedDemoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.out = StringIO(); call_command("seed_demo", "--employees", "30", stdout=cls.out)

    def test_counts_and_accounts(self):
        self.assertEqual(Employee.objects.filter(nik__startswith="DM").count(), 30)
        for role in (Role.SUPERADMIN, Role.HRD, Role.POLI, Role.DEPT_ADMIN): self.assertTrue(User.objects.filter(role=role).exists())
        self.assertTrue(User.objects.get(username="superadmin").is_staff)
        self.assertTrue(ChangeRequest.objects.filter(status="pending").exists())
        self.assertIn("Demo#HRIS-2026", self.out.getvalue())

    def test_stock_never_negative_and_ledger_consistent(self):
        self.assertFalse(Medicine.objects.filter(stock__lt=0).exists())
        for m in Medicine.objects.all(): self.assertEqual(m.stock, sum(x.qty for x in m.movements.all()))
        self.assertTrue(MedicalRecord.objects.exists())

    def test_leave_balance_not_overdrawn(self):
        from django.db.models import Sum
        for e in Employee.objects.filter(status="aktif"):
            self.assertGreaterEqual(LeaveLedger.objects.filter(employee=e).aggregate(s=Sum("days"))["s"] or 0, 0)

    def test_bpjs_deductions_and_uniforms_seeded_consistently(self):
        from decimal import Decimal
        from apps.hrd.models import BpjsDeduction, BpjsMembership, UniformPurchase
        self.assertTrue(BpjsDeduction.objects.exists()); self.assertTrue(UniformPurchase.objects.exists())
        for d in BpjsDeduction.objects.select_related("employee"):                                    # setiap potongan berasal dari anggota BPJS aktif (karyawan yang keluar di putaran 38c sengaja tetap anggota aktif → muncul sebagai anomali)
            self.assertTrue(BpjsMembership.objects.filter(employee=d.employee, scheme=d.scheme, status="aktif").exists())
        for p in UniformPurchase.objects.select_related("employee"):
            self.assertEqual(p.deduction_amount, p.rate_amount * p.quantity); self.assertEqual(p.gender, p.employee.gender)
            self.assertEqual(p.rate_amount, Decimal(19000) if p.gender == "L" else Decimal(17000))

    def test_partner_bills_seeded_consistently(self):
        from apps.poli.models import PartnerBill
        bs = list(PartnerBill.objects.select_related("employee")); self.assertEqual(len(bs), 6)
        for b in bs: self.assertEqual(b.events.first().to_status, "diterima"); self.assertEqual(b.events.last().to_status, b.status); self.assertGreaterEqual(b.bill_date, b.service_date)
        self.assertTrue({"dibayar", "ditolak", "diterima"} <= {b.status for b in bs})

    def test_second_run_refuses(self):
        with self.assertRaises(CommandError): call_command("seed_demo", "--employees", "20", stdout=StringIO())

    def test_pages_render_for_each_role(self):
        for uname, paths in (("superadmin", ["/", "/admin/", "/audit/"]), ("hrd", ["/", "/employees/", "/requests/", "/hrd/", "/hrd/uniforms/", "/hrd/uniforms/master/", "/hrd/bpjs/deductions/", "/hrd/bpjs/deductions/kes/", "/hrd/separations/"]),
                             ("poli", ["/", "/poli/records/", "/poli/medicines/", "/poli/billing/", "/poli/billing/partners/"]), ("admin_prd", ["/", "/employees/", "/requests/"])):
            self.client.force_login(User.objects.get(username=uname))
            for p in paths: self.assertLess(self.client.get(p).status_code, 500, (uname, p))


    # ---- putaran 17b: master shift baru + tukar 1 orang / 2 orang
    def test_shift_master_codes_groups_and_full_rotation_table(self):
        self.assertEqual(set(Shift.objects.values_list("code", flat=True)), {"PAGI", "SIANG", "MALAM", "GS-12", "GS-14", "GS-16"})
        self.assertEqual(set(Shift.objects.filter(is_gs=True).values_list("code", flat=True)), {"GS-12", "GS-14", "GS-16"})
        self.assertEqual(ShiftGroup.objects.count(), 14)
        self.assertEqual(set(ShiftGroup.objects.filter(pattern="3_SHIFT").values_list("code", flat=True)), {f"{l}_pack" for l in "ABCDEFG"})
        self.assertEqual(ShiftRotation.objects.count(), 14 * 7)  # tiap kelompok punya 7 hari
        for g in ShiftGroup.objects.all():
            rows = list(g.rotation.all()); self.assertEqual(sum(r.shift_id is None for r in rows), 1, g.code)  # tepat 1 hari libur per minggu
            if g.pattern == "2_SHIFT": self.assertFalse(any(r.shift and r.shift.crosses_midnight for r in rows), g.code)  # pola 2 shift tanpa Malam
            r_clean = [r.clean() for r in rows]  # lolos validasi rotasi

    def test_employees_have_either_group_or_fixed_shift(self):
        for e in Employee.objects.filter(nik__startswith="DM"):
            self.assertNotEqual(bool(e.shift_id), bool(e.shift_group_id), e.nik)  # tepat salah satu
            if e.shift_id: self.assertTrue(e.shift.is_gs, e.nik)
            if e.department.code in ("PRD", "GDG", "MTC"): self.assertEqual(e.shift_group.pattern, "2_SHIFT")
            if e.department.code == "PKG": self.assertEqual(e.shift_group.pattern, "3_SHIFT")

    def test_swap_requests_cover_solo_and_duo_and_are_consistent_with_schedule_rules(self):
        swaps = list(ChangeRequest.objects.filter(type__in=["tukar_shift", "tukar_libur"]).select_related("employee"))
        solo, duo = [q for q in swaps if not q.payload.get("partner_id")], [q for q in swaps if q.payload.get("partner_id")]
        self.assertTrue(solo and duo)
        self.assertTrue({q.type for q in duo} == {"tukar_shift", "tukar_libur"} and {q.type for q in solo} == {"tukar_shift", "tukar_libur"})
        self.assertTrue(any(q.status == "executed" for q in duo) and any(q.status == "executed" for q in solo))
        expected = {("tukar_shift", True): 2, ("tukar_libur", True): 4, ("tukar_libur", False): 2, ("tukar_shift", False): 1}
        for q in swaps:
            partner = schedule.partner_of(q); n = ShiftAssignment.objects.filter(request=q).count()
            if partner: self.assertEqual(q.employee.department_id, partner.department_id)  # pasangan satu departemen (Admin Dept boleh mengajukan)
            if q.status == "executed": self.assertEqual(n, expected[(q.type, bool(partner))], q.pk)
            else:
                self.assertEqual(n, 0, q.pk)
                if q.status in ("pending", "approved"): schedule.build_rows(q.type, q.employee, partner, q.payload)  # masih valid menurut aturan jadwal yang sama

    def test_demo_swaps_do_not_collide(self):
        seen = set()
        for a in ShiftAssignment.objects.all(): self.assertNotIn((a.employee_id, a.date), seen); seen.add((a.employee_id, a.date))
        live = {}
        for q in ChangeRequest.objects.filter(type__in=["tukar_shift", "tukar_libur"], status__in=["pending", "approved"]):
            for who in (q.employee_id, q.payload.get("partner_id")):
                for d in schedule.dates_of(q.type, q.payload):
                    if who: self.assertNotIn((who, d), live); live[(who, d)] = q.pk

    def test_hrd_can_execute_a_demo_duo_swap_end_to_end(self):
        from apps.hr import services
        q = ChangeRequest.objects.filter(type="tukar_shift", status="approved").exclude(payload__partner_id=None).first()
        self.assertIsNotNone(q)
        hrd = User.objects.get(username="hrd"); services.transition(q, "executed", hrd)
        self.assertEqual(ShiftAssignment.objects.filter(request=q).count(), 2)

    def test_pages_for_swaps_render(self):
        self.client.force_login(User.objects.get(username="hrd"))
        for q in ChangeRequest.objects.filter(type__in=["tukar_shift", "tukar_libur"])[:6]: self.assertEqual(self.client.get(f"/requests/{q.pk}/").status_code, 200)
        e = Employee.objects.filter(shift_group__isnull=False).first(); self.assertContains(self.client.get(f"/employees/{e.pk}/"), "Jadwal 14 hari ke depan")
        self.assertEqual(self.client.get("/master/shift/").status_code, 200)


class SeedDemoLatestFeaturesTests(TestCase):
    """Putaran 34: data demo mencakup fitur putaran 23–33 + staf Poli/pemeriksa, dan konsisten dengan aturan masing-masing fitur."""
    @classmethod
    def setUpTestData(cls): call_command("seed_demo", "--employees", "60", stdout=StringIO())

    def test_every_department_has_an_active_admin(self):
        from apps.core.user_services import departments_without_admin
        self.assertEqual(list(departments_without_admin()), [])

    def test_poli_department_has_staff_and_examiners_are_only_poli_staff(self):
        from apps.poli.models import MedicalRecord
        staff = Employee.objects.filter(department__code="POL", status="aktif"); self.assertGreaterEqual(staff.count(), 3)
        self.assertTrue(staff.filter(position__name="Bidan", gender="P").exists())
        used = MedicalRecord.objects.exclude(examiner=None)
        self.assertTrue(used.exists() and MedicalRecord.objects.exclude(doctor_name="").exists() and MedicalRecord.objects.filter(examiner=None).exists())
        for rec in used: self.assertEqual(rec.examiner.department.code, "POL")
        self.assertFalse(Employee.objects.filter(name__startswith="dr.").exists())  # dokter bukan karyawan
        for rec in MedicalRecord.objects.filter(kind="kehamilan"): self.assertEqual(rec.examiner.position.name, "Bidan")
        for rec in MedicalRecord.objects.exclude(employee__department__code="POL"): self.assertNotEqual(rec.employee.department.code, "POL")  # staf Poli bukan pasien demo

    def test_all_general_request_types_are_represented(self):
        have = set(ChangeRequest.objects.values_list("type", flat=True))
        self.assertEqual(have, set(ChangeRequest.TYPES))  # 17 jenis, termasuk standby/lembur/promosi/demosi/rotasi/status/izin_khusus
        from apps.hr.models import EmployeeHistory
        self.assertTrue(EmployeeHistory.objects.filter(request__type="promosi").exists())

    def test_extra_work_batches_follow_the_rules(self):
        from apps.hr import overtime
        rows = ChangeRequest.objects.filter(type__in=("standby", "lembur")); self.assertTrue(rows.exists())
        self.assertTrue({"pending", "executed", "rejected", "cancelled"} <= set(rows.values_list("status", flat=True)))
        self.assertTrue(all(q.requested_by.role == Role.DEPT_ADMIN and q.requested_by.department_id == q.department_id for q in rows))
        for q in rows: self.assertLessEqual(int(q.payload["minutes"]), overtime.limit(q.type)); self.assertTrue(len(q.payload["batch"]) == 32)
        cancelled = rows.filter(status="cancelled"); self.assertTrue(cancelled.exists())
        for q in cancelled: self.assertIn("→ cancelled]", q.note); self.assertIn("sakit mendadak", q.note)  # jalur A80 (alasan wajib) benar-benar lewat services.transition

    def test_attendance_checks_cover_every_status_with_trail(self):
        from apps.hr.models import AttendanceCheck as AC
        self.assertTrue({"diminta", "dijawab", "dikembalikan", "diverifikasi", "dibatalkan"} <= set(AC.objects.values_list("status", flat=True)))
        self.assertTrue(any(a.is_late() for a in AC.objects.all()))
        for a in AC.objects.all(): self.assertEqual(a.events.order_by("id").first().action, "minta"); self.assertEqual(a.events.order_by("id").last().to_status, a.status)
        self.assertTrue(AC.objects.exclude(conflict="").exists())
        self.assertTrue(any(e.action == "koreksi" for a in AC.objects.all() for e in a.events.all()))

    def test_uniform_stock_ledger_is_consistent_and_shows_low_and_negative(self):
        from apps.hrd.models import UniformStock, UniformStockMovement, UniformPurchase
        for st in UniformStock.objects.all(): self.assertEqual(st.balance, sum(m.quantity for m in UniformStock.objects.get(pk=st.pk).utype.movements.filter(size=st.size)))
        self.assertTrue(UniformStock.objects.filter(balance__lt=0).exists()); self.assertTrue(any(s.low for s in UniformStock.objects.all()))
        self.assertTrue({"masuk", "keluar", "koreksi", "batal"} <= set(UniformStockMovement.objects.values_list("kind", flat=True)))
        self.assertTrue(UniformPurchase.objects.filter(deduction_status="sudah").exists() and UniformPurchase.objects.exclude(voided_at=None).exists())

    def test_maternity_pregnancy_and_letters_are_consistent(self):
        from apps.hrd.models import MaternityLeave
        from apps.poli.models import MedicalRecord, SickLeaveLetter
        self.assertEqual(set(MaternityLeave.objects.values_list("state", flat=True)), {"aktif", "selesai", "batal"})
        m = MaternityLeave.objects.get(state="aktif"); last = MedicalRecord.objects.filter(employee=m.employee, kind="kehamilan").order_by("-visit_at").first()
        self.assertEqual(last.exam["kehamilan"]["hpl"], m.due_date.isoformat())  # HPL kunjungan = HPL cuti hamil
        self.assertEqual(set(SickLeaveLetter.objects.values_list("kind", flat=True)), {"izin_pulang", "izin_libur", "izin_hamil"})
        for l in SickLeaveLetter.objects.filter(kind="izin_hamil"): self.assertEqual(l.record.kind, "kehamilan")

    def test_warning_revoked_bpjs_two_periods_and_all_pdfs_render(self):
        from apps.hrd.models import BpjsDeduction, WarningLetter
        self.assertTrue(WarningLetter.objects.exclude(revoked_at=None).exists()); self.assertEqual(BpjsDeduction.objects.values("period").distinct().count(), 2)
        from apps.poli.models import SickLeaveLetter, Referral
        from apps.poli.pdf import referral_pdf, sick_leave_pdf
        for l in SickLeaveLetter.objects.all(): self.assertTrue(sick_leave_pdf(l).startswith(b"%PDF"))
        for rf in Referral.objects.exclude(status="batal"): self.assertTrue(referral_pdf(rf).startswith(b"%PDF"))

    def test_new_pages_render_for_each_role(self):
        for uname, paths in (("hrd", ["/validasi/", "/requests/g/lembur/", "/requests/g/lembur/rekap/", "/hrd/uniforms/stock/", "/hrd/maternity/", "/hrd/warnings/", "/master/department/"]),
                             ("admin_prd", ["/validasi/", "/requests/g/lembur/", "/requests/g/lembur/rekap/", "/requests/new/lembur/"]),
                             ("poli", ["/poli/records/new/", "/poli/records/"]), ("superadmin", ["/users/"])):
            self.client.force_login(User.objects.get(username=uname))
            for p in paths: self.assertLess(self.client.get(p).status_code, 500, (uname, p))


class SeedDemoSeparationTests(TestCase):
    """Putaran 38c: data demo Karyawan Keluar dibuat lewat form/servis yang sama dengan UI, konsisten dengan status & riwayat karyawan."""
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", "--employees", "40", stdout=StringIO())

    def test_every_kind_of_state_is_seeded_and_consistent(self):
        from apps.hr.models import EmployeeHistory
        from apps.hrd.models import Separation
        live = list(Separation.objects.filter(voided_at__isnull=True).select_related("employee"))
        self.assertGreaterEqual(len(live), 3)
        past = [s for s in live if s.applied_at]; scheduled = [s for s in live if not s.applied_at]
        self.assertTrue(past); self.assertTrue(scheduled)
        for s in past:
            self.assertEqual(s.employee.status, "nonaktif")
            self.assertTrue(EmployeeHistory.objects.filter(employee=s.employee, field="status", new_value="nonaktif", effective_date=s.last_date).exists())
        for s in scheduled: self.assertEqual(s.employee.status, "aktif"); self.assertGreater(s.last_date, __import__("datetime").date.today())
        self.assertTrue(any(s.paklaring_number for s in past)); self.assertTrue(any(s.tali_asih_paid_on for s in past))
        self.assertEqual(len({s.paklaring_number for s in live if s.paklaring_number}), len([s for s in live if s.paklaring_number]))   # nomor paklaring unik

    def test_voided_example_restores_employee_and_staff_poli_untouched(self):
        from apps.hrd.models import Separation
        for s in Separation.objects.filter(voided_at__isnull=False).select_related("employee"): self.assertEqual(s.employee.status, "aktif"); self.assertTrue(s.void_reason)
        self.assertFalse(Separation.objects.filter(employee__department__code="POL").exists())
        self.assertFalse(Employee.objects.filter(department__code="POL", status="nonaktif").exists())
