"""UI/UX putaran 14: menu samping per peran, breadcrumb/footer, panel dashboard per peran (VISION → Rujukan UI/UX)."""
import json
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from apps.core.models import Role, User
from apps.hr.models import ChangeRequest, Department, Employee, Position
from apps.poli.models import MedicalRecord, Medicine

PW = "kata-sandi-panjang-123"


class DashboardUiBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d1, cls.d2 = Department.objects.create(code="A", name="Produksi"), Department.objects.create(code="B", name="Gudang")
        pos = Position.objects.create(name="Staff")
        mk = lambda nik, name, d: Employee.objects.create(nik=nik, name=name, gender="L", department=d, position=pos, join_date=timezone.localdate() - timedelta(days=400))
        cls.e1, cls.e2 = mk("001", "Budi Santoso", cls.d1), mk("002", "Rina Amelia", cls.d2)
        cls.su = User.objects.create_user("su", password=PW, role=Role.SUPERADMIN)
        cls.hrd = User.objects.create_user("hrd", password=PW, role=Role.HRD)
        cls.adm = User.objects.create_user("adm", password=PW, role=Role.DEPT_ADMIN, department=cls.d1)
        cls.poli = User.objects.create_user("poli", password=PW, role=Role.POLI)

    def get(self, user, path):
        self.client.force_login(user); return self.client.get(path)

    def req(self, emp, status="pending", type_="cuti"):
        return ChangeRequest.objects.create(type=type_, employee=emp, department=emp.department, status=status, requested_by=self.adm)

    def visit(self, emp, days_ago=0, kind="berobat", complaint="RAHASIA-KELUHAN"):
        return MedicalRecord.objects.create(employee=emp, kind=kind, visit_at=timezone.now() - timedelta(days=days_ago), complaint=complaint, created_by=self.poli)


class NavTests(DashboardUiBase):
    def hrefs(self, user):
        h = self.get(user, "/").content.decode(); return h

    def test_menu_per_role(self):
        want = {"su": ['href="/users/"', 'href="/audit/"', 'href="/poli/"', 'href="/hrd/"', 'href="/master/department/"'],
                "hrd": ['href="/hrd/"', 'href="/leave/"', 'href="/master/department/"'],
                "adm": ['href="/employees/"', 'href="/requests/"'],
                "poli": ['href="/poli/"', 'href="/poli/medicines/"', 'href="/poli/records/"']}
        deny = {"hrd": ['href="/poli/', 'href="/users/"', 'href="/audit/"'],
                "adm": ['href="/poli/', 'href="/hrd/"', 'href="/leave/"', 'href="/master/', 'href="/users/"', 'href="/audit/"'],
                "poli": ['href="/hrd/"', 'href="/leave/"', 'href="/master/', 'href="/users/"', 'href="/audit/"', 'href="/requests/"']}
        for name, needles in want.items():
            h = self.hrefs(User.objects.get(username=name))
            for n in needles: self.assertIn(n, h, (name, n))
            for n in deny.get(name, []): self.assertNotIn(n, h, (name, n))

    def test_exactly_one_item_active_and_breadcrumb(self):
        h = self.get(self.hrd, "/employees/").content.decode()
        self.assertEqual(h.count('aria-current="page"'), 1)
        self.assertIn('class="crumb"', h); self.assertIn("<b>Data Karyawan</b>", h)
        self.assertNotIn('class="crumb"', self.get(self.hrd, "/").content.decode())  # dashboard punya judul sendiri
        h = self.get(self.su, "/master/position/").content.decode()
        self.assertIn('href="/master/department/" aria-current="page"', h)  # semua jenis master = satu butir
        h = self.get(self.su, "/poli/medicines/").content.decode()
        self.assertEqual(h.count('aria-current="page"'), 1); self.assertIn('href="/poli/medicines/" aria-current="page"', h)

    def test_shell_parts(self):
        h = self.get(self.hrd, "/employees/").content.decode()
        for needle in ('class="side"', "Internal Use Only", "v1.1.0", 'class="bell"', 'id="scrim"', "Ganti sandi"):
            self.assertIn(needle, h)

    def test_login_has_no_sidebar_and_is_branded(self):
        self.client.logout(); h = self.client.get("/login/").content.decode()
        self.assertIn('class="auth"', h); self.assertNotIn('class="side"', h); self.assertNotIn("Internal Use Only", h)
        self.assertIn("HRIS &amp; Poliklinik PT X", h)

    def test_bell_badge_counts_unread_only_own(self):
        from apps.core.models import Notification
        for _ in range(3): Notification.objects.create(user=self.hrd, kind="request", title="x")
        Notification.objects.create(user=self.hrd, kind="request", title="sudah", is_read=True)
        Notification.objects.create(user=self.adm, kind="request", title="orang lain")
        h = self.get(self.hrd, "/employees/").content.decode()
        self.assertIn('aria-label="Notifikasi, 3 belum dibaca"', h)
        self.assertNotIn("Notifikasi, ", self.get(self.poli, "/employees/").content.decode())


