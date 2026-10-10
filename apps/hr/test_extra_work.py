"""Putaran 30: pengajuan Stand By & Lembur (Admin Produksi → HRD), banyak karyawan per kiriman, keputusan HRD per kiriman."""
from datetime import date, timedelta
from django.test import TestCase
from django.utils import timezone
from apps.core.models import AuditLog, Notification, Role, User
from .models import ChangeRequest, Department, Employee, Position
from .overtime import minutes_between
from datetime import time


class ExtraWorkBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.prd, cls.oth = Department.objects.create(code="P", name="Produksi"), Department.objects.create(code="Q", name="Gudang")
        pos = Position.objects.create(name="Operator")
        mk = lambda n, d: Employee.objects.create(nik=n, name=f"Emp {n}", gender="L", department=d, position=pos, join_date=date(2024, 1, 1))
        cls.a, cls.b, cls.c = mk("001", cls.prd), mk("002", cls.prd), mk("003", cls.oth)
        cls.hrd = User.objects.create_user("hrd", password="x", role=Role.HRD)
        cls.adm = User.objects.create_user("admprd", password="x", role=Role.DEPT_ADMIN, department=cls.prd)
        cls.adm2 = User.objects.create_user("admgdg", password="x", role=Role.DEPT_ADMIN, department=cls.oth)
        cls.poli = User.objects.create_user("poli", password="x", role=Role.POLI)
        cls.today = timezone.localdate()

    def post(self, user=None, **kw):
        self.client.force_login(user or self.adm)
        d = {"kind": "lembur", "date": self.today.isoformat(), "start": "16:00", "end": "18:00", "reason": "Kejar target", "niks": ["001", "002"], **kw}
        return self.client.post("/requests/g/lembur/new/", d)


