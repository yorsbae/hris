"""Putaran 50: filter Shift = A7 / A7 Pack / GS; Urutkan & Kelompokkan (server untuk Karyawan, komponen bersama di base.html);
foto/berkas bukti opsional di pengajuan Izin & Cuti (ekstensi bebas, disajikan aman, ber-scope, diaudit)."""
import shutil, tempfile
from datetime import date, time
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from apps.core.models import AuditLog, Role, User
from .models import ChangeRequest, Department, Employee, LeaveLedger, Position, RequestAttachment, Shift, ShiftGroup

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
MEDIA = tempfile.mkdtemp(prefix="hris_media_")


def tearDownModule(): shutil.rmtree(MEDIA, ignore_errors=True)


class ShiftFilterSortGroupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.prd, cls.gdg = Department.objects.create(code="PRD", name="Produksi"), Department.objects.create(code="GDG", name="Gudang")
        pos = Position.objects.create(name="Staff")
        gs = Shift.objects.create(name="General Shift 16", code="GS-16", start=time(8), end=time(16), is_gs=True)
        g2, g3 = ShiftGroup.objects.create(code="A", pattern=ShiftGroup.P2), ShiftGroup.objects.create(code="A_pack", pattern=ShiftGroup.P3)
        mk = lambda nik, name, d, **kw: Employee.objects.create(nik=nik, name=name, gender="L", department=d, position=pos, join_date=kw.pop("jd", date(2024, 1, 1)), **kw)
        cls.a7, cls.pack, cls.gs, cls.none = (mk("E1", "Andi", cls.prd, shift_group=g2, jd=date(2022, 5, 1)), mk("E2", "Budi", cls.gdg, shift_group=g3, jd=date(2021, 5, 1)),
                                               mk("E3", "Citra", cls.prd, shift=gs, jd=date(2023, 5, 1)), mk("E4", "Dewi", cls.gdg, jd=date(2020, 5, 1)))
        cls.hrd = User.objects.create_user("hrd", password="x", role=Role.HRD)
        cls.adm = User.objects.create_user("adm", password="x", role=Role.DEPT_ADMIN, department=cls.prd)

    def names(self, qs="", user=None):
        self.client.force_login(user or self.hrd)
        r = self.client.get("/api/employees/?" + qs); self.assertEqual(r.status_code, 200)
        return [x["name"] for x in r.json()["results"]]

    def test_shift_filter_a7_a7pack_gs(self):
        self.assertEqual(self.names("shift=a7"), ["Andi"])
        self.assertEqual(self.names("shift=a7pack"), ["Budi"])
        self.assertEqual(self.names("shift=gs"), ["Citra"])
        self.assertEqual(self.names("shift="), ["Andi", "Budi", "Citra", "Dewi"])
        self.assertEqual(self.names("shift=ngawur"), ["Andi", "Budi", "Citra", "Dewi"])  # nilai tak dikenal diabaikan (bukan 500)

    def test_old_numeric_shift_link_still_works(self):
        self.assertEqual(self.names(f"shift={self.gs.shift_id}"), ["Citra"])

    def test_page_offers_only_the_three_shift_options(self):
        self.client.force_login(self.hrd); h = self.client.get("/employees/").content.decode()
        for o in ('value="a7">A7<', 'value="a7pack">A7 Pack<', 'value="gs">GS<'): self.assertIn(o, h)
        self.assertNotIn("General Shift 12", h); self.assertNotIn("Shift Malam", h)

    def test_shift_filter_respects_department_scope(self):
        self.assertEqual(self.names("shift=a7pack", self.adm), [])  # Budi di Gudang: di luar scope Admin Dept Produksi
        self.assertEqual(self.names("shift=gs", self.adm), ["Citra"])

    def test_sort_whole_dataset(self):
        self.assertEqual(self.names("sort=-name"), ["Dewi", "Citra", "Budi", "Andi"])
        self.assertEqual(self.names("sort=join_date"), ["Dewi", "Budi", "Andi", "Citra"])
        self.assertEqual(self.names("sort=-nik"), ["Dewi", "Citra", "Budi", "Andi"])

    def test_sort_invalid_or_forbidden_is_ignored(self):
        self.assertEqual(self.names("sort=password"), ["Andi", "Budi", "Citra", "Dewi"])
        self.assertEqual(self.names("sort=name;drop"), ["Andi", "Budi", "Citra", "Dewi"])
        self.assertEqual(self.names("sort=join_date", self.adm), ["Andi", "Citra"])  # tanggal masuk bukan kolom Admin Dept → urutan nama

    def test_group_by_shift_and_department(self):
        self.client.force_login(self.hrd)
        rows = self.client.get("/api/employees/?group=shift").json()["results"]
        self.assertEqual([(r["name"], r["grp"]) for r in rows], [("Andi", "A7"), ("Budi", "A7 Pack"), ("Citra", "GS"), ("Dewi", "Tanpa kelompok/GS")])
        rows = self.client.get("/api/employees/?group=department&sort=-name").json()["results"]
        self.assertEqual([(r["name"], r["grp"]) for r in rows], [("Dewi", "Gudang"), ("Budi", "Gudang"), ("Citra", "Produksi"), ("Andi", "Produksi")])
        self.assertNotIn("grp", self.client.get("/api/employees/").json()["results"][0])

    def test_group_unknown_ignored(self):
        self.client.force_login(self.hrd); rows = self.client.get("/api/employees/?group=nik_ktp").json()["results"]
        self.assertEqual(len(rows), 4); self.assertNotIn("grp", rows[0])

    def test_export_follows_sort(self):
        self.client.force_login(self.hrd); r = self.client.get("/api/employees/export/?format=csv&sort=-name&shift=a7")
        self.assertEqual(r.status_code, 200); body = r.content.decode(); self.assertIn("Andi", body); self.assertNotIn("Budi", body)

    def test_shared_sort_group_component_is_on_every_page(self):
        self.client.force_login(self.hrd); h = self.client.get("/requests/").content.decode()
        for needle in ("sgInject", "sgApply", "tr.grow"): self.assertIn(needle, h)
        # halaman Karyawan mengurus sendiri di server → komponen bersama tidak menyuntik ganda
        e = self.client.get("/employees/").content.decode()
        self.assertIn('data-sg="server"', e); self.assertIn('id="sort"', e); self.assertIn('id="group"', e)


