import json
from django.db import IntegrityError, transaction
from apps.core.models import AuditLog, Notification, Role, User
from apps.hrd.test_base import HrdBase, PW
from apps.poli import services
from apps.poli.models import (Diagnosis, MedicalRecord, Medicine, Prescription, RecordAddendum, Referral, SickLeaveLetter, StockMovement, dispense)


class PoliBase(HrdBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.diag = Diagnosis.objects.create(code="A09", name="Diare")
        cls.med = Medicine.objects.create(code="PCT", name="Paracetamol", unit="tablet", min_stock=5)
        cls.med2 = Medicine.objects.create(code="AMX", name="Amoxicillin", unit="kapsul", min_stock=0)
        services.stock_in(cls.med.pk, 50, cls.poli, ref="FKT-1"); services.stock_in(cls.med2.pk, 10, cls.poli)

    def rx_data(self, *lines):
        d = {"rx-TOTAL_FORMS": "5", "rx-INITIAL_FORMS": "0", "rx-MIN_NUM_FORMS": "0", "rx-MAX_NUM_FORMS": "20"}
        for i in range(5):
            m, q, dose = lines[i] if i < len(lines) else ("", "", "")
            d.update({f"rx-{i}-medicine": m, f"rx-{i}-qty": q, f"rx-{i}-dosage": dose})
        return d

    def post_record(self, nik="001", kind="berobat", rx=(), **extra):
        data = {"nik": nik, "kind": kind, "complaint": "Mencret", "diagnosis_code": "A09", **self.rx_data(*rx), **extra}
        return self.client.post("/poli/records/new/", data)

    def create(self, emp=None, **kw):
        kw.setdefault("kind", "berobat")
        return services.create_record(self.poli, emp or self.e1, kw.pop("kind"), "Keluhan", {}, None, "", kw.pop("rx", ()))[0]


class AccessTests(PoliBase):
    URLS = ["/poli/", "/poli/records/", "/poli/records/new/", "/poli/medicines/", "/poli/medicines/new/", "/poli/diagnoses/", "/poli/diagnoses/new/", "/poli/referrals/"]

    def test_poli_and_superadmin_ok(self):
        for u in ("poli", "su"):
            self.login(u)
            for url in self.URLS: self.assertEqual(self.client.get(url).status_code, 200, (u, url))

    def test_hrd_and_dept_admin_forbidden_everywhere(self):
        r = self.create(); ref = services.create_referral(self.poli, r, "RS Umum")
        urls = self.URLS + [f"/poli/records/{r.pk}/", f"/poli/employees/{self.e1.pk}/", f"/poli/medicines/{self.med.pk}/", f"/poli/referrals/{ref.pk}/",
                            f"/poli/referrals/{ref.pk}/letter.pdf", f"/poli/records/{r.pk}/referral/new/"]
        for u in ("hrd", "adm"):
            self.login(u)
            for url in urls: self.assertEqual(self.client.get(url).status_code, 403, (u, url))
            for url in (f"/poli/records/{r.pk}/addendum/", f"/poli/medicines/{self.med.pk}/stock/in/", f"/poli/referrals/{ref.pk}/send/"):
                self.assertEqual(self.client.post(url, {"qty": 5, "note": "x", "outcome": "x"}).status_code, 403, (u, url))
        self.assertEqual(self.med.__class__.objects.get(pk=self.med.pk).stock, 50)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get("/poli/records/"); self.assertEqual(resp.status_code, 302); self.assertIn("/login/", resp["Location"])

    def test_post_only_actions_reject_get(self):
        self.login("poli"); r = self.create()
        self.assertEqual(self.client.get(f"/poli/records/{r.pk}/addendum/").status_code, 405)
        self.assertEqual(self.client.get(f"/poli/medicines/{self.med.pk}/stock/in/").status_code, 405)

    def test_nav_link_only_for_poli_and_superadmin(self):
        for u, shown in (("poli", True), ("su", True), ("hrd", False), ("adm", False)):
            self.login(u); self.assertEqual("/poli/" in self.client.get("/employees/").content.decode(), shown, u)

    def test_hrd_cannot_use_json_api_either(self):
        self.login("hrd"); self.assertEqual(self.client.post("/api/poli/records/", "{}", content_type="application/json").status_code, 403)


class StockTests(PoliBase):
    def test_dispense_rejects_non_positive_and_non_int(self):
        for bad in (0, -5, 2.5, "3", True, None):
            with self.assertRaises(ValueError, msg=repr(bad)): dispense(self.med.pk, bad, self.poli)
        self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 50)

    def test_dispense_unknown_medicine_is_value_error(self):
        with self.assertRaises(ValueError): dispense(999999, 1, self.poli)

    def test_negative_stock_blocked_at_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic(): Medicine.objects.filter(pk=self.med.pk).update(stock=-1)

    def test_stock_card_balance_matches_stock(self):
        self.create(rx=[(self.med.pk, 7, "3x1")]); services.stock_adjust(self.med.pk, -2, self.poli, "rusak")
        m = Medicine.objects.get(pk=self.med.pk)
        self.assertEqual(m.stock, 41); self.assertEqual(sum(s.qty for s in m.movements.all()), m.stock)
        self.assertEqual(m.movements.first().balance_after, m.stock)

    def test_stock_in_and_adjust_rules(self):
        for bad in (0, -1): self.assertRaises(ValueError, services.stock_in, self.med.pk, bad, self.poli)
        self.assertRaises(ValueError, services.stock_adjust, self.med.pk, 0, self.poli, "x")
        self.assertRaises(ValueError, services.stock_adjust, self.med.pk, -1, self.poli, "  ")      # alasan wajib
        self.assertRaises(ValueError, services.stock_adjust, self.med.pk, -51, self.poli, "opname")  # tidak boleh negatif
        self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 50)

    def test_movements_append_only(self):
        mv = StockMovement.objects.first()
        with self.assertRaises(PermissionError): mv.qty = 99; mv.save()
        with self.assertRaises(PermissionError): mv.delete()

    def test_stock_pages_post_and_audit(self):
        self.login("poli")
        self.client.post(f"/poli/medicines/{self.med.pk}/stock/in/", {"qty": 10, "ref": "FKT-2", "note": "dari apotek"})
        self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 60)
        self.assertEqual(self.last_audit_poli("stock_in").after["stock"], 60)
        self.client.post(f"/poli/medicines/{self.med.pk}/stock/adjust/", {"delta": -4, "note": "kedaluwarsa"})
        self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 56)
        resp = self.client.post(f"/poli/medicines/{self.med.pk}/stock/adjust/", {"delta": -999, "note": "x"}, follow=True)
        self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 56); self.assertIn("negatif", " ".join(self.msgs(resp)))
        resp = self.client.post(f"/poli/medicines/{self.med.pk}/stock/adjust/", {"delta": -1, "note": ""}, follow=True)
        self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 56)
        self.assertEqual(self.client.post(f"/poli/medicines/{self.med.pk}/stock/zzz/", {}).status_code, 404)

    def test_medicine_form_cannot_set_stock_and_code_unique_case_insensitive(self):
        self.login("poli")
        self.client.post("/poli/medicines/new/", {"code": "ibu", "name": "Ibuprofen", "unit": "tablet", "min_stock": 3, "stock": 9999})
        m = Medicine.objects.get(code="IBU"); self.assertEqual(m.stock, 0)
        resp = self.client.post("/poli/medicines/new/", {"code": "Ibu", "name": "Dobel", "unit": "tablet", "min_stock": 1}); self.assertContains(resp, "sudah dipakai")
        self.client.post(f"/poli/medicines/{m.pk}/edit/", {"code": "IBU", "name": "Ibuprofen 400", "unit": "tablet", "min_stock": 4, "stock": 777})
        m.refresh_from_db(); self.assertEqual((m.name, m.min_stock, m.stock), ("Ibuprofen 400", 4, 0))
        self.assertEqual(self.last_audit_poli("medicine_update").before["name"], "Ibuprofen")
        resp = self.client.post("/poli/medicines/new/", {"code": "NEG", "name": "Neg", "unit": "x", "min_stock": -1}); self.assertEqual(Medicine.objects.filter(code="NEG").count(), 0)

    def test_medicine_list_filters_low_stock(self):
        self.login("poli"); services.stock_adjust(self.med.pk, -46, self.poli, "opname")  # sisa 4 ≤ min 5
        names = [m.code for m in self.client.get("/poli/medicines/?low=1").context["page"]]
        self.assertEqual(names, ["PCT"]); self.assertEqual(self.client.get("/poli/medicines/?q=amox").context["page"].paginator.count, 1)


