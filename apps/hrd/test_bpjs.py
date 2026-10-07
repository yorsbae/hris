import threading
from datetime import date
from io import StringIO
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from apps.hr.models import Employee
from . import services
from .models import BpjsMembership, BpjsStatusLog
from .test_base import HrdBase


class BpjsTests(HrdBase):
    def post(self, emp, **kw):
        data = {"scheme": "kes", "status": "aktif", "effective_date": "2025-01-01", "note": ""}; data.update(kw)
        return self.client.post(reverse("hrd_bpjs_detail", args=[emp.pk]), data)

    def setUp(self): self.login()

    def test_first_record_creates_membership_log_and_audit(self):
        r = self.post(self.e1); self.assertEqual(r.status_code, 302)
        m = BpjsMembership.objects.get(employee=self.e1, scheme="kes"); self.assertEqual((m.status, str(m.effective_date)), ("aktif", "2025-01-01"))
        l = BpjsStatusLog.objects.get(employee=self.e1); self.assertEqual((l.old_status, l.new_status), ("", "aktif"))
        a = self.last_audit("bpjs_status"); self.assertEqual(a.after["status"], "aktif")

    def test_audit_never_contains_bpjs_number(self):
        self.post(self.e1)
        blob = str(list(__import__("apps.core.models", fromlist=["AuditLog"]).AuditLog.objects.values_list("before", "after")))
        self.assertNotIn("0001234567890", blob); self.assertNotIn("TK-9988776655", blob)

    def test_change_keeps_history_not_overwrite(self):
        self.post(self.e1); self.post(self.e1, status="nonaktif", effective_date="2025-06-01", note="Resign")
        self.assertEqual(BpjsMembership.objects.filter(employee=self.e1, scheme="kes").count(), 1)
        self.assertEqual(BpjsMembership.objects.get(employee=self.e1, scheme="kes").status, "nonaktif")
        self.assertEqual(list(BpjsStatusLog.objects.filter(employee=self.e1).order_by("id").values_list("old_status", "new_status")), [("", "aktif"), ("aktif", "nonaktif")])

    def test_schemes_are_independent(self):
        self.post(self.e1, scheme="kes"); self.post(self.e1, scheme="tk", status="nonaktif", note="Belum didaftarkan")
        self.assertEqual(BpjsMembership.objects.get(employee=self.e1, scheme="kes").status, "aktif")
        self.assertEqual(BpjsMembership.objects.get(employee=self.e1, scheme="tk").status, "nonaktif")

    def test_validation_rules(self):
        self.post(self.e1)
        self.assertContains(self.post(self.e1), "Sama dengan status saat ini")                                                          # tanpa perubahan
        self.assertContains(self.post(self.e1, status="nonaktif", effective_date="2025-06-01"), "Alasan wajib")                         # nonaktif perlu alasan
        self.assertContains(self.post(self.e1, status="nonaktif", effective_date="2024-12-31", note="x"), "lebih awal")                 # mundur dari status saat ini
        self.assertContains(self.post(self.e2, effective_date="2023-12-31"), "sebelum tanggal masuk")                                   # sebelum tanggal masuk
        self.assertEqual(self.post(self.e2, scheme="zzz").status_code, 200); self.assertFalse(BpjsMembership.objects.filter(employee=self.e2).exists())
        self.assertEqual(BpjsStatusLog.objects.count(), 1)  # semua yang ditolak tidak menulis histori

    def test_service_rejects_stale_duplicate(self):
        services.set_bpjs_status(self.e1, "kes", "aktif", date(2025, 1, 1), "", self.hrd)
        with self.assertRaises(ValueError): services.set_bpjs_status(self.e1, "kes", "aktif", date(2025, 2, 1), "", self.hrd)
        self.assertEqual(BpjsStatusLog.objects.count(), 1)

    def test_service_rejects_backdated_change_even_without_form(self):
        services.set_bpjs_status(self.e1, "kes", "aktif", date(2025, 5, 1), "", self.hrd)
        with self.assertRaises(ValueError): services.set_bpjs_status(self.e1, "kes", "nonaktif", date(2025, 4, 30), "mundur", self.hrd)
        self.assertEqual(BpjsMembership.objects.get(employee=self.e1, scheme="kes").status, "aktif"); self.assertEqual(BpjsStatusLog.objects.count(), 1)

    def test_log_is_append_only(self):
        services.set_bpjs_status(self.e1, "kes", "aktif", date(2025, 1, 1), "", self.hrd)
        l = BpjsStatusLog.objects.get()
        l.note = "diubah"
        with self.assertRaises(PermissionError): l.save()
        with self.assertRaises(PermissionError): l.delete()

    def test_soft_deleted_employee_not_found(self):
        self.e3.soft_delete(self.hrd, "uji"); self.assertEqual(self.client.get(reverse("hrd_bpjs_detail", args=[self.e3.pk])).status_code, 404)

    def test_list_filters_and_never_shows_numbers(self):
        self.post(self.e1, scheme="kes"); self.post(self.e1, scheme="tk", status="nonaktif", note="x")
        services.set_bpjs_status(self.e2, "kes", "nonaktif", date(2025, 1, 1), "belum daftar", self.hrd)
        r = self.client.get("/hrd/bpjs/"); self.assertNotContains(r, "0001234567890"); self.assertNotContains(r, "TK-9988776655")
        names = lambda resp: sorted(e.nik for e in resp.context["page"])  # noqa: E731
        self.assertEqual(names(self.client.get("/hrd/bpjs/?kes=aktif")), ["001"])
        self.assertEqual(names(self.client.get("/hrd/bpjs/?kes=nonaktif")), ["002"])
        self.assertEqual(names(self.client.get("/hrd/bpjs/?kes=belum")), ["003"])           # karyawan aktif tanpa catatan; nonaktif tersaring default
        self.assertEqual(names(self.client.get("/hrd/bpjs/?tk=nonaktif")), ["001"])
        self.assertEqual(names(self.client.get("/hrd/bpjs/?q=sari")), ["002"])
        self.assertEqual(names(self.client.get(f"/hrd/bpjs/?department={self.d2.pk}")), ["002"])
        self.assertEqual(names(self.client.get("/hrd/bpjs/?emp=semua&kes=belum")), ["003", "009"])

    def test_anomaly_filter_inactive_employee_with_active_bpjs(self):
        services.set_bpjs_status(self.e_off, "tk", "aktif", date(2023, 2, 1), "", self.hrd)
        services.set_bpjs_status(self.e1, "tk", "aktif", date(2024, 2, 1), "", self.hrd)
        r = self.client.get("/hrd/bpjs/?emp=semua&anomaly=1"); self.assertEqual([e.nik for e in r.context["page"]], ["009"])

    def test_garbage_filters_do_not_crash(self):
        for qs in ("department=abc", "kes=%27%3Bdrop", "emp=zzz", "page=abc", "page=9999"):
            self.assertEqual(self.client.get("/hrd/bpjs/?" + qs).status_code, 200, qs)
        for qs in ("q=%00", "q=a%00b"):  # NUL: PostgreSQL → 500 bila lolos; middleware menolak 400
            self.client.raise_request_exception = False
            self.assertEqual(self.client.get("/hrd/bpjs/?" + qs).status_code, 400, qs)

    def test_pagination_server_side(self):
        Employee.objects.bulk_create([Employee(nik=f"X{i:04d}", name=f"Karyawan {i:04d}", gender="L", department=self.d1, join_date=date(2024, 1, 1)) for i in range(120)])
        r = self.client.get("/hrd/bpjs/"); self.assertEqual(len(r.context["page"]), 50); self.assertEqual(r.context["page"].paginator.num_pages, 3)

    def test_employee_detail_links_to_bpjs(self):
        self.assertContains(self.client.get(f"/employees/{self.e1.pk}/"), f"/hrd/bpjs/{self.e1.pk}/")

    def test_init_command_seeds_from_existing_numbers_idempotent(self):
        out = StringIO(); call_command("init_bpjs_status", "--dry-run", stdout=out)
        self.assertIn("K=1, TK=1", out.getvalue()); self.assertEqual(BpjsMembership.objects.count(), 0)  # simulasi: rollback
        call_command("init_bpjs_status", stdout=StringIO())
        self.assertEqual(BpjsMembership.objects.count(), 2); self.assertEqual(BpjsStatusLog.objects.count(), 2)
        m = BpjsMembership.objects.get(employee=self.e1, scheme="kes"); self.assertEqual((m.status, m.effective_date), ("aktif", self.e1.join_date))
        call_command("init_bpjs_status", stdout=StringIO()); self.assertEqual(BpjsMembership.objects.count(), 2)  # idempoten
        self.assertFalse(BpjsMembership.objects.filter(employee=self.e2).exists())  # tanpa nomor → tidak dibuat

    def test_init_command_does_not_override_existing_status(self):
        services.set_bpjs_status(self.e1, "kes", "nonaktif", date(2025, 1, 1), "keluar", self.hrd)
        call_command("init_bpjs_status", stdout=StringIO())
        self.assertEqual(BpjsMembership.objects.get(employee=self.e1, scheme="kes").status, "nonaktif")


