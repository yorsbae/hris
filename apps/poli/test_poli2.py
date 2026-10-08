"""Putaran 17: rekomendasi (lookup), diagnosa↔obat, riwayat pasien, tambah/kurangi obat di rekam medis, rekap stok harian/bulanan."""
import csv, io, json
from datetime import datetime, time, timedelta
from django.utils import timezone
from apps.core.models import AuditLog
from apps.poli import reports, services
from apps.poli.models import Diagnosis, DiagnosisMedicine, MedicalRecord, Medicine, Prescription, PrescriptionReturn, StockMovement
from apps.poli.test_poli import PoliBase


def js(resp): return json.loads(resp.content)


class LookupTests(PoliBase):
    def test_employee_lookup_scope_and_limits(self):
        self.login("poli"); r = js(self.client.get("/api/lookup/employees/", {"q": "00"}))["results"]
        self.assertEqual({x["nik"] for x in r}, {"001", "002", "003"})  # 009 nonaktif tidak muncul
        self.assertEqual(js(self.client.get("/api/lookup/employees/", {"q": "0"}))["results"], [])  # < 2 karakter: tidak ada hasil (bukan seluruh tabel)
        self.assertEqual({x["nik"] for x in js(self.client.get("/api/lookup/employees/", {"q": "00", "gender": "P"}))["results"]}, {"002", "003"})
        self.assertEqual({x["nik"] for x in js(self.client.get("/api/lookup/employees/", {"q": "00", "exclude": "001"}))["results"]}, {"002", "003"})
        self.login("adm")  # Admin Departemen hanya departemennya (Produksi: 001, 003)
        self.assertEqual({x["nik"] for x in js(self.client.get("/api/lookup/employees/", {"q": "00"}))["results"]}, {"001", "003"})
        self.login("hrd"); self.assertEqual(len(js(self.client.get("/api/lookup/employees/", {"q": "00"}))["results"]), 3)

    def test_employee_lookup_limit_10_and_fields_minimal(self):
        from apps.hr.models import Employee
        from datetime import date
        for i in range(30): Employee.objects.create(nik=f"X{i:03d}", name=f"Orang {i}", gender="L", department=self.d1, join_date=date(2024, 1, 1))
        self.login("hrd"); r = js(self.client.get("/api/lookup/employees/", {"q": "X0"}))["results"]
        self.assertEqual(len(r), 10)
        self.assertLessEqual(set(r[0]), {"nik", "name", "gender", "department", "department_id", "position", "shift", "group"})  # tanpa data sensitif

    def test_lookup_access_and_bad_input(self):
        self.assertEqual(self.client.get("/api/lookup/employees/", {"q": "00"}).status_code, 401)
        self.login("hrd")
        for u in ("/api/lookup/medicines/", "/api/lookup/diagnoses/"): self.assertEqual(self.client.get(u, {"q": "pa"}).status_code, 403, u)  # data poli tertutup
        self.assertEqual(js(self.client.get("/api/lookup/employees/", {"q": "00", "department": "abc"}))["results"].__len__(), 3)  # input sampah diabaikan, bukan 500
        self.assertEqual(js(self.client.get("/api/lookup/employees/", {"q": "00", "department": "99999999999999"}))["results"].__len__(), 3)
        self.assertEqual(self.client.get("/api/lookup/employees/", {"q": "a\x00b"}).status_code, 400)  # NUL ditolak middleware

    def test_medicine_and_diagnosis_lookup(self):
        self.login("poli")
        r = js(self.client.get("/api/lookup/medicines/", {"q": "para"}))["results"]; self.assertEqual([x["code"] for x in r], ["PCT"])
        self.assertEqual(r[0]["stock"], 50)
        m3 = Medicine.objects.create(code="ZZZ", name="Zinc habis", unit="tablet")
        self.assertEqual(len(js(self.client.get("/api/lookup/medicines/", {"q": "zinc"}))["results"]), 1)
        self.assertEqual(js(self.client.get("/api/lookup/medicines/", {"q": "zinc", "available": "1"}))["results"], [])  # stok 0 disaring bila diminta
        services.link_medicine(self.diag, self.med.pk, 3, "3x1")
        d = js(self.client.get("/api/lookup/diagnoses/", {"q": "a09"}))["results"][0]
        self.assertEqual(d["code"], "A09"); self.assertEqual(d["medicines"][0]["name"], "Paracetamol"); self.assertEqual(d["medicines"][0]["qty"], 3)

    def test_widget_present_on_nik_inputs(self):
        self.login("hrd")
        for url in ("/requests/new/", "/hrd/aids/new/", "/hrd/maternity/new/"):
            self.assertContains(self.client.get(url), 'data-lookup="employee"', msg_prefix=url)
        self.assertContains(self.client.get("/hrd/maternity/new/"), 'data-q-gender="P"')
        self.login("poli"); self.assertContains(self.client.get("/poli/records/new/"), 'data-lookup="diagnosis"')


