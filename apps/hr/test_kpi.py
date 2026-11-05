from datetime import date
from django.test import TestCase
from django.urls import reverse
from . import kpi
from .models import AttendanceDaily as AD, Department, Employee
from apps.core.models import Role, User


class KpiTests(TestCase):
    def setUp(self):
        self.d = Department.objects.create(name="Produksi", code="PRD")
        self.e = Employee.objects.create(nik="DM1", name="Budi", department=self.d, join_date=date(2025, 1, 1))
        for i, st in enumerate(["alfa", "alfa", "alfa", "hadir", "hadir"], 1):
            AD.objects.create(employee=self.e, date=date(2026, 10, i), department=self.d, status=st)
        self.hrd = User.objects.create_user("hrd1", password="x", role=Role.HRD)

    def test_score_and_suggestion(self):
        rows = kpi.build(AD.objects.filter(date__year=2026, date__month=10), date(2026, 10, 1), date(2026, 10, 31), today=date(2026, 11, 4))
        r = rows[0]
        self.assertEqual(r["alfa"], 3); self.assertEqual(r["score"], 70); self.assertEqual(r["grade"], "C"); self.assertEqual(r["saran"], "SP 1")

    def test_pages_render(self):
        self.client.force_login(self.hrd)
        for n in ("kpi_overview", "kpi_attendance", "kpi_warnings", "kpi_departments"):
            r = self.client.get(reverse(n), {"month": "2026-10"}); self.assertEqual(r.status_code, 200, n)
        self.assertContains(self.client.get(reverse("kpi_overview"), {"month": "2026-10"}), "Budi")
        self.assertEqual(self.client.get(reverse("kpi_overview"), {"month": "2026-10", "export": 1}).status_code, 200)


class KpiReviewTests(KpiTests):
    def test_review_saves_and_blends(self):
        self.client.force_login(self.hrd)
        url = reverse("kpi_review_form", args=[self.e.pk]) + "?month=2026-10"
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {"month": "2026-10", "teamwork": 5, "responsibility": 5, "initiative": 5, "quality": 5, "compliance": 5, "note": "baik"})
        self.assertEqual(r.status_code, 302)
        row = kpi.build(AD.objects.filter(date__year=2026, date__month=10), date(2026, 10, 1), date(2026, 10, 31), today=date(2026, 11, 4))[0]
        self.assertEqual(row["review"], 100); self.assertEqual(row["disc"], 70); self.assertEqual(row["score"], 79)  # 0.7*70 + 0.3*100
        self.assertEqual(self.client.post(url, {"month": "2026-10", "teamwork": 9}).status_code, 200)  # tidak valid: tetap di form

    def test_pages_and_sp_prefill(self):
        self.client.force_login(self.hrd)
        for n, a in (("kpi_reviews", []), ("kpi_employee", [self.e.pk])):
            self.assertEqual(self.client.get(reverse(n, args=a), {"month": "2026-10"}).status_code, 200, n)
        r = self.client.get(reverse("kpi_attendance"), {"month": "2026-10"}); self.assertContains(r, "Buat SP 1")
        r = self.client.get(reverse("hrd_warning_new"), {"nik": "DM1", "level": "1", "violation": "Alfa 3 hari"}); self.assertContains(r, 'value="DM1"'); self.assertContains(r, "Alfa 3 hari")

    def test_dept_admin_scope(self):
        other = Department.objects.create(name="Packaging", code="PKG"); e2 = Employee.objects.create(nik="DM2", name="Sari", department=other, join_date=date(2025, 1, 1))
        adm = User.objects.create_user("adm1", password="x", role=Role.DEPT_ADMIN, department=self.d)
        self.client.force_login(adm)
        self.assertEqual(self.client.get(reverse("kpi_review_form", args=[e2.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("kpi_warnings")).status_code, 403)
