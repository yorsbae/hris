"""Filter lanjutan daftar karyawan (server-side), ukuran halaman, filter bergantung, filter tersimpan, dan pencarian global lintas modul."""
from datetime import date, timedelta
from unittest import mock
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from apps.core.models import Role, User
from apps.poli.models import Medicine
from .models import ChangeRequest, Contract, Department, Employee, Position, SavedFilter, Shift
from .saved_filters import MAX_PER_PAGE, clean_query
from .views import _years_ago

PW = "Sandi-Uji-12345"
TODAY = date.today()


def ago(years, days=0): return _years_ago(TODAY, years) - timedelta(days=days)


@override_settings(FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode())
class ListFilterBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d1, cls.d2 = Department.objects.create(code="PRD", name="Produksi"), Department.objects.create(code="GDG", name="Gudang")
        cls.p1, cls.p2, cls.p3 = Position.objects.create(name="Operator", level=1), Position.objects.create(name="Leader", level=2), Position.objects.create(name="Staff", level=1)
        cls.sh = Shift.objects.create(name="Pagi", code="P", start="07:00", end="15:00")
        mk = lambda nik, name, **k: Employee.objects.create(nik=nik, name=name, **{"gender": "L", "department": cls.d1, "position": cls.p1, "join_date": ago(2), "shift": cls.sh,
                                                                                    "birth_date": ago(30), "phone": "0812", "nik_ktp": "3326010101010001", "bank_account": "123", **k})
        cls.a = mk("E001", "Ani Satu", gender="P", education="S1", join_date=ago(0, 30), birth_date=ago(25))   # < 1 tahun, 25 th
        cls.b = mk("E002", "Budi Dua", education="SMA", join_date=ago(2), birth_date=ago(40), position=cls.p2)  # 1-3 th, 40 th, grade 2
        cls.c = mk("E003", "Citra Tiga", gender="P", department=cls.d2, education="S1", join_date=ago(4), birth_date=ago(35), position=cls.p3, supervisor=cls.b)
        cls.d = mk("E004", "Dodi Empat", join_date=ago(7), birth_date=ago(50), phone="", status="nonaktif")  # 5-10 th, telepon kosong
        cls.e = mk("E005", "Eka Lima", join_date=ago(12), birth_date=None, position=None, shift=None, nik_ktp="", bank_account="")  # > 10 th, banyak data kosong
        Contract.objects.create(employee=cls.a, number="K1", kind="PKWT", start=ago(0, 30), end=TODAY + timedelta(days=200), status="aktif")
        Contract.objects.create(employee=cls.b, number="K2", kind="PKWTT", start=ago(2), status="aktif")
        cls.hrd = User.objects.create_user("hrd", password=PW, role=Role.HRD)
        cls.su = User.objects.create_user("su", password=PW, role=Role.SUPERADMIN)
        cls.adm = User.objects.create_user("adm", password=PW, role=Role.DEPT_ADMIN, department=cls.d1)
        cls.poli = User.objects.create_user("poli", password=PW, role=Role.POLI)

    def login(self, name): self.client.force_login(User.objects.get(username=name))

    def niks(self, **params):
        r = self.client.get("/api/employees/", {"size": 200, **params}); self.assertEqual(r.status_code, 200)
        return sorted(x["nik"] for x in r.json()["results"])


