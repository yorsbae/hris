"""Putaran 62: impor format asli Fingerspot (.xls BIFF mentah & XML GRID), Data Scanlog + ekspor, Shift Harian, Kontrol Harian, Master Mesin."""
import os, re
from datetime import timedelta
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from apps.core.models import AuditLog
from apps.hr import attendance_calc as calc, attendance_import as imp
from apps.hr.models import AttendanceDaily, AttendanceDevice, AttendanceImport, AttendanceRaw
from apps.hr.test_attendance import Base, at, workday

DATA = os.path.join(os.path.dirname(__file__), "testdata")


def fixture(name): return open(os.path.join(DATA, name), "rb").read()


class FingerspotFormatTests(Base):
    """Berkas contoh asli dari mesin/aplikasi Fingerspot Personnel (A = ekspor XML, B = ekspor 'XLS')."""
    def _taps(self, name):
        g = imp.read_grid(fixture(name), name); r = imp.extract(g, imp.detect(g)); self.assertEqual(r["errors"], []); return r["taps"]

    def test_raw_biff_xls_is_read(self):
        t = self._taps("fingerspot_scanlog.xls"); self.assertEqual(len(t), 3)
        self.assertEqual(t[0][0], "1"); self.assertEqual(t[0][2].strftime("%d-%m-%Y %H:%M:%S"), "09-10-2026 20:16:30"); self.assertEqual(t[0][3], "Mesin 1")

    def test_xml_grid_is_read_and_equals_xls(self):
        self.assertEqual(self._taps("fingerspot_scanlog.xml"), self._taps("fingerspot_scanlog.xls"))

    def test_xml_with_entity_declaration_is_not_expanded(self):
        bad = b'<?xml version="1.0"?><!DOCTYPE GRID [<!ENTITY x "AAAA">]><GRID><COLUMNS><COLUMN Name="a" Caption="PIN"/></COLUMNS><ROWS><ROW a="&x;"/></ROWS></GRID>'
        with self.assertRaises(ValueError): imp.read_grid(bad, "x.xml")

    def test_web_upload_of_both_real_files_commits(self):
        self.login("hrd")
        for n in ("fingerspot_scanlog.xls", "fingerspot_scanlog.xml"):
            r = self.client.post(reverse("att_import"), {"file": SimpleUploadedFile(n, fixture(n))}); self.assertEqual(r.status_code, 302, n)
            i = AttendanceImport.objects.latest("id"); r = self.client.post(reverse("att_import_detail", args=[i.pk]), {"action": "commit"}, follow=True); self.assertContains(r, "Impor selesai")
        self.assertEqual(AttendanceRaw.objects.count(), 3)            # berkas kedua = duplikat, tidak menggandakan
        self.assertEqual(set(AttendanceRaw.objects.values_list("device_id", flat=True)), {"Mesin 1"})