class RecordTests(PoliBase):
    def test_create_record_with_rx_reduces_stock_and_audits_without_medical_text(self):
        self.login("poli")
        resp = self.post_record(rx=[(self.med.pk, 6, "3x1"), (self.med2.pk, 2, "")], tensi="120/80", suhu="36.8")
        r = MedicalRecord.objects.get(); self.assertRedirects(resp, f"/poli/records/{r.pk}/")
        self.assertEqual((Medicine.objects.get(pk=self.med.pk).stock, Medicine.objects.get(pk=self.med2.pk).stock), (44, 8))
        self.assertEqual(r.exam, {"tensi": "120/80", "suhu": 36.8}); self.assertEqual(r.prescriptions.count(), 2); self.assertEqual(r.diagnosis, self.diag)
        a = self.last_audit_poli("create_record"); self.assertNotIn("Mencret", json.dumps(a.after)); self.assertEqual(a.after["employee"], "001")
        self.assertTrue(StockMovement.objects.filter(reason="prescription", ref=f"MR{r.pk}").count() == 2)

    def test_insufficient_stock_rolls_back_whole_record(self):
        self.login("poli")
        resp = self.post_record(rx=[(self.med2.pk, 3, ""), (self.med.pk, 51, "")])
        self.assertEqual(resp.status_code, 200); self.assertContains(resp, "tidak cukup")
        self.assertEqual(MedicalRecord.objects.count(), 0); self.assertEqual(Prescription.objects.count(), 0)
        self.assertEqual(Medicine.objects.get(pk=self.med2.pk).stock, 10)  # obat pertama TIDAK terpotong

    def test_validation_rules(self):
        self.login("poli")
        cases = [dict(nik="999"), dict(nik="009"), dict(complaint=""), dict(diagnosis_code="ZZZ"), dict(tensi="abc"), dict(suhu="99"),
                 dict(rx=[(self.med.pk, "", "")]), dict(rx=[("", 5, "")]), dict(rx=[(self.med.pk, 0, "")]), dict(rx=[(self.med.pk, -3, "")]),
                 dict(rx=[(self.med.pk, 1, ""), (self.med.pk, 2, "")]), dict(kind="kehamilan"), dict(kind="kecelakaan_kerja"), dict(kind="bogus")]
        for c in cases:
            self.assertEqual(self.post_record(**c).status_code, 200, c)
        self.assertEqual(MedicalRecord.objects.count(), 0); self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 50)

    def test_pregnancy_only_for_women_and_stores_fields(self):
        self.login("poli")
        self.assertEqual(self.post_record(nik="001", kind="kehamilan", preg_weeks="20").status_code, 200)  # Budi (L)
        self.assertEqual(MedicalRecord.objects.count(), 0)
        self.post_record(nik="002", kind="kehamilan", preg_weeks="20", preg_hpl="2027-01-10", preg_djj="140", complaint="")
        r = MedicalRecord.objects.get(); self.assertEqual(r.exam["kehamilan"], {"usia_minggu": 20, "hpl": "2027-01-10", "djj": 140})
        self.assertRaises(ValueError, services.create_record, self.poli, self.e1, "kehamilan")  # lapis servis

    def test_work_accident_requires_and_stores_incident(self):
        self.login("poli")
        self.post_record(kind="kecelakaan_kerja", incident_place="Line 2", incident_story="Tergores mesin", tensi="110/70", preg_weeks="12")
        r = MedicalRecord.objects.get(); self.assertEqual(r.exam["kecelakaan"], {"lokasi": "Line 2", "kronologi": "Tergores mesin"}); self.assertNotIn("kehamilan", r.exam)

    def test_service_rejects_duplicate_medicine_and_too_many_lines(self):  # lapis servis (form hanya lapis pertama)
        self.assertRaises(ValueError, services.create_record, self.poli, self.e1, "berobat", "", {}, None, "", [(self.med.pk, 1, ""), (self.med.pk, 2, "")])
        self.assertRaises(ValueError, services.create_record, self.poli, self.e1, "berobat", "", {}, None, "", [(self.med.pk + i, 1, "") for i in range(services.MAX_LINES + 1)])
        self.assertEqual(MedicalRecord.objects.count(), 0); self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 50)

    def test_inactive_and_deleted_employee_rejected(self):
        self.assertRaises(ValueError, services.create_record, self.poli, services.active_employee(nik="009"), "berobat")
        self.e3.soft_delete(self.hrd, "x"); self.assertIsNone(services.active_employee(nik="003"))

    def test_low_stock_notification_to_poli_users_only(self):
        self.login("poli"); self.post_record(rx=[(self.med.pk, 46, "")])  # sisa 4 ≤ 5
        self.assertEqual(Notification.objects.filter(kind="stock", user=self.poli).count(), 1)
        self.assertEqual(Notification.objects.filter(kind="stock", user=self.hrd).count(), 0)

    def test_detail_audits_view_and_shows_identity_minimum_only(self):
        r = self.create(rx=[(self.med.pk, 1, "")]); self.login("poli")
        resp = self.client.get(f"/poli/records/{r.pk}/"); self.assertEqual(resp.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(module="poli", action="view_record", object_id=str(r.pk), user=self.poli).exists())
        html = resp.content.decode(); self.assertNotIn("0001234567890", html); self.assertNotIn("TK-9988776655", html)
        self.assertEqual([k for k, _ in resp.context["idn"]], ["NIK", "Nama", "Jenis kelamin", "Departemen", "Jabatan"])

    def test_xss_escaped(self):
        self.login("poli"); self.post_record(complaint="<script>alert(1)</script>")
        r = MedicalRecord.objects.get(); body = self.client.get(f"/poli/records/{r.pk}/").content.decode()
        self.assertNotIn("<script>alert(1)</script>", body); self.assertIn("&lt;script&gt;", body)

    def test_list_filters_and_pagination(self):
        for _ in range(3): self.create()
        self.create(self.e2, kind="pemeriksaan"); self.login("poli")
        ctx = lambda q: self.client.get("/poli/records/" + q).context["page"]
        self.assertEqual(ctx("").paginator.count, 4); self.assertEqual(ctx("?kind=pemeriksaan").paginator.count, 1)
        self.assertEqual(ctx("?q=sari").paginator.count, 1); self.assertEqual(ctx("?q=001").paginator.count, 3)
        self.assertEqual(ctx(f"?date_from={self.days(1)}").paginator.count, 0); self.assertEqual(ctx("?date_from=bukan-tanggal").paginator.count, 4)
        self.assertEqual(ctx(f"?date_from={self.today()}&date_to={self.today()}").paginator.count, 4)   # batas hari inklusif
        self.assertEqual(ctx(f"?date_to={self.days(-1)}").paginator.count, 0)
        self.assertEqual(self.client.get("/poli/records/?q=%00").status_code, 400)
        self.assertEqual(self.client.get("/poli/").context["cards"][0][2], 4)

    def test_history_page_scoped_to_employee(self):
        self.create(self.e1); self.create(self.e2); self.login("poli")
        self.assertEqual(self.client.get(f"/poli/employees/{self.e1.pk}/").context["page"].paginator.count, 1)
        self.assertTrue(AuditLog.objects.filter(module="poli", action="view_history").exists())
        self.e3.soft_delete(self.hrd, "x"); self.assertEqual(self.client.get(f"/poli/employees/{self.e3.pk}/").status_code, 404)

    def test_addendum_append_only_and_audit_without_text(self):
        r = self.create(); self.login("poli")
        self.client.post(f"/poli/records/{r.pk}/addendum/", {"note": "Koreksi: alergi penisilin"})
        a = RecordAddendum.objects.get(); self.assertEqual(a.created_by, self.poli)
        self.assertNotIn("alergi", json.dumps(self.last_audit_poli("add_addendum").after or {}))
        self.client.post(f"/poli/records/{r.pk}/addendum/", {"note": "   "}); self.assertEqual(RecordAddendum.objects.count(), 1)
        with self.assertRaises(PermissionError): a.note = "x"; a.save()
        with self.assertRaises(PermissionError): a.delete()
        self.assertContains(self.client.get(f"/poli/records/{r.pk}/"), "alergi penisilin")

    def test_unknown_record_404(self):
        self.login("poli"); self.assertEqual(self.client.get("/poli/records/99999/").status_code, 404)