class AdvancedFilterTests(ListFilterBase):
    def test_gender_and_education(self):
        self.login("hrd")
        self.assertEqual(self.niks(gender="P"), ["E001", "E003"]); self.assertEqual(self.niks(education="S1"), ["E001", "E003"]); self.assertEqual(self.niks(gender="X"), self.niks())

    def test_tenure_buckets_are_exclusive_and_exhaustive(self):
        self.login("hrd")
        got = {k: self.niks(tenure=k) for k in ("lt1", "1-3", "3-5", "5-10", "gt10")}
        self.assertEqual(got, {"lt1": ["E001"], "1-3": ["E002"], "3-5": ["E003"], "5-10": ["E004"], "gt10": ["E005"]})
        self.assertEqual(self.niks(tenure="ngawur"), self.niks())  # nilai tak dikenal diabaikan

    def test_age_range_excludes_unknown_birth_date(self):
        self.login("hrd")
        r = self.niks(age_min=26, age_max=45); self.assertEqual(r, ["E002", "E003"])      # E001 25 th (di bawah), E004 50 th (di atas), E005 tanpa tanggal lahir
        self.assertNotIn("E005", self.niks(age_min=0)); self.assertEqual(self.niks(age_max=25), ["E001"])
        self.assertEqual(self.niks(age_min="abc"), self.niks())

    def test_join_date_range(self):
        self.login("hrd")
        self.assertEqual(self.niks(join_from=ago(5).isoformat(), join_to=ago(1).isoformat()), ["E002", "E003"])
        self.assertEqual(self.niks(join_from="bukan-tanggal"), self.niks())

    def test_supervisor_filter(self):
        self.login("hrd"); self.assertEqual(self.niks(supervisor="E002"), ["E003"]); self.assertEqual(self.niks(supervisor="none"), ["E001", "E002", "E004", "E005"])

    def test_problem_filters(self):
        self.login("hrd")
        self.assertEqual(self.niks(problem="no_birth"), ["E005"]); self.assertEqual(self.niks(problem="no_phone"), ["E004"]); self.assertEqual(self.niks(problem="no_ktp"), ["E005"])
        self.assertEqual(self.niks(problem="no_bank"), ["E005"]); self.assertEqual(self.niks(problem="no_position"), ["E005"]); self.assertEqual(self.niks(problem="no_schedule"), ["E005"])
        self.assertEqual(self.niks(problem="no_contract"), ["E003", "E004", "E005"])
        self.assertEqual(self.niks(problem="any"), ["E003", "E004", "E005"])  # E001/E002 lengkap & berkontrak

    def test_contract_kind_and_grade(self):
        self.login("hrd")
        self.assertEqual(self.niks(contract_kind="PKWT"), ["E001"]); self.assertEqual(self.niks(contract_kind="PKWTT"), ["E002"])
        self.assertEqual(self.niks(level=2), ["E002"]); self.assertEqual(self.niks(level=1), ["E001", "E003", "E004"])

    def test_filters_combine_with_and(self):
        self.login("hrd"); self.assertEqual(self.niks(gender="P", education="S1", department=self.d2.pk), ["E003"]); self.assertEqual(self.niks(gender="P", tenure="gt10"), [])

    def test_hrd_only_filters_are_ignored_for_other_roles(self):
        for name in ("adm", "poli"):
            self.login(name)
            base = self.niks()
            for k, v in (("gender", "P"), ("education", "S1"), ("tenure", "lt1"), ("age_min", 40), ("problem", "any"), ("supervisor", "E002"), ("level", 2), ("join_from", TODAY.isoformat())):
                self.assertEqual(self.niks(**{k: v}), base, f"{name} tidak boleh memakai filter {k}")

    def test_export_follows_new_filters(self):
        self.login("hrd"); r = self.client.get("/api/employees/export/", {"format": "csv", "gender": "P"}); body = r.content.decode()
        self.assertIn("E001", body); self.assertIn("E003", body); self.assertNotIn("E002", body)

    def test_ids_endpoint_follows_new_filters(self):
        self.login("hrd"); d = self.client.get("/api/employees/ids/", {"problem": "no_birth"}).json(); self.assertEqual(d["ids"], [self.e.pk])

    def test_years_ago_clamps_leap_day(self):
        self.assertEqual(_years_ago(date(2028, 2, 29), 1), date(2027, 2, 28)); self.assertEqual(_years_ago(date(2026, 10, 15), 10), date(2016, 10, 15))