class PanelsTests(DashboardUiBase):
    def panels(self, user, q=""):
        r = self.get(user, "/api/dashboard/panels/" + q); self.assertEqual(r.status_code, 200); return r.json()

    def test_requires_login_and_role(self):
        self.client.logout(); self.assertEqual(self.client.get("/api/dashboard/panels/").status_code, 401)
        u = User.objects.create_user("nobody", password=PW, role="")
        self.assertEqual(self.get(u, "/api/dashboard/panels/").status_code, 403)

    def test_hrd_has_no_poli_data_and_attendance_is_honest_empty(self):
        self.visit(self.e1); self.req(self.e1)
        p = self.panels(self.hrd)
        self.assertIsNone(p["attendance"])  # Tahap 6 belum ada → bukan angka karangan
        for k in ("visits", "visits6m", "deltas"): self.assertNotIn(k, p)
        self.assertNotIn("RAHASIA-KELUHAN", json.dumps(p)); self.assertNotIn("Pemeriksaan poliklinik", json.dumps(p))
        self.assertEqual(p["side"]["title"], "Pending Approval"); self.assertEqual(len(p["side"]["items"]), 1)

    def test_superadmin_gets_poli_aggregates_but_no_patient_names(self):
        self.visit(self.e1); self.visit(self.e2, days_ago=1); self.visit(self.e2, days_ago=1)
        p = self.panels(self.su); s = json.dumps(p)
        self.assertEqual(p["visits"]["values"][-1], 1); self.assertEqual(p["visits"]["values"][-2], 2); self.assertEqual(len(p["visits"]["labels"]), 7)
        self.assertEqual(sum(p["visits6m"]["values"]), 3); self.assertEqual(len(p["visits6m"]["labels"]), 6)
        self.assertEqual(p["deltas"]["visits_today"], {"diff": -1, "vs": "kemarin"})
        self.assertNotIn("Budi", s.replace("Budi Santoso", "")) ; self.assertNotIn("RAHASIA-KELUHAN", s)
        self.assertNotIn("Pemeriksaan poliklinik", s)  # aktivitas medis hanya Poli
        self.assertEqual(self.panels(self.su, "?visits=30")["visits"]["days"], 30)
        self.assertEqual(self.panels(self.su, "?visits=zzz")["visits"]["days"], 7)  # masukan sampah tidak 500

    def test_poli_sees_patient_activity_but_never_complaint_or_hr_data(self):
        self.visit(self.e1, kind="kecelakaan_kerja"); self.req(self.e1)
        low = Medicine.objects.create(code="M1", name="Paracetamol", unit="tablet", stock=2, min_stock=10)
        p = self.panels(self.poli); s = json.dumps(p)
        self.assertEqual(p["activity"][0]["by"], "Budi Santoso (Produksi)"); self.assertEqual(p["activity"][0]["note"], "Kecelakaan kerja")
        self.assertNotIn("RAHASIA-KELUHAN", s); self.assertNotIn("leave", p); self.assertNotIn("attendance", p)
        self.assertEqual(p["side"]["items"][0]["url"], f"/poli/medicines/{low.pk}/"); self.assertEqual(p["deltas"]["visits_today"]["diff"], 1)

    def test_dept_admin_only_own_department(self):
        self.req(self.e1); self.req(self.e2); self.req(self.e2, status="approved")
        p = self.panels(self.adm); s = json.dumps(p)
        self.assertEqual(len(p["activity"]), 1); self.assertEqual(len(p["side"]["items"]), 1)
        self.assertNotIn("Rina", s); self.assertNotIn("Gudang", s)
        for k in ("visits", "visits6m", "deltas"): self.assertNotIn(k, p)
        self.assertEqual(p["leave"]["total"], 1)

    def test_leave_donut_groups_and_periods(self):
        for st in ("approved", "executed", "pending", "submitted", "rejected"): self.req(self.e1, status=st)
        self.req(self.e1, status="approved", type_="izin")  # bukan cuti → tidak dihitung
        l = self.panels(self.hrd)["leave"]
        self.assertEqual({i["label"]: i["n"] for i in l["items"]}, {"Disetujui": 2, "Menunggu": 2, "Ditolak": 1}); self.assertEqual(l["total"], 5)
        self.assertEqual(self.panels(self.hrd, "?leave=year")["leave"]["period"], "Tahun ini")
        self.assertEqual(self.panels(self.hrd, "?leave=prev")["leave"]["total"], 0)
        self.assertEqual(self.panels(self.hrd, "?leave=ngawur")["leave"]["period"], "Bulan ini")

    def test_query_count_does_not_grow_with_rows(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.client.force_login(self.hrd)
        with CaptureQueriesContext(connection) as a: self.client.get("/api/dashboard/panels/")
        for _ in range(15): self.req(self.e1)
        with CaptureQueriesContext(connection) as b: self.client.get("/api/dashboard/panels/")
        self.assertEqual(len(a), len(b))

    def test_dashboard_api_superadmin_adds_visits_today_hrd_does_not(self):
        self.visit(self.e1)
        self.assertEqual(self.get(self.su, "/api/dashboard/").json()["visits_today"], 1)
        self.assertNotIn("visits_today", self.get(self.hrd, "/api/dashboard/").json())

    def test_home_renders_role_greeting_for_every_role(self):
        for u in (self.su, self.hrd, self.adm, self.poli):
            h = self.get(u, "/").content.decode()
            self.assertIn("Selamat datang", h); self.assertIn('id="kpis"', h); self.assertIn("PT X", h)