@override_settings(MEDIA_ROOT=MEDIA)
class RequestPhotoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.prd, cls.gdg = Department.objects.create(code="PRD", name="Produksi"), Department.objects.create(code="GDG", name="Gudang")
        pos = Position.objects.create(name="Staff")
        mk = lambda nik, d: Employee.objects.create(nik=nik, name=f"Emp {nik}", gender="L", department=d, position=pos, join_date=date(2024, 1, 1))
        cls.e1, cls.e2 = mk("P01", cls.prd), mk("G01", cls.gdg)
        for e in (cls.e1, cls.e2): LeaveLedger.objects.create(employee=e, year=2026, kind="grant", days=12)
        cls.hrd = User.objects.create_user("hrd", password="x", role=Role.HRD)
        cls.adm = User.objects.create_user("admprd", password="x", role=Role.DEPT_ADMIN, department=cls.prd)
        cls.other = User.objects.create_user("admgdg", password="x", role=Role.DEPT_ADMIN, department=cls.gdg)

    def sick(self, files=(), nik="P01", typ="sakit", submit=False, start="2026-11-02", end="2026-11-03"):
        d = {"type": typ, "nik": nik, "start_date": start, "end_date": end, "reason": "Demam"}
        if submit: d["submit"] = "1"
        if files: d["photos"] = list(files)
        return self.client.post("/requests/new/", d)

    def test_form_has_optional_photo_field_and_multipart(self):
        self.client.force_login(self.adm); h = self.client.get("/requests/new/").content.decode()
        self.assertIn('enctype="multipart/form-data"', h); self.assertIn('name="photos"', h); self.assertIn("multiple", h)

    def test_request_without_photo_still_works(self):
        self.client.force_login(self.adm); self.sick(submit=True)
        r = ChangeRequest.objects.get(); self.assertEqual((r.status, r.attachments.count()), ("pending", 0))

    def test_photo_saved_any_extension_and_size_recorded(self):
        self.client.force_login(self.adm)
        self.sick([SimpleUploadedFile("surat dokter.PNG", PNG, "image/png"), SimpleUploadedFile("scan.xyz", b"data-bebas", "application/x-foo")], submit=True)
        r = ChangeRequest.objects.get(); a = list(r.attachments.all()); self.assertEqual(len(a), 2)
        self.assertEqual((a[0].original_name, a[0].content_type, a[0].is_image, a[0].size), ("surat_dokter.PNG", "image/png", True, len(PNG)))
        self.assertEqual((a[1].original_name, a[1].is_image), ("scan.xyz", False))
        self.assertNotIn("surat", a[0].file.name); self.assertTrue(AuditLog.objects.filter(action="request_attach").exists())

    def test_image_inline_nonimage_download_and_svg_never_inline(self):
        self.client.force_login(self.adm)
        self.sick([SimpleUploadedFile("a.png", PNG), SimpleUploadedFile("x.svg", b"<svg onload=alert(1)/>"), SimpleUploadedFile("evil.png", b"<html><script>1</script>")], submit=True)
        r = ChangeRequest.objects.get(); png, svg, fake = r.attachments.all()
        resp = self.client.get(f"/requests/{r.pk}/foto/{png.pk}/"); self.assertEqual(resp["Content-Type"], "image/png"); self.assertNotIn("attachment", resp.get("Content-Disposition", ""))
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff"); self.assertIn("sandbox", resp["Content-Security-Policy"])
        for a in (svg, fake):  # SVG & "png" palsu (isi HTML) dikenali dari isi → selalu diunduh, tidak ditampilkan
            resp = self.client.get(f"/requests/{r.pk}/foto/{a.pk}/"); self.assertIn("attachment", resp["Content-Disposition"]); self.assertEqual(resp["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", self.client.get(f"/requests/{r.pk}/foto/{png.pk}/?dl=1")["Content-Disposition"])
        self.assertTrue(AuditLog.objects.filter(action="request_attachment_view").exists())

    def test_scope_other_department_and_anonymous(self):
        self.client.force_login(self.adm); self.sick([SimpleUploadedFile("a.png", PNG)], submit=True)
        r = ChangeRequest.objects.get(); a = r.attachments.get(); url = f"/requests/{r.pk}/foto/{a.pk}/"
        self.client.force_login(self.other); self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(f"/requests/{r.pk}/foto/", {"photos": SimpleUploadedFile("b.png", PNG)}).status_code, 404)
        self.client.force_login(self.hrd); self.assertEqual(self.client.get(url).status_code, 200)
        self.client.logout(); self.assertIn(self.client.get(url).status_code, (302, 401, 403))

    def test_attachment_id_of_other_request_is_404(self):
        self.client.force_login(self.adm); self.sick([SimpleUploadedFile("a.png", PNG)], submit=True)
        self.sick(submit=True, start="2026-12-07", end="2026-12-08")
        r1, r2 = ChangeRequest.objects.order_by("id"); a = r1.attachments.get()
        self.assertEqual(self.client.get(f"/requests/{r2.pk}/foto/{a.pk}/").status_code, 404)

    def test_size_and_count_limits(self):
        self.client.force_login(self.adm)
        with override_settings(REQUEST_ATTACH_MAX_MB=0):
            self.sick([SimpleUploadedFile("a.png", PNG)], submit=True)
        self.assertEqual(ChangeRequest.objects.count(), 0)  # galat menahan pembuatan pengajuan
        with override_settings(REQUEST_ATTACH_MAX_FILES=1):
            resp = self.sick([SimpleUploadedFile("a.png", PNG), SimpleUploadedFile("b.png", PNG)], submit=True)
        self.assertEqual(ChangeRequest.objects.count(), 0); self.assertContains(resp, "Maksimal 1 berkas")
        resp = self.sick([SimpleUploadedFile("kosong.png", b"")], submit=True); self.assertContains(resp, "kosong"); self.assertEqual(ChangeRequest.objects.count(), 0)

    def test_files_ignored_for_types_that_need_no_proof(self):
        self.client.force_login(self.hrd)
        d = Department.objects.create(code="X", name="Lain")
        self.client.post("/requests/new/", {"type": "mutasi_dept", "nik": "P01", "department": d.pk, "effective_date": "2026-12-01", "reason": "Penataan", "submit": "1", "photos": SimpleUploadedFile("a.png", PNG)})
        self.assertEqual((ChangeRequest.objects.count(), RequestAttachment.objects.count()), (1, 0))

    def test_add_photo_on_detail_only_while_open(self):
        self.client.force_login(self.adm); self.sick(submit=True); r = ChangeRequest.objects.get()
        self.assertContains(self.client.get(f"/requests/{r.pk}/"), "Tambah bukti")
        self.client.post(f"/requests/{r.pk}/foto/", {"photos": SimpleUploadedFile("b.png", PNG)}); self.assertEqual(r.attachments.count(), 1)
        self.client.force_login(self.hrd); self.client.post(f"/requests/{r.pk}/approved/")
        self.client.force_login(self.adm)
        self.client.post(f"/requests/{r.pk}/foto/", {"photos": SimpleUploadedFile("c.png", PNG)}); self.assertEqual(r.attachments.count(), 1)  # sudah diputuskan → terkunci
        self.assertNotContains(self.client.get(f"/requests/{r.pk}/"), "Tambah bukti")

    def test_other_dept_user_without_ownership_cannot_add(self):
        self.client.force_login(self.adm); self.sick(submit=True); r = ChangeRequest.objects.get()
        colleague = User.objects.create_user("adm2", password="x", role=Role.DEPT_ADMIN, department=self.prd); self.client.force_login(colleague)
        self.client.post(f"/requests/{r.pk}/foto/", {"photos": SimpleUploadedFile("b.png", PNG)}); self.assertEqual(r.attachments.count(), 0)