class BpjsConcurrencyTests(TransactionTestCase):
    """Dua HRD mencatat status BPJS karyawan/program yang sama bersamaan: tepat satu menang, satu ditolak rapi (bukan 500), histori tidak ganda.
    Hanya bermakna di PostgreSQL (SQLite tidak punya row lock / concurrent writer)."""
    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_first_record(self):
        from apps.core.models import Role, User
        from apps.hr.models import Department, Employee
        d = Department.objects.create(code="Z", name="Z"); e = Employee.objects.create(nik="C1", name="Konkuren", gender="L", department=d, join_date=date(2024, 1, 1))
        u = User.objects.create_user("h", password="kata-sandi-panjang-123", role=Role.HRD)
        barrier, results = threading.Barrier(2), []

        def worker():
            try:
                barrier.wait(timeout=5)
                services.set_bpjs_status(e, "kes", "aktif", date(2025, 1, 1), "", u); results.append("ok")
            except ValueError: results.append("ditolak")
            except Exception as ex: results.append(f"ERROR {type(ex).__name__}")
            finally: connection.close()

        ts = [threading.Thread(target=worker) for _ in range(2)]
        [t.start() for t in ts]; [t.join(10) for t in ts]
        self.assertEqual(sorted(results), ["ditolak", "ok"], results)
        self.assertEqual(BpjsMembership.objects.filter(employee=e).count(), 1); self.assertEqual(BpjsStatusLog.objects.filter(employee=e).count(), 1)