class ReferralTests(PoliBase):
    def test_create_flow_and_numbering(self):
        r = self.create(); self.login("poli")
        resp = self.client.post(f"/poli/records/{r.pk}/referral/new/", {"facility": "RSUD Kota", "note": "Curiga apendisitis"})
        ref = Referral.objects.get(); self.assertRedirects(resp, f"/poli/referrals/{ref.pk}/"); self.assertRegex(ref.number, r"^RJK/\d{6}/0001$")
        self.assertEqual((ref.status, ref.created_by), ("diajukan", self.poli))
        # satu rujukan berjalan per rekam medis
        resp = self.client.post(f"/poli/records/{r.pk}/referral/new/", {"facility": "RS Lain"}); self.assertContains(resp, "masih punya rujukan")
        self.assertEqual(Referral.objects.count(), 1)
        self.client.post(f"/poli/referrals/{ref.pk}/finish/", {"outcome": "x"}); ref.refresh_from_db(); self.assertEqual(ref.status, "diajukan")  # tidak boleh lompat
        self.client.post(f"/poli/referrals/{ref.pk}/send/"); ref.refresh_from_db(); self.assertEqual(ref.status, "dirujuk")
        self.client.post(f"/poli/referrals/{ref.pk}/finish/", {"outcome": ""}); ref.refresh_from_db(); self.assertEqual(ref.status, "dirujuk")  # hasil wajib
        self.client.post(f"/poli/referrals/{ref.pk}/finish/", {"outcome": "Rawat inap 3 hari"}); ref.refresh_from_db()
        self.assertEqual((ref.status, ref.outcome), ("selesai", "Rawat inap 3 hari"))
        self.client.post(f"/poli/referrals/{ref.pk}/cancel/", {"outcome": "x"}); ref.refresh_from_db(); self.assertEqual(ref.status, "selesai")  # final
        self.assertNotIn("Rawat", json.dumps(self.last_audit_poli("referral_finish").after))

    def test_cancel_requires_reason_and_frees_record_for_new_referral(self):
        r = self.create(); ref = services.create_referral(self.poli, r, "RS A"); self.login("poli")
        self.client.post(f"/poli/referrals/{ref.pk}/cancel/", {"outcome": ""}); ref.refresh_from_db(); self.assertEqual(ref.status, "diajukan")
        self.client.post(f"/poli/referrals/{ref.pk}/cancel/", {"outcome": "Pasien menolak"}); ref.refresh_from_db(); self.assertEqual(ref.status, "batal")
        self.assertEqual(self.client.get(f"/poli/referrals/{ref.pk}/letter.pdf").status_code, 404)
        self.assertEqual(services.create_referral(self.poli, r, "RS B").number[-4:], "0002")

    def test_unknown_action_and_facility_required(self):
        r = self.create(); ref = services.create_referral(self.poli, r, "RS A"); self.login("poli")
        self.assertEqual(self.client.post(f"/poli/referrals/{ref.pk}/zzz/").status_code, 404)
        self.assertRaises(ValueError, services.create_referral, self.poli, r, "  ")

    def test_letter_pdf_a4_audited(self):
        r = self.create(); ref = services.create_referral(self.poli, r, "RS A"); self.login("poli")
        resp = self.client.get(f"/poli/referrals/{ref.pk}/letter.pdf"); self.assertEqual(resp["Content-Type"], "application/pdf"); self.assertTrue(resp.content.startswith(b"%PDF"))
        self.assertTrue(AuditLog.objects.filter(module="poli", action="print_referral").exists())

    def test_list_filter_and_dashboard_excludes_cancelled(self):
        r1, r2 = self.create(), self.create(self.e2)
        a, b = services.create_referral(self.poli, r1, "RS A"), services.create_referral(self.poli, r2, "RS B")
        services.referral_transition(b.pk, "batal", self.poli, "tidak jadi"); self.login("poli")
        self.assertEqual(self.client.get("/poli/referrals/?status=berjalan").context["page"].paginator.count, 1)
        self.assertEqual(self.client.get("/api/dashboard/").json()["open_referrals"], 1)