class ScanlogTests(Base):
    def setUp(self):
        self.d = workday(); self.scan(self.e1, self.d, (7, 5), (15, 0)); self.scan(self.e2, self.d, (8, 0))
        AttendanceRaw.objects.create(employee=None, pin="999", device_id="FP2", punched_at=at(self.d, 9, 0))

    def get(self, **kw): self.login("hrd"); return self.client.get(reverse("att_scanlog"), {"from": self.d, "to": self.d, **kw})

    def test_lists_rows_and_unmapped(self):
        r = self.get(); self.assertEqual(r.status_code, 200); self.assertEqual(r.context["page"].paginator.count, 4); self.assertContains(r, "PIN belum dipetakan")

    def test_filters(self):
        self.assertEqual(self.get(dept=self.e1.department_id).context["page"].paginator.count, 2)
        self.assertEqual(self.get(device="FP2").context["page"].paginator.count, 1)
        self.assertEqual(self.get(unmapped="1").context["page"].paginator.count, 1)
        self.assertEqual(self.get(q=self.e2.nik).context["page"].paginator.count, 1)
        self.assertEqual(self.get(q="999").context["page"].paginator.count, 1)

    def test_only_hrd_can_open(self):
        for who, code in (("adm", 403), ("poli", 403)):
            self.login(who); self.assertEqual(self.client.get(reverse("att_scanlog")).status_code, code, who)
        self.client.logout(); self.assertIn(self.client.get(reverse("att_scanlog")).status_code, (302, 401))

    def test_range_is_capped(self):
        self.login("hrd"); r = self.client.get(reverse("att_scanlog"), {"from": self.d - timedelta(days=200), "to": self.d}); self.assertEqual((r.context["d2"] - r.context["d1"]).days, 30)

    def test_csv_export_has_exact_fingerspot_columns_and_is_audited(self):
        r = self.get(export=1); self.assertEqual(r.status_code, 200); head = r.content.decode("utf-8-sig").splitlines()[0]
        self.assertEqual(head, "Tanggal scan,Tanggal,Jam,PIN,NIP,Nama,Jabatan,Departemen,Kantor,Verifikasi,I/O,Workcode,SN,Mesin")
        self.assertEqual(len(r.content.decode("utf-8-sig").splitlines()), 5)
        self.assertTrue(AuditLog.objects.filter(action="attendance_scanlog_export").exists())

    def test_xml_export_round_trips_through_importer_without_duplicates(self):
        r = self.get(export=1, format="xml"); self.assertEqual(r.status_code, 200); self.assertIn("application/xml", r["Content-Type"])
        n = AttendanceRaw.objects.count(); g = imp.read_grid(r.content, "scanlog.xml"); res = imp.extract(g, imp.detect(g))
        self.assertEqual(len(res["taps"]), n)
        keys = {(t[0], t[3], t[2]) for t in res["taps"]}; have = {(a.pin, a.device_id, a.punched_at.astimezone().replace(tzinfo=None, microsecond=0)) for a in AttendanceRaw.objects.all()}
        self.assertEqual(keys, have)

    def test_export_over_limit_is_refused_not_truncated(self):
        from unittest import mock
        from apps.hr import attendance_reports as ar
        with mock.patch.dict(ar.EXPORT_LIMIT, {"csv": 2}):
            r = self.get(export=1, format="csv"); self.assertEqual(r.status_code, 302); self.assertNotIn("export", r["Location"])
            r = self.client.get(r["Location"]); self.assertContains(r, "melebihi batas ekspor")
        self.assertFalse(AuditLog.objects.filter(action="attendance_scanlog_export").exists())

    def test_xlsx_export(self):
        r = self.get(export=1, format="xlsx"); self.assertEqual(r.content[:2], b"PK")

    def test_formula_like_name_is_neutralised_in_export(self):
        self.e1.name = "=HYPERLINK(\"x\")"; self.e1.save(); r = self.get(export=1)
        self.assertNotIn(',=HYPERLINK', r.content.decode("utf-8-sig"))


class ShiftGridTests(Base):
    def setUp(self):
        self.d = workday(); self.d0 = self.d - timedelta(days=1)
        self.e2.shift = self.pagi; self.e2.save()
        self.scan(self.e1, self.d, (7, 20), (15, 0)); self.scan(self.e3, self.d, (7, 0), (15, 0)); calc.process(self.d, self.d)   # e1 telat 20, e3 tepat, e2 tanpa scan

    def get(self, who="hrd", **kw): self.login(who); return self.client.get(reverse("att_shift"), {"from": self.d, "to": self.d, **kw})

    def cells(self, r): return {g["e"].pk: g["cells"][0] for g in r.context["grid"]}

    def test_cells_show_shift_late_and_absent(self):
        c = self.cells(self.get())
        self.assertEqual(c[self.e1.pk][0:2], ("late", "PAGI +20m")); self.assertEqual(c[self.e3.pk][0:2], ("ok", "PAGI")); self.assertEqual(c[self.e2.pk][0], "absent")

    def test_status_filter_only_people_with_that_status(self):
        self.assertEqual({g["e"].pk for g in self.get(status="absent").context["grid"]}, {self.e2.pk})
        self.assertEqual({g["e"].pk for g in self.get(status="late").context["grid"]}, {self.e1.pk})

    def test_dept_admin_scope_and_dept_filter_ignored(self):
        r = self.get("adm", dept=self.e2.department_id); ids = {g["e"].pk for g in r.context["grid"]}
        self.assertTrue(ids and all(i != self.e2.pk for i in ids))

    def test_dept_filter_for_hrd(self):
        self.assertEqual({g["e"].pk for g in self.get(dept=self.e2.department_id).context["grid"]}, {self.e2.pk})

    def test_people_without_rows_in_range_are_not_listed_and_cells_without_row_are_dashes(self):
        r = self.get(**{"from": self.d0}); c = {g["e"].pk: g["cells"] for g in r.context["grid"]}
        self.assertEqual(c[self.e1.pk][0][0], "none"); self.assertEqual(len(c[self.e1.pk]), 2)

    def test_range_capped_at_31_days(self):
        self.login("hrd"); r = self.client.get(reverse("att_shift"), {"from": self.d - timedelta(days=90), "to": self.d}); self.assertEqual(len(r.context["days"]), 31)

    def test_export_and_poli_forbidden(self):
        r = self.get(export=1); self.assertEqual(r.status_code, 200); self.assertIn("NIK,Nama,Departemen", r.content.decode("utf-8-sig").splitlines()[0])
        self.login("poli"); self.assertEqual(self.client.get(reverse("att_shift")).status_code, 403)