class DiagnosisMedicineTests(PoliBase):
    def test_link_validation_and_unique(self):
        l = services.link_medicine(self.diag, self.med.pk, 2, "3x1")
        self.assertEqual((l.qty, l.position), (2, 0))
        with self.assertRaises(ValueError): services.link_medicine(self.diag, self.med.pk, 1)  # duplikat
        for bad in (0, -1, "2", True): self.assertRaises(ValueError, services.link_medicine, self.diag, self.med2.pk, bad)
        self.assertRaises(ValueError, services.link_medicine, self.diag, 999999, 1)
        for i in range(9): services.link_medicine(self.diag, Medicine.objects.create(code=f"M{i}", name=f"Obat {i}", unit="tab").pk, 1)
        with self.assertRaises(ValueError): services.link_medicine(self.diag, Medicine.objects.create(code="M99", name="Obat 99", unit="tab").pk, 1)  # maks 10

    def test_ui_link_unlink_and_audit(self):
        self.login("poli")
        r = self.client.post(f"/poli/diagnoses/{self.diag.pk}/medicines/", {"medicine": self.med.pk, "qty": 2, "dosage": "3x1"}); self.assertEqual(r.status_code, 302)
        self.assertEqual(DiagnosisMedicine.objects.get().qty, 2)
        self.assertTrue(AuditLog.objects.filter(action="diagnosis_link_medicine").exists())
        self.assertContains(self.client.get(f"/poli/diagnoses/{self.diag.pk}/medicines/"), "Paracetamol")
        self.assertContains(self.client.get("/poli/diagnoses/"), "1 obat")
        r = self.client.post(f"/poli/diagnoses/{self.diag.pk}/medicines/", {"medicine": "", "qty": 1}); self.assertEqual(r.status_code, 200)  # obat belum dipilih → galat, bukan 500
        l = DiagnosisMedicine.objects.get()
        self.assertEqual(self.client.post(f"/poli/diagnoses/{self.diag.pk}/medicines/{l.pk}/unlink/").status_code, 302)
        self.assertFalse(DiagnosisMedicine.objects.exists())
        self.assertEqual(self.client.post(f"/poli/diagnoses/{self.diag.pk}/medicines/{l.pk}/unlink/").status_code, 404)

    def test_rbac(self):
        for u in ("hrd", "adm"):
            self.login(u); self.assertEqual(self.client.get(f"/poli/diagnoses/{self.diag.pk}/medicines/").status_code, 403)
            self.assertEqual(self.client.post(f"/poli/diagnoses/{self.diag.pk}/medicines/", {"medicine": self.med.pk, "qty": 1}).status_code, 403)
        self.assertFalse(DiagnosisMedicine.objects.exists())


class PatientSummaryTests(PoliBase):
    def test_summary_and_audit(self):
        self.create(); self.create(); self.login("poli")
        j = js(self.client.get("/poli/api/patient/", {"nik": "001"}))
        self.assertTrue(j["found"]); self.assertEqual((j["name"], j["total"], len(j["visits"])), ("Budi", 2, 2))
        self.assertTrue(AuditLog.objects.filter(action="view_history", user=self.poli).exists())
        self.assertEqual(self.client.get("/poli/api/patient/", {"nik": "009"}).status_code, 404)  # nonaktif
        self.assertEqual(self.client.get("/poli/api/patient/", {"nik": "nope"}).status_code, 404)

    def test_summary_closed_to_non_poli(self):
        for u in ("hrd", "adm"): self.login(u); self.assertEqual(self.client.get("/poli/api/patient/", {"nik": "001"}).status_code, 403)

    def test_form_renders_history_panel_and_rows(self):
        self.login("poli"); h = self.client.get("/poli/records/new/").content.decode()
        for needle in ('id="pt"', 'rx-empty', 'data-lookup="medicine"', '/poli/api/patient/'): self.assertIn(needle, h)
        self.assertNotIn("<option", h.split("Resep obat")[1].split("</form>")[0].replace('<option value="">', ""))  # tidak ada dropdown seluruh obat