class ApiAndLetterTests(PoliBase):
    def api(self, body): return self.client.post("/api/poli/records/", json.dumps(body), content_type="application/json")

    def test_api_still_works_and_rejects_bad_input_with_400(self):
        self.login("poli")
        ok = self.api({"employee_id": self.e1.pk, "kind": "berobat", "complaint": "x", "diagnosis_id": self.diag.pk, "prescriptions": [{"medicine_id": self.med.pk, "qty": 2, "dosage": "1x1"}]})
        self.assertEqual(ok.status_code, 201); self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 48)
        bad = [{"employee_id": self.e1.pk, "kind": "berobat", "prescriptions": [{"medicine_id": self.med.pk, "qty": -10}]},   # dulu: MENAMBAH stok
               {"employee_id": self.e1.pk, "kind": "berobat", "prescriptions": [{"medicine_id": self.med.pk, "qty": "2"}]},
               {"employee_id": self.e1.pk, "kind": "berobat", "prescriptions": [{"medicine_id": 99999, "qty": 1}]},             # dulu: 500
               {"employee_id": self.e_off.pk, "kind": "berobat"}, {"employee_id": 99999, "kind": "berobat"}, {"kind": "berobat"},
               {"employee_id": self.e1.pk, "kind": "ngawur"}, {"employee_id": self.e1.pk, "kind": "berobat", "diagnosis_id": 99999},
               {"employee_id": self.e1.pk, "kind": "berobat", "exam": "bukan-objek"}, [1, 2]]
        for b in bad: self.assertEqual(self.api(b).status_code, 400, b)
        self.assertEqual(self.client.post("/api/poli/records/", "{bukan json", content_type="application/json").status_code, 400)
        self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 48); self.assertEqual(MedicalRecord.objects.count(), 1)

    def test_sick_leave_letter_idempotent(self):
        r = self.create(); self.login("poli")
        a = self.client.get(f"/api/poli/records/{r.pk}/letter.pdf"); b = self.client.get(f"/api/poli/records/{r.pk}/letter.pdf")
        self.assertEqual(a.status_code, 200); self.assertEqual(SickLeaveLetter.objects.count(), 1)