class ControlTests(Base):
    def setUp(self):
        self.d = workday(); self.e2.shift = self.pagi; self.e2.save()
        self.scan(self.e1, self.d, (7, 20), (14, 30)); self.scan(self.e3, self.d, (7, 0)); calc.process(self.d, self.d)

    def get(self, who="hrd", **kw): self.login(who); return self.client.get(reverse("att_control"), {"from": self.d, "to": self.d, **kw})

    def test_totals(self):
        t = self.get().context["tot"]
        self.assertEqual((t["telat"], t["early"], t["absent"], t["incomplete"]), (1, 1, 1, 1))

    def test_links_open_filtered_daily_list(self):
        self.login("hrd")
        for key, expect in (("absent", {self.e2.pk}), ("late", {self.e1.pk}), ("early", {self.e1.pk}), ("incomplete", {self.e3.pk})):
            r = self.client.get(reverse("att_daily"), {"from": self.d, "to": self.d, "status": key}); self.assertEqual({a.employee_id for a in r.context["page"]}, expect, key)

    def test_daily_shift_filter(self):
        self.login("hrd"); r = self.client.get(reverse("att_daily"), {"from": self.d, "to": self.d, "shift": self.malam.pk}); self.assertEqual(len(r.context["page"]), 0)

    def test_dept_admin_sees_only_own_department_rows(self):
        names = {r["department__name"] for r in self.get("adm").context["rows"]}; self.assertEqual(names, {self.e1.department.name})

    def test_percent_and_export(self):
        r = self.get(export=1); self.assertEqual(r.status_code, 200); self.assertIn("departemen,", r.content.decode("utf-8-sig").splitlines()[0])

    def test_idle_machine_warning_only_for_hrd(self):
        AttendanceDevice.objects.create(code="FP-MATI", name="Mesin Gerbang")
        self.assertContains(self.get(), "Mesin Gerbang"); self.assertNotContains(self.get("adm"), "Mesin Gerbang")


class DeviceTests(Base):
    def test_register_unknown_then_edit_and_deactivate(self):
        d = workday(); self.scan(self.e1, d, (7, 0))
        self.login("hrd"); r = self.client.get(reverse("att_devices")); self.assertContains(r, "FP1")
        self.client.post(reverse("att_devices"), {"code": "FP1", "name": "Gerbang Utama", "location": "Pos 1"}); m = AttendanceDevice.objects.get(code="FP1"); self.assertTrue(m.active)
        self.client.post(reverse("att_devices"), {"edit": "1", "code": "FP1", "name": "Gerbang", "serial": "123"}); m.refresh_from_db(); self.assertEqual((m.name, m.serial, m.active), ("Gerbang", "123", False))
        self.assertEqual(AuditLog.objects.filter(module="hr", action__startswith="attendance_device").count(), 2)

    def test_idle_detection(self):
        from django.utils import timezone
        from apps.hr.attendance_reports import device_status
        for code, name, act in (("OLD", "Lama", True), ("FP1", "Hidup", True), ("OFF", "Mati", False)): AttendanceDevice.objects.create(code=code, name=name, active=act)
        AttendanceRaw.objects.create(employee=self.e1, pin="1", device_id="FP1", punched_at=timezone.now())
        self.assertEqual({r["d"].code for r in device_status()[2]}, {"OLD"})      # nonaktif tidak diperingatkan; yang baru scan tidak diam

    def test_roles_and_validation(self):
        for who in ("adm", "poli"): self.login(who); self.assertEqual(self.client.get(reverse("att_devices")).status_code, 403)
        self.login("hrd"); self.client.post(reverse("att_devices"), {"code": "", "name": "x"}); self.assertEqual(AttendanceDevice.objects.count(), 0)
        self.client.post(reverse("att_devices"), {"code": "<b>x</b>", "name": "<script>1</script>"}); r = self.client.get(reverse("att_devices")); self.assertNotContains(r, "<script>1</script>")