class ExtraWorkTests(ExtraWorkBase):
    def test_batch_creates_one_request_per_employee_and_notifies_hrd_once(self):
        r = self.post(); self.assertEqual(r.status_code, 302)
        rows = ChangeRequest.objects.filter(type="lembur"); self.assertEqual(rows.count(), 2)
        self.assertTrue(all(x.status == "pending" and x.payload["minutes"] == 120 for x in rows))
        self.assertEqual(len({x.payload["batch"] for x in rows}), 1)
        self.assertEqual(Notification.objects.filter(user=self.hrd).count(), 1)
        self.assertTrue(AuditLog.objects.filter(action="extra_work_create").exists())

    def test_admin_cannot_include_other_department(self):
        r = self.post(niks=["001", "003"]); self.assertEqual(r.status_code, 200); self.assertEqual(ChangeRequest.objects.count(), 0)
        self.assertContains(r, "003")

    def test_hrd_may_cross_departments_via_extra_niks(self):
        self.post(self.hrd, niks=[], extra_niks="001, 003"); self.assertEqual(ChangeRequest.objects.count(), 2)

    def test_poli_forbidden(self):
        self.client.force_login(self.poli); self.assertEqual(self.client.get("/requests/g/lembur/new/").status_code, 403)

    def test_duration_limits_and_midnight(self):
        self.assertEqual(minutes_between(time(22, 0), time(2, 0)), 240); self.assertEqual(minutes_between(time(8, 0), time(8, 0)), 1440)
        self.assertEqual(self.post(start="16:00", end="21:00").status_code, 200)  # 300 menit > 240
        self.assertEqual(self.post(kind="standby", start="12:00", end="14:30").status_code, 200)  # 150 > 120
        self.assertEqual(ChangeRequest.objects.count(), 0)
        self.post(start="22:00", end="02:00"); self.assertEqual(ChangeRequest.objects.filter(payload__minutes=240).count(), 2)

    def test_date_window(self):
        self.assertEqual(self.post(date=(self.today - timedelta(days=30)).isoformat()).status_code, 200)
        self.assertEqual(self.post(date=(self.today + timedelta(days=60)).isoformat()).status_code, 200)
        self.assertEqual(ChangeRequest.objects.count(), 0)

    def test_overlap_and_daily_cap_and_leave(self):
        self.post(); n = ChangeRequest.objects.count()
        self.assertEqual(self.post(start="17:00", end="19:00").status_code, 200)  # jam bertumpuk
        self.assertEqual(self.post(start="18:00", end="21:00").status_code, 200)  # 120+180 > 240
        self.assertEqual(ChangeRequest.objects.count(), n)
        self.post(start="19:00", end="21:00"); self.assertEqual(ChangeRequest.objects.count(), n + 2)  # pas 240: boleh
        ChangeRequest.objects.create(type="izin", employee=self.b, department=self.prd, status="approved", requested_by=self.hrd,
                                     payload={"start_date": (self.today + timedelta(days=2)).isoformat(), "end_date": (self.today + timedelta(days=3)).isoformat()})
        self.assertEqual(self.post(date=(self.today + timedelta(days=2)).isoformat()).status_code, 200)

    def test_approve_is_final_and_batch_decision_hrd_only(self):
        self.post(); b = ChangeRequest.objects.first().payload["batch"]
        self.client.force_login(self.adm); self.assertEqual(self.client.post(f"/requests/batch/{b}/approved/").status_code, 403)
        self.client.force_login(self.hrd); self.client.post(f"/requests/batch/{b}/approved/")
        self.assertEqual(set(ChangeRequest.objects.values_list("status", flat=True)), {"executed"})
        self.assertEqual(Notification.objects.filter(user=self.adm, kind="approval").count(), 1)

    def test_batch_reject_needs_reason(self):
        self.post(); b = ChangeRequest.objects.first().payload["batch"]; self.client.force_login(self.hrd)
        self.client.post(f"/requests/batch/{b}/rejected/", {"note": ""}); self.assertEqual(ChangeRequest.objects.filter(status="pending").count(), 2)
        self.client.post(f"/requests/batch/{b}/rejected/", {"note": "Tidak perlu"}); self.assertEqual(ChangeRequest.objects.filter(status="rejected").count(), 2)

    def test_list_scope_batch_filter_and_summary(self):
        self.post(); self.post(self.adm2, niks=["003"], date=(self.today + timedelta(days=1)).isoformat())
        self.client.force_login(self.adm); r = self.client.get("/requests/g/lembur/")
        self.assertEqual(len(r.context["page"]), 2); self.assertContains(r, "16:00–18:00 (120 mnt)")
        self.assertEqual(self.client.get("/requests/g/lembur/?batch=zzz").status_code, 404)
        self.client.force_login(self.hrd); self.assertEqual(len(self.client.get("/requests/g/lembur/").context["page"]), 3)

    def test_generic_form_does_not_offer_dedicated_types_and_menu_item(self):
        self.client.force_login(self.adm); r = self.client.get("/requests/new/")
        self.assertNotIn("standby", [c[0] for c in r.context["form"].fields["type"].choices])
        self.assertContains(self.client.get("/requests/g/lembur/"), 'href="/requests/g/lembur/"')
        r = self.client.get("/requests/g/lembur/new/"); self.assertContains(r, "Emp 001"); self.assertNotContains(r, "Emp 003")


