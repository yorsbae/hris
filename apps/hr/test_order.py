from datetime import date, timedelta
from django.test import TestCase, override_settings
from apps.core.models import AuditLog, Notification, Role, User
from apps.hr.models import Department, Position, StaffOrder
from cryptography.fernet import Fernet

PW = "kata-sandi-panjang-123"


@override_settings(FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode())
class OrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a, cls.b = Department.objects.create(code="A", name="Produksi"), Department.objects.create(code="B", name="Gudang")
        cls.hrd = User.objects.create_user("hrd", password=PW, role=Role.HRD)
        cls.su = User.objects.create_user("su", password=PW, role=Role.SUPERADMIN)
        cls.adm_a = User.objects.create_user("adm_a", password=PW, role=Role.DEPT_ADMIN, department=cls.a)
        cls.adm_b = User.objects.create_user("adm_b", password=PW, role=Role.DEPT_ADMIN, department=cls.b)
        cls.pay = User.objects.create_user("pay", password=PW, role=Role.PAYROLL)

    def login(self, n): self.client.force_login(User.objects.get(username=n))

    def data(self, **k):
        d = {"section": "Packing", "qty": 5, "needed_from": (date.today() + timedelta(days=3)).isoformat(), "urgency": "normal", "reason": "Banyak pesanan ekspor, tim kewalahan."}
        d.update(k); return d

    def mk(self, dept=None, **k):
        return StaffOrder.objects.create(department=dept or self.a, section="Packing", qty=5, needed_from=date.today(), reason="alasan panjang cukup", requested_by=self.adm_a, **k)

    def test_dept_admin_creates_order_with_own_department_and_hrd_notified(self):
        self.login("adm_a")
        r = self.client.post("/orders/new/", self.data(department=self.b.pk))  # input departemen palsu diabaikan
        o = StaffOrder.objects.get(); self.assertRedirects(r, f"/orders/{o.pk}/")
        self.assertEqual((o.department, o.status, o.requested_by), (self.a, "menunggu", self.adm_a))
        self.assertTrue(Notification.objects.filter(user=self.hrd, link=f"/orders/{o.pk}/").exists())
        self.assertTrue(AuditLog.objects.filter(action="order_create").exists())

    def test_validation(self):
        self.login("adm_a")
        for bad in (self.data(qty=0), self.data(reason="pendek"), self.data(needed_from="2000-01-01"), self.data(needed_until=(date.today() - timedelta(days=1)).isoformat())):
            self.assertEqual(self.client.post("/orders/new/", bad).status_code, 200)
        self.assertEqual(StaffOrder.objects.count(), 0)

    def test_hrd_cannot_create_payroll_blocked(self):
        self.login("hrd"); self.assertEqual(self.client.post("/orders/new/", self.data()).status_code, 403)
        self.login("pay"); self.assertEqual(self.client.get("/orders/").status_code, 403)

    def test_scope_other_department_404(self):
        o = self.mk(); self.login("adm_b")
        self.assertEqual(self.client.get(f"/orders/{o.pk}/").status_code, 404)
        self.assertEqual(self.client.post(f"/orders/{o.pk}/cancel/").status_code, 404)
        self.assertNotContains(self.client.get("/orders/"), "Packing")
        self.login("adm_a"); self.assertEqual(self.client.get(f"/orders/{o.pk}/").status_code, 200)

    def test_hrd_approve_full_and_partial(self):
        o, o2 = self.mk(), self.mk(); self.login("hrd")
        self.client.post(f"/orders/{o.pk}/acc/", {"note": "oke"}); o.refresh_from_db()
        self.assertEqual((o.status, o.qty_approved, o.decided_by), ("acc", 5, self.hrd))
        self.client.post(f"/orders/{o2.pk}/acc/", {"qty_approved": "3"}); o2.refresh_from_db(); self.assertEqual(o2.qty_approved, 3)
        n = Notification.objects.filter(user=self.adm_a, link=f"/orders/{o2.pk}/").get(); self.assertIn("sebagian", n.title)
        self.assertFalse(Notification.objects.filter(user=self.adm_b).exists())

    def test_approve_more_than_requested_rejected(self):
        o = self.mk(); self.login("hrd"); self.client.post(f"/orders/{o.pk}/acc/", {"qty_approved": "9"}); o.refresh_from_db()
        self.assertEqual(o.status, "menunggu")

    def test_reject_requires_reason_and_notifies_with_reason(self):
        o = self.mk(); self.login("hrd")
        self.client.post(f"/orders/{o.pk}/tolak/", {"note": ""}); o.refresh_from_db(); self.assertEqual(o.status, "menunggu")
        self.client.post(f"/orders/{o.pk}/tolak/", {"note": "Anggaran headcount penuh"}); o.refresh_from_db()
        self.assertEqual((o.status, o.decision_note), ("ditolak", "Anggaran headcount penuh"))
        self.assertIn("Anggaran headcount penuh", Notification.objects.get(user=self.adm_a, link=f"/orders/{o.pk}/").title)
        self.assertTrue(AuditLog.objects.filter(action="order_tolak").exists())

    def test_decision_is_final(self):
        o = self.mk(); self.login("hrd"); self.client.post(f"/orders/{o.pk}/acc/")
        self.client.post(f"/orders/{o.pk}/tolak/", {"note": "berubah pikiran"}); o.refresh_from_db(); self.assertEqual(o.status, "acc")

    def test_dept_admin_cannot_decide(self):
        o = self.mk(); self.login("adm_a"); self.assertEqual(self.client.post(f"/orders/{o.pk}/acc/").status_code, 403)
        o.refresh_from_db(); self.assertEqual(o.status, "menunggu")

    def test_cancel_only_while_pending_by_own_department(self):
        o = self.mk(); self.login("adm_a"); self.client.post(f"/orders/{o.pk}/cancel/"); o.refresh_from_db(); self.assertEqual(o.status, "dibatalkan")
        o2 = self.mk(); self.login("hrd"); self.client.post(f"/orders/{o2.pk}/acc/"); self.login("adm_a")
        self.client.post(f"/orders/{o2.pk}/cancel/"); o2.refresh_from_db(); self.assertEqual(o2.status, "acc")

    def test_get_does_not_change_state(self):
        o = self.mk(); self.login("hrd"); self.assertEqual(self.client.get(f"/orders/{o.pk}/acc/").status_code, 405)

    def test_menu_present_and_hrd_sees_all(self):
        self.mk(); self.mk(self.b); self.login("hrd"); r = self.client.get("/orders/")
        self.assertEqual(len(r.context["page"]), 2); self.assertContains(r, "/orders/")


class OrderDashboardTests(OrderTests):
    """Kartu tugas HRD: order menunggu & pengajuan disetujui-belum-dilaksanakan; Admin Dept tidak menerimanya."""
    def dash(self, who): self.login(who); return self.client.get("/api/dashboard/").json()

    def test_hrd_counts_pending_orders_and_unexecuted_approved(self):
        from apps.hr.models import ChangeRequest, Employee, Position
        pos = Position.objects.create(name="Staff")
        e = Employee.objects.create(nik="001", name="Budi", gender="L", department=self.a, position=pos, join_date=date(2024, 1, 1))
        mk = lambda st: ChangeRequest.objects.create(type="izin", employee=e, department=self.a, status=st, payload={}, requested_by=self.adm_a)
        mk("approved"); mk("approved"); mk("executed"); mk("pending")
        self.mk(); self.mk(status="acc")
        d = self.dash("hrd"); self.assertEqual((d["approved_unexecuted"], d["orders_pending"]), (2, 1))
        self.assertNotIn("orders_pending", self.dash("adm_a"))