class DiagnosisTests(PoliBase):
    def test_crud_and_unique_code(self):
        self.login("poli")
        self.client.post("/poli/diagnoses/new/", {"code": "j00", "name": "Common cold", "category": "ISPA"})
        d = Diagnosis.objects.get(code="J00"); self.assertEqual(d.name, "Common cold")
        self.assertContains(self.client.post("/poli/diagnoses/new/", {"code": "J00", "name": "Dobel"}), "sudah dipakai")
        self.client.post(f"/poli/diagnoses/{d.pk}/edit/", {"code": "J00", "name": "Nasofaringitis akut", "category": "ISPA"}); d.refresh_from_db(); self.assertEqual(d.name, "Nasofaringitis akut")
        self.assertEqual(self.client.get("/poli/diagnoses/?q=nasofar").context["page"].paginator.count, 1)
        self.assertEqual(self.last_audit_poli("diagnosis_update").before["name"], "Common cold")


def last_audit_poli(self, action): return AuditLog.objects.filter(module="poli", action=action).order_by("-id").first()
PoliBase.last_audit_poli = last_audit_poli


# ------------------------------------------------------------------ konkurensi (bermakna di PostgreSQL)
import threading
from datetime import date
from django.db import connection
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from apps.hr.models import Department, Employee, Position