class AddReturnTests(PoliBase):
    def test_add_prescription_reduces_stock_and_marks_added(self):
        r = self.create(rx=[(self.med.pk, 5, "3x1")])
        p, m = services.add_prescription(self.poli, r, self.med2.pk, 4, "2x1")
        self.assertEqual(m.stock, 6); self.assertIsNotNone(p.added_at); self.assertEqual(p.added_by, self.poli)
        mv = StockMovement.objects.filter(medicine=self.med2).first(); self.assertEqual((mv.qty, mv.reason, mv.ref), (-4, "prescription", f"MR{r.pk}"))
        self.assertEqual(Prescription.objects.filter(record=r).count(), 2)
        self.assertEqual(MedicalRecord.objects.get(pk=r.pk).complaint, r.complaint)  # rekam medis tidak berubah

    def test_add_invalid_and_insufficient(self):
        r = self.create()
        for bad in (0, -3, "2", True, None): self.assertRaises(ValueError, services.add_prescription, self.poli, r, self.med.pk, bad)
        self.assertRaises(ValueError, services.add_prescription, self.poli, r, self.med2.pk, 11)  # stok 10
        self.assertRaises(ValueError, services.add_prescription, self.poli, r, 999999, 1)
        self.assertEqual(Medicine.objects.get(pk=self.med2.pk).stock, 10); self.assertFalse(Prescription.objects.filter(record=r).exists())  # tidak ada baris setengah jadi

    def test_return_restores_stock_with_card_and_limits(self):
        r = self.create(rx=[(self.med.pk, 10, "")]); p = r.prescriptions.get()
        ret, m = services.return_prescription(self.poli, p.pk, 4, "Pasien alergi")
        self.assertEqual(m.stock, 44)
        mv = StockMovement.objects.filter(medicine=self.med).first(); self.assertEqual((mv.qty, mv.reason, mv.note), (4, "return", "Pasien alergi"))
        with self.assertRaises(ValueError): services.return_prescription(self.poli, p.pk, 7, "x")  # sisa 6
        services.return_prescription(self.poli, p.pk, 6, "sisanya")
        with self.assertRaises(ValueError): services.return_prescription(self.poli, p.pk, 1, "x")  # habis
        self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 50)
        for bad in (0, -1, "1", True): self.assertRaises(ValueError, services.return_prescription, self.poli, p.pk, bad, "x")
        self.assertRaises(ValueError, services.return_prescription, self.poli, p.pk, 1, "  ")  # alasan wajib
        self.assertRaises(ValueError, services.return_prescription, self.poli, 999999, 1, "x")

    def test_return_append_only(self):
        r = self.create(rx=[(self.med.pk, 3, "")]); ret, _ = services.return_prescription(self.poli, r.prescriptions.get().pk, 1, "x")
        ret.qty = 2
        with self.assertRaises(PermissionError): ret.save()
        with self.assertRaises(PermissionError): ret.delete()

    def test_ui_add_return_and_cross_record_id_forged(self):
        self.login("poli"); r = self.create(rx=[(self.med.pk, 5, "")]); other = self.create(emp=self.e3, rx=[(self.med2.pk, 2, "")])
        resp = self.client.post(f"/poli/records/{r.pk}/rx/add/", {"medicine": self.med2.pk, "qty": 3, "dosage": "1x1"}, follow=True)
        self.assertIn("Obat ditambahkan", " ".join(self.msgs(resp))); self.assertEqual(Medicine.objects.get(pk=self.med2.pk).stock, 5)
        self.assertContains(resp, "tambahan ·")
        p = r.prescriptions.filter(medicine=self.med).get()
        resp = self.client.post(f"/poli/records/{r.pk}/rx/return/", {"prescription": p.pk, "qty": 2, "reason": "Tidak jadi"}, follow=True)
        self.assertIn("Obat dikurangi", " ".join(self.msgs(resp))); self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 47)
        foreign = other.prescriptions.get()  # ID baris resep rekam medis LAIN dipalsukan → 404, stok tidak berubah
        self.assertEqual(self.client.post(f"/poli/records/{r.pk}/rx/return/", {"prescription": foreign.pk, "qty": 1, "reason": "x"}).status_code, 404)
        self.assertFalse(PrescriptionReturn.objects.filter(prescription=foreign).exists())
        a = AuditLog.objects.filter(action="rx_return").first(); self.assertNotIn("Tidak jadi", json.dumps(a.after))  # alasan tidak masuk audit
        self.assertEqual(AuditLog.objects.filter(action__in=("rx_add", "rx_return")).count(), 2)
        d = self.client.get(f"/poli/records/{r.pk}/").content.decode(); self.assertIn("Bersih", d)

    def test_ui_errors_not_500(self):
        self.login("poli"); r = self.create()
        for data in ({}, {"medicine": "abc", "qty": 1}, {"medicine": self.med.pk, "qty": "x"}, {"medicine": self.med.pk, "qty": 999999}):
            self.assertEqual(self.client.post(f"/poli/records/{r.pk}/rx/add/", data).status_code, 302)
        for data in ({}, {"prescription": "x", "qty": 1, "reason": "a"}, {"prescription": 99999, "qty": 1, "reason": "a"}):
            self.assertIn(self.client.post(f"/poli/records/{r.pk}/rx/return/", data).status_code, (302, 404))

    def test_rbac(self):
        r = self.create(rx=[(self.med.pk, 5, "")]); p = r.prescriptions.get()
        for u in ("hrd", "adm"):
            self.login(u)
            self.assertEqual(self.client.post(f"/poli/records/{r.pk}/rx/add/", {"medicine": self.med.pk, "qty": 1}).status_code, 403)
            self.assertEqual(self.client.post(f"/poli/records/{r.pk}/rx/return/", {"prescription": p.pk, "qty": 1, "reason": "x"}).status_code, 403)
        self.login("poli"); self.assertEqual(self.client.get(f"/poli/records/{r.pk}/rx/add/").status_code, 405)
        self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 45)

    def test_concurrent_safe_serial_returns_never_exceed(self):
        r = self.create(rx=[(self.med.pk, 5, "")]); p = r.prescriptions.get(); ok = 0
        for _ in range(8):
            try: services.return_prescription(self.poli, p.pk, 2, "x"); ok += 1
            except ValueError: pass
        self.assertEqual(ok, 2); self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 49)  # 45 + 2 + 2 (sisa 1 tidak bisa 2)