class PageSizeAndFacetTests(ListFilterBase):
    def test_page_size(self):
        self.login("hrd")
        for size, n in ((25, 5), (200, 5)): self.assertEqual(len(self.client.get("/api/employees/", {"size": size}).json()["results"]), n)
        with mock.patch("apps.hr.views.PAGE_SIZES", (2, 50)):
            d = self.client.get("/api/employees/", {"size": 2}).json(); self.assertEqual((len(d["results"]), d["size"], d["count"]), (2, 2, 5))
        d = self.client.get("/api/employees/", {"size": 7}).json(); self.assertEqual(d["size"], 50)  # ukuran tak dikenal → bawaan
        self.assertEqual(self.client.get("/api/employees/", {"size": "x"}).json()["size"], 50)

    def test_facets_positions_depend_on_department(self):
        self.login("hrd")
        names = lambda **q: [p["name"] for p in self.client.get("/api/employees/facets/", q).json()["positions"]]
        self.assertEqual(names(), ["Leader", "Operator", "Staff"])
        self.assertEqual(names(department=self.d2.pk), ["Staff"]); self.assertEqual(names(department=self.d1.pk), ["Leader", "Operator"])
        self.assertEqual(names(department="abc"), ["Leader", "Operator", "Staff"])

    def test_facets_respect_dept_admin_scope(self):
        self.login("adm"); r = self.client.get("/api/employees/facets/", {"department": self.d2.pk}).json()
        self.assertEqual([p["name"] for p in r["positions"]], ["Leader", "Operator"])  # departemen lain diabaikan: tetap departemen sendiri
        self.client.logout(); self.assertIn(self.client.get("/api/employees/facets/").status_code, (401, 302))


class SavedFilterTests(ListFilterBase):
    def post(self, **d): return self.client.post("/api/saved-filters/", {"page": "employees", **d})

    def test_save_list_update_delete(self):
        self.login("hrd")
        r = self.post(name="Perempuan S1", query="gender=P&education=S1&page=3&sort=name"); self.assertEqual(r.status_code, 200)
        f = SavedFilter.objects.get(); self.assertEqual((f.user, f.name), (self.hrd, "Perempuan S1")); self.assertEqual(f.query, "education=S1&gender=P&sort=name")  # page dibuang, terurut
        self.assertEqual(self.client.get("/api/saved-filters/").json()["results"][0]["query"], f.query)
        self.post(name="Perempuan S1", query="gender=L"); self.assertEqual(SavedFilter.objects.count(), 1); self.assertEqual(SavedFilter.objects.get().query, "gender=L")
        d = self.client.post(f"/api/saved-filters/{f.pk}/delete/").json(); self.assertEqual(d["results"], []); self.assertEqual(SavedFilter.objects.count(), 0)

    def test_private_per_user(self):
        self.login("hrd"); self.post(name="Milik HRD", query="gender=P"); f = SavedFilter.objects.get()
        self.login("adm"); self.assertEqual(self.client.get("/api/saved-filters/").json()["results"], [])
        self.assertEqual(self.client.post(f"/api/saved-filters/{f.pk}/delete/").status_code, 404); self.assertEqual(SavedFilter.objects.count(), 1)
        self.post(name="Milik HRD", query="status=aktif"); self.assertEqual(SavedFilter.objects.count(), 2)  # nama sama boleh antar pengguna

    def test_validation(self):
        self.login("hrd")
        self.assertEqual(self.post(name="", query="gender=P").status_code, 400); self.assertEqual(self.post(name="x" * 61, query="gender=P").status_code, 400)
        self.assertEqual(self.post(name="Kosong", query="").status_code, 400); self.assertEqual(self.post(name="Sampah", query="foo=bar&page=2").status_code, 400)  # kunci tak dikenal dibuang → kosong
        self.assertEqual(self.client.post("/api/saved-filters/", {"page": "lain", "name": "x", "query": "q=a"}).status_code, 400)
        self.assertEqual(SavedFilter.objects.count(), 0)

    def test_limit_per_user(self):
        self.login("hrd")
        for i in range(MAX_PER_PAGE): self.assertEqual(self.post(name=f"F{i}", query="gender=P").status_code, 200)
        self.assertEqual(self.post(name="Kelebihan", query="gender=P").status_code, 400); self.assertEqual(self.post(name="F0", query="gender=L").status_code, 200)  # perbarui tetap boleh

    def test_requires_login_and_post_for_delete(self):
        self.client.logout(); self.assertIn(self.client.get("/api/saved-filters/").status_code, (401, 302))
        self.login("hrd"); self.post(name="A", query="gender=P"); f = SavedFilter.objects.get(); self.assertEqual(self.client.get(f"/api/saved-filters/{f.pk}/delete/").status_code, 405)

    def test_clean_query(self):
        self.assertEqual(clean_query("employees", "q=ani&q=dobel&zzz=1&status=&department=%20%20&gender=P"), "gender=P&q=ani")
        self.assertEqual(clean_query("employees", "q=" + "x" * 101), ""); self.assertEqual(clean_query("employees", None), "")