class PoliConcurrencyTests(TransactionTestCase):
    """Petugas bersamaan: stok tidak boleh negatif/ganda, rujukan tidak diproses dua kali, tidak ada 500 (hasil ditolak rapi)."""
    def setUp(self):
        d = Department.objects.create(code="Z", name="Z"); pos = Position.objects.create(name="S")
        self.e = Employee.objects.create(nik="C1", name="Konkuren", gender="L", department=d, position=pos, join_date=date(2020, 1, 1))
        self.u1, self.u2 = (User.objects.create_user(n, password=PW, role=Role.POLI) for n in ("p1", "p2"))
        self.med = Medicine.objects.create(code="PCT", name="Paracetamol", unit="tablet", min_stock=0)
        services.stock_in(self.med.pk, 50, self.u1)

    def race(self, fns):
        barrier, results = threading.Barrier(len(fns)), []
        def worker(fn):
            try: barrier.wait(timeout=5); fn(); results.append("ok")
            except ValueError: results.append("ditolak")
            except Exception as ex: results.append(f"ERROR {type(ex).__name__}: {ex}")
            finally: connection.close()
        ts = [threading.Thread(target=worker, args=(f,)) for f in fns]
        [t.start() for t in ts]; [t.join(15) for t in ts]
        return sorted(results)

    @skipUnlessDBFeature("has_select_for_update")
    def test_two_prescriptions_competing_for_same_stock(self):
        mk = lambda u: (lambda: services.create_record(u, self.e, "berobat", "x", {}, None, "", [(self.med.pk, 30, "")]))
        self.assertEqual(self.race([mk(self.u1), mk(self.u2)]), ["ditolak", "ok"])
        m = Medicine.objects.get(pk=self.med.pk); self.assertEqual(m.stock, 20)
        self.assertEqual(MedicalRecord.objects.count(), 1)  # yang ditolak tidak meninggalkan rekam medis setengah jadi
        self.assertEqual(sum(s.qty for s in m.movements.all()), m.stock)

    @skipUnlessDBFeature("has_select_for_update")
    def test_many_adjustments_never_go_negative(self):
        fns = [lambda: services.stock_adjust(self.med.pk, -20, self.u1, "opname") for _ in range(5)]
        self.assertEqual(self.race(fns), ["ditolak"] * 3 + ["ok"] * 2)
        self.assertEqual(Medicine.objects.get(pk=self.med.pk).stock, 10)

    @skipUnlessDBFeature("has_select_for_update")
    def test_referral_transition_processed_once(self):
        r = services.create_record(self.u1, self.e, "berobat")[0]; ref = services.create_referral(self.u1, r, "RS A")
        self.assertEqual(self.race([lambda: services.referral_transition(ref.pk, "dirujuk", self.u1), lambda: services.referral_transition(ref.pk, "dirujuk", self.u2)]), ["ditolak", "ok"])

    @skipUnlessDBFeature("has_select_for_update")
    def test_only_one_running_referral_per_record(self):
        r = services.create_record(self.u1, self.e, "berobat")[0]
        self.assertEqual(self.race([lambda: services.create_referral(self.u1, r, "RS A"), lambda: services.create_referral(self.u2, r, "RS B")]), ["ditolak", "ok"])
        self.assertEqual(Referral.objects.count(), 1)