class StockReportTests(PoliBase):
    def backdate(self, med, days_ago, qty_filter=None):
        pass

    def test_formula_and_opening_closing_consistency(self):
        today = timezone.localdate()
        # kartu stok setUp (masuk 50 & 10) terjadi "sekarang" → mundurkan ke 2 hari lalu
        StockMovement.objects.update(created_at=timezone.make_aware(datetime.combine(today - timedelta(days=2), time(9, 0))))
        r = self.create(rx=[(self.med.pk, 8, "")])            # keluar 8 hari ini
        services.return_prescription(self.poli, r.prescriptions.get().pk, 3, "x")  # retur 3
        services.stock_in(self.med.pk, 20, self.poli)          # masuk 20
        services.stock_adjust(self.med.pk, -2, self.poli, "rusak")
        rep = reports.stock_report("day", today); row = [x for x in rep["rows"] if x["code"] == "PCT"][0]
        self.assertEqual((row["opening"], row["masuk"], row["retur"], row["keluar"], row["adj"], row["closing"]), (50, 20, 3, 8, -2, 63))
        self.assertEqual(row["closing"], Medicine.objects.get(code="PCT").stock)
        old = [x for x in reports.stock_report("day", today - timedelta(days=1))["rows"] if x["code"] == "PCT"][0]  # kemarin: tidak ada gerakan
        self.assertEqual((old["opening"], old["closing"], old["masuk"]), (50, 50, 0))
        d2 = [x for x in reports.stock_report("day", today - timedelta(days=2))["rows"] if x["code"] == "PCT"][0]
        self.assertEqual((d2["opening"], d2["masuk"], d2["closing"]), (0, 50, 50))

    def test_every_row_closing_equals_card_balance(self):
        today = timezone.localdate(); self.create(rx=[(self.med.pk, 5, ""), (self.med2.pk, 4, "")]); services.stock_adjust(self.med2.pk, 3, self.poli, "opname")
        for x in reports.stock_report("month", today, show_all=True)["rows"]:
            last = StockMovement.objects.filter(medicine_id=x["id"]).order_by("-id").first()  # kartu TERBARU
            self.assertEqual(x["closing"], last.balance_after if last else Medicine.objects.get(pk=x["id"]).stock, x["code"])

    def test_legacy_medicine_without_opening_card(self):
        m = Medicine.objects.create(code="OLD", name="Obat lama", unit="tab", stock=40)  # stok ada tanpa kartu penerimaan awal
        rep = {x["code"]: x for x in reports.stock_report("day", timezone.localdate(), show_all=True)["rows"]}["OLD"]
        self.assertEqual((rep["opening"], rep["closing"]), (40, 40))
        services.stock_in(m.pk, 10, self.poli)
        rep = {x["code"]: x for x in reports.stock_report("day", timezone.localdate())["rows"]}["OLD"]
        self.assertEqual((rep["opening"], rep["masuk"], rep["closing"]), (40, 10, 50))

    def test_month_and_hide_idle(self):
        today = timezone.localdate(); Medicine.objects.create(code="IDLE", name="Nganggur", unit="tab")
        codes = {x["code"] for x in reports.stock_report("month", today)["rows"]}; self.assertNotIn("IDLE", codes); self.assertIn("PCT", codes)
        self.assertIn("IDLE", {x["code"] for x in reports.stock_report("month", today, show_all=True)["rows"]})
        self.assertEqual(reports.stock_report("month", today.replace(day=15))["start"], today.replace(day=1))
        self.assertEqual({x["code"] for x in reports.stock_report("day", today, q="amox")["rows"]}, {"AMX"})

    def test_page_csv_rbac_and_bad_input(self):
        self.login("poli"); today = timezone.localdate()
        self.assertContains(self.client.get("/poli/reports/stock/"), "Rekap stok obat")
        self.assertContains(self.client.get("/poli/reports/stock/", {"period": "month", "month": today.strftime("%Y-%m")}), "bulanan")
        for bad in ({"date": "xx"}, {"period": "month", "month": "2026-13"}, {"date": "9999-01-01"}, {"period": "month", "month": "zz"}, {"date": "1900-01-01"}):
            self.assertEqual(self.client.get("/poli/reports/stock/", bad).status_code, 200, bad)
        r = self.client.get("/poli/reports/stock/", {"date": today.isoformat(), "format": "csv"})
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8-sig"); rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0][3], "Stok awal"); self.assertEqual(rows[-1][1], "TOTAL")
        self.assertTrue(AuditLog.objects.filter(action="stock_report_export").exists())
        for u in ("hrd", "adm"): self.login(u); self.assertEqual(self.client.get("/poli/reports/stock/").status_code, 403)

    def test_csv_formula_injection_neutralised(self):
        Medicine.objects.create(code="=1+1", name="@SUM(A1)", unit="-x", stock=0)
        self.login("poli"); r = self.client.get("/poli/reports/stock/", {"all": "1", "format": "csv"}); t = r.content.decode("utf-8-sig")
        self.assertIn("'=1+1", t); self.assertIn("'@SUM(A1)", t)

    def test_constant_query_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.login("poli")
        with CaptureQueriesContext(connection) as a: self.client.get("/poli/reports/stock/", {"all": "1"})
        for i in range(60): Medicine.objects.create(code=f"Q{i}", name=f"Obat Q{i}", unit="tab")
        with CaptureQueriesContext(connection) as b: self.client.get("/poli/reports/stock/", {"all": "1"})
        self.assertEqual(len(a), len(b))  # agregasi di database: tidak tumbuh dengan jumlah obat