class GlobalSearchTests(ListFilterBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Medicine.objects.create(code="OBT1", name="Paracetamol Ani", unit="tab", stock=10, min_stock=2)
        cls.req = ChangeRequest.objects.create(type="izin", employee=cls.a, department=cls.d1, status="pending", requested_by=cls.adm)
        cls.req2 = ChangeRequest.objects.create(type="izin", employee=cls.c, department=cls.d2, status="pending", requested_by=cls.hrd)

    def titles(self, q, **kw):
        r = self.client.get("/search/", {"q": q}); self.assertEqual(r.status_code, 200); return [s["title"] for s in r.context["sections"]], r

    def test_hrd_sees_employees_requests_masters(self):
        self.login("hrd"); t, r = self.titles("ani"); self.assertEqual(t, ["Karyawan", "Pengajuan"]); self.assertContains(r, "E001 · Ani Satu")
        self.assertIn("Departemen", self.titles("produk")[0]); self.assertIn("Jabatan", self.titles("lead")[0])
        self.assertNotIn("Obat", self.titles("paracetamol")[0]); self.assertNotIn("Pengguna", self.titles("hrd")[0])

    def test_dept_admin_is_scoped_to_own_department(self):
        self.login("adm"); t, r = self.titles("citra"); self.assertEqual(t, []); self.assertContains(r, "Tidak ada hasil")  # Citra di Gudang
        t, r = self.titles("ani"); self.assertEqual(t, ["Karyawan", "Pengajuan"]); self.assertEqual(self.titles("produk")[0], [])  # master hanya HRD

    def test_poli_sees_employees_and_medicines_only(self):
        self.login("poli"); self.assertIn("Obat", self.titles("paracetamol")[0]); self.assertIn("Karyawan", self.titles("ani")[0]); self.assertNotIn("Jabatan", self.titles("lead")[0])

    def test_superadmin_sees_users_and_medicines(self):
        self.login("su"); self.assertIn("Pengguna", self.titles("hrd")[0]); self.assertIn("Obat", self.titles("paracetamol")[0])

    def test_short_or_empty_query(self):
        self.login("hrd"); self.assertContains(self.client.get("/search/", {"q": "a"}), "terlalu pendek"); self.assertContains(self.client.get("/search/"), "minimal 2 huruf")
        self.assertEqual(self.client.get("/search/", {"q": "x" * 500}).status_code, 200)

    def test_deleted_employees_not_found_and_requires_login(self):
        self.e.soft_delete(self.hrd, "uji"); self.login("hrd"); self.assertEqual(self.titles("eka")[0], [])
        self.client.logout(); self.assertEqual(self.client.get("/search/", {"q": "ani"}).status_code, 302)

    def test_see_all_link_when_more_than_limit(self):
        for i in range(10): Employee.objects.create(nik=f"Z{i:03d}", name=f"Zeta {i}", gender="L", department=self.d1, join_date=ago(1))
        self.login("hrd"); r = self.client.get("/search/", {"q": "zeta"}); s = r.context["sections"][0]
        self.assertEqual((s["total"], len(s["items"])), (10, 8)); self.assertContains(r, "/employees/?q=zeta")


class ListPageRenderTests(ListFilterBase):
    def test_hrd_gets_all_filter_controls(self):
        self.login("hrd"); r = self.client.get("/employees/")
        for k in ("gender", "education", "level", "contract_kind", "tenure", "age_min", "age_max", "join_from", "join_to", "supervisor", "problem", "size", "saved", "colmenu", "print"):
            self.assertContains(r, f'id="{k}"')
        self.assertContains(r, ">S1</option>"); self.assertContains(r, "Grade 2")

    def test_other_roles_do_not_get_hrd_controls_or_values(self):
        for name in ("adm", "poli"):
            self.login(name); r = self.client.get("/employees/")
            for k in ("gender", "education", "tenure", "age_min", "problem", "supervisor"): self.assertNotContains(r, f'id="{k}"')
            self.assertNotContains(r, ">S1</option>"); self.assertContains(r, 'id="size"'); self.assertContains(r, 'id="colmenu"')
