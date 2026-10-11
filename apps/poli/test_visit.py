"""Putaran 46: Rekap visit pasien (rawat jalan / sakit / rumah) di akun Poli."""
import re
from datetime import timedelta
from django.utils import timezone
from apps.core.models import AuditLog
from apps.poli import services
from apps.poli.models import PatientVisit
from apps.poli.test_poli import PoliBase


class VisitTests(PoliBase):
    def setUp(self): self.login("poli")

    def add(self, nik="001", kind="rawat_jalan", **kw):
        data = {"nik": nik, "kind": kind, "visit_date": timezone.localdate().isoformat(), "complaint": "Demam 2 hari", **kw}
        return self.client.post("/poli/visits/new/", data)

    def test_create_each_kind_and_encrypt_complaint(self):
        self.assertEqual(self.add().status_code, 302)
        self.assertEqual(self.add("002", "sakit", rest_days="3").status_code, 302)
        self.assertEqual(self.add("003", "rumah", location="Mess Blok C").status_code, 302)
        self.assertEqual(PatientVisit.objects.count(), 3)
        v = PatientVisit.objects.get(kind="sakit"); self.assertEqual(v.rest_days, 3); self.assertEqual(v.status, "berjalan")
        from django.db import connection
        with connection.cursor() as c: c.execute("select complaint from poli_patientvisit where id=%s", [v.pk]); raw = c.fetchone()[0]
        self.assertNotIn("Demam", raw)  # keluhan terenkripsi di DB

    def test_validation(self):
        r = self.add(kind="rumah"); self.assertEqual(r.status_code, 200); self.assertContains(r, "wajib mengisi lokasi")
        self.assertEqual(self.add("009").status_code, 200)  # nonaktif ditolak
        self.assertEqual(self.add("404").status_code, 200)
        r = self.add(visit_date=(timezone.localdate() + timedelta(days=1)).isoformat()); self.assertContains(r, "masa depan")
        self.assertEqual(PatientVisit.objects.count(), 0)
        # rest_days diabaikan bila bukan jenis sakit
        self.add(rest_days="5"); self.assertIsNone(PatientVisit.objects.get().rest_days)

    def test_transition_requires_outcome_and_is_final(self):
        self.add(); v = PatientVisit.objects.get()
        r = self.client.post(f"/poli/visits/{v.pk}/finish/", {"outcome": ""}, follow=True); self.assertIn("wajib", " ".join(self.msgs(r))); v.refresh_from_db(); self.assertEqual(v.status, "berjalan")
        self.client.post(f"/poli/visits/{v.pk}/finish/", {"outcome": "Sembuh, kembali bekerja"}); v.refresh_from_db(); self.assertEqual((v.status, v.outcome), ("selesai", "Sembuh, kembali bekerja"))
        r = self.client.post(f"/poli/visits/{v.pk}/cancel/", {"outcome": "salah"}, follow=True); self.assertIn("tidak bisa", " ".join(self.msgs(r)))
        self.assertEqual(self.client.post(f"/poli/visits/{v.pk}/bogus/", {}).status_code, 404)
        self.add(); v2 = PatientVisit.objects.exclude(pk=v.pk).get(); self.client.post(f"/poli/visits/{v2.pk}/cancel/", {"outcome": "Salah catat"}); v2.refresh_from_db(); self.assertEqual(v2.status, "batal")

    def test_list_filters_and_kpi(self):
        self.add(); self.add("002", "sakit"); self.add("003", "rumah", location="Rumah")
        r = self.client.get("/poli/visits/"); self.assertEqual(r.context["total"], 3); self.assertEqual(r.context["running"], 3); self.assertContains(r, "Rekap Visit Pasien")
        self.assertEqual(len(self.client.get("/poli/visits/?kind=sakit").context["page"]), 1)
        self.assertEqual(len(self.client.get("/poli/visits/?q=Sari").context["page"]), 1)
        self.assertEqual(len(self.client.get(f"/poli/visits/?department={self.d2.pk}").context["page"]), 1)
        self.assertEqual(len(self.client.get("/poli/visits/?date_from=2000-01-01&date_to=2000-01-02").context["page"]), 0)
        self.assertEqual(len(self.client.get("/poli/visits/?status=selesai").context["page"]), 0)

    def test_toolbar_hooks_present(self):
        r = self.client.get("/poli/visits/"); self.assertContains(r, "data-fbar"); self.assertContains(r, "+ Catat visit"); self.assertContains(r, "export=1")

    def test_export_has_no_medical_text_and_is_audited(self):
        self.add(complaint="RAHASIA-MEDIS"); r = self.client.get("/poli/visits/?export=1")
        body = r.content.decode("utf-8-sig"); self.assertIn("001", body); self.assertNotIn("RAHASIA-MEDIS", body); self.assertTrue(AuditLog.objects.filter(action="visit_export").exists())
        self.assertEqual(self.client.get("/poli/visits/?export=1&format=xlsx").status_code, 200)

    def test_audit_has_no_medical_text(self):
        self.add(complaint="RAHASIA-MEDIS"); v = PatientVisit.objects.get(); self.client.get(f"/poli/visits/{v.pk}/")
        self.client.post(f"/poli/visits/{v.pk}/finish/", {"outcome": "HASIL-RAHASIA"})
        blob = " ".join(str(a.before) + str(a.after) for a in AuditLog.objects.filter(module="poli"))
        self.assertNotIn("RAHASIA", blob); self.assertTrue(AuditLog.objects.filter(action="view_visit").exists())

    def test_only_poli_and_superadmin(self):
        self.add(); v = PatientVisit.objects.get()
        for u in ("hrd", "adm"):
            self.login(u)
            for url in ("/poli/visits/", "/poli/visits/new/", f"/poli/visits/{v.pk}/"): self.assertEqual(self.client.get(url).status_code, 403, (u, url))
            self.assertEqual(self.client.post(f"/poli/visits/{v.pk}/finish/", {"outcome": "x"}).status_code, 403)
        self.login("su"); self.assertEqual(self.client.get("/poli/visits/").status_code, 200)

    def test_menu_and_hub(self):
        self.assertContains(self.client.get("/poli/"), "/poli/visits/"); self.assertContains(self.client.get("/poli/visits/"), "Rekap Visit Pasien")

    def test_service_rejects_inactive(self):
        with self.assertRaises(ValueError): services.create_visit(self.poli, self.e_off, "rawat_jalan", timezone.localdate())