class ExtraWorkUndoRecapTests(ExtraWorkBase):
    """Putaran 31: pembatalan lembur yang sudah final (A80) + rekap bulanan."""
    def approved(self):
        self.post(); self.client.force_login(self.hrd)
        b = ChangeRequest.objects.filter(type="lembur").first().payload["batch"]
        self.client.post(f"/requests/batch/{b}/approved/"); return b

    def test_hrd_can_cancel_executed_with_reason_and_slot_is_freed(self):
        self.approved(); r = ChangeRequest.objects.filter(type="lembur").first(); self.assertEqual(r.status, "executed")
        self.client.post(f"/requests/{r.pk}/cancelled/", {"note": ""}); r.refresh_from_db(); self.assertEqual(r.status, "executed")  # alasan wajib
        self.client.post(f"/requests/{r.pk}/cancelled/", {"note": "Salah setuju"}); r.refresh_from_db(); self.assertEqual(r.status, "cancelled")
        self.assertTrue(Notification.objects.filter(user=self.adm, title__contains="dibatalkan HRD").exists())
        self.assertEqual(self.post(niks=[r.employee.nik]).status_code, 302)  # jam yang sama bisa diajukan lagi

    def test_dept_admin_cannot_cancel_executed(self):
        self.approved(); r = ChangeRequest.objects.filter(type="lembur").first()
        self.client.force_login(self.adm); self.client.post(f"/requests/{r.pk}/cancelled/", {"note": "x"}); r.refresh_from_db()
        self.assertEqual(r.status, "executed")

    def test_executed_swap_cannot_be_cancelled(self):
        from .services import transition
        self.assertIn("executed", ChangeRequest.FLOW["approved"]); self.assertNotIn("executed", ChangeRequest.FLOW)  # hanya lembur/stand by yang punya jalur batal
        r = ChangeRequest.objects.create(type="mutasi_jabatan", employee=self.a, department=self.prd, payload={}, requested_by=self.adm, status="executed")
        with self.assertRaises(ValueError): transition(r, "cancelled", self.hrd, "x")

    def test_recap_counts_only_executed_and_respects_scope(self):
        self.approved(); self.post(kind="standby", start="12:00", end="12:30", niks=["001"])  # pending: tidak dihitung
        m = self.today.strftime("%Y-%m")
        self.client.force_login(self.hrd); r = self.client.get(f"/requests/g/lembur/rekap/?month={m}")
        self.assertContains(r, "Emp 001"); self.assertEqual(r.context["totals"], {"lembur": 240, "standby": 0, "total": 240})
        self.client.force_login(self.adm2); self.assertNotContains(self.client.get(f"/requests/g/lembur/rekap/?month={m}"), "Emp 001")
        self.client.force_login(self.poli); self.assertEqual(self.client.get("/requests/g/lembur/rekap/").status_code, 403)

    def test_recap_excludes_cancelled_and_exports(self):
        self.approved(); r = ChangeRequest.objects.filter(type="lembur", employee=self.a).first()
        self.client.post(f"/requests/{r.pk}/cancelled/", {"note": "salah"}); m = self.today.strftime("%Y-%m")
        self.assertEqual(self.client.get(f"/requests/g/lembur/rekap/?month={m}").context["totals"]["lembur"], 120)
        e = self.client.get(f"/requests/g/lembur/rekap/?month={m}&fmt=csv"); self.assertEqual(e.status_code, 200); self.assertIn(b"Emp 002", e.content)
        self.assertEqual(self.client.get("/requests/g/lembur/rekap/?month=bukan").status_code, 200)


class ExtraWorkReminderTests(ExtraWorkBase):
    """Putaran 32: pengingat harian ke HRD untuk pengajuan yang belum diputuskan."""
    def test_reminds_hrd_once_and_only_for_imminent_dates(self):
        from .management.commands.remind_extra_work import run
        self.post(date=(self.today + timedelta(days=10)).isoformat())
        self.assertEqual(run(self.today), 0)  # tanggal masih jauh
        self.post(date=self.today.isoformat(), start="08:00", end="09:00")
        self.assertEqual(run(self.today), 1); self.assertEqual(run(self.today), 0)  # tidak menumpuk
        n = Notification.objects.get(user=self.hrd, title__startswith="Stand By & Lembur:"); self.assertIn("2 pengajuan", n.title)
        self.assertFalse(Notification.objects.filter(user=self.adm, title__startswith="Stand By & Lembur:").exists())

    def test_late_count_and_no_reminder_after_decision(self):
        from .management.commands.remind_extra_work import run
        self.post(date=(self.today - timedelta(days=2)).isoformat())
        run(self.today); self.assertIn("sudah lewat", Notification.objects.get(user=self.hrd, title__startswith="Stand By & Lembur:").title)
        Notification.objects.all().update(is_read=True)
        self.client.force_login(self.hrd); b = ChangeRequest.objects.filter(type="lembur").first().payload["batch"]
        self.client.post(f"/requests/batch/{b}/approved/"); self.assertEqual(run(self.today), 0)
