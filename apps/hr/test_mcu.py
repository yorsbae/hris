"""MCU (HRD input di info karyawan, Poli hanya melihat) + kartu Dashboard "Perlu perhatian" yang bisa diklik (daftar tersaring = angka kartu)."""
import shutil, tempfile, atexit
from datetime import date, timedelta
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from apps.core.models import AuditLog
from apps.hrd.test_base import HrdBase
from apps.hrd.models import BpjsMembership, MaternityLeave
from .models import Contract, MedicalCheckup

MEDIA = tempfile.mkdtemp(); atexit.register(shutil.rmtree, MEDIA, ignore_errors=True)
T = date.today()


@override_settings(MEDIA_ROOT=MEDIA)
class McuTests(HrdBase):
    def save(self, user="hrd", emp=None, **kw):
        self.login(user)
        data = {"checkup_date": (T - timedelta(days=3)).isoformat(), "provider": "Klinik Sehat", "result": "fit_catatan", "notes": "Kolesterol tinggi, diet", **kw}
        return self.client.post(f"/employees/{(emp or self.e1).pk}/mcu/", data)

    def test_hrd_inputs_poli_views_others_blocked(self):
        r = self.save(); m = MedicalCheckup.objects.get(); self.assertEqual(m.employee, self.e1); self.assertEqual(r.status_code, 302)
        self.login("poli"); self.assertContains(self.client.get("/poli/mcu/"), "Klinik Sehat")
        self.assertContains(self.client.get(f"/employees/{self.e1.pk}/"), "MCU karyawan baru")
        self.assertEqual(self.client.post(f"/employees/{self.e1.pk}/mcu/", {}).status_code, 403)   # Poli tidak menginput
        self.login("adm"); self.assertEqual(self.client.get("/poli/mcu/").status_code, 403); self.assertNotContains(self.client.get(f"/employees/{self.e1.pk}/"), "Klinik Sehat")
        self.login("hrd"); self.assertEqual(self.client.get("/poli/mcu/").status_code, 403)       # HRD input lewat info karyawan, daftar milik Poli

    def test_one_per_employee_and_editable(self):
        self.save(); self.save(provider="RS Baru", result="fit", notes="")
        self.assertEqual(MedicalCheckup.objects.count(), 1)                                      # edit, bukan data kedua
        m = MedicalCheckup.objects.get(); self.assertEqual((m.provider, m.result), ("RS Baru", "fit")); self.assertEqual(m.updated_by.username, "hrd")
        self.assertTrue(AuditLog.objects.filter(action="mcu_update").exists())
        self.assertEqual(self.client.get(f"/employees/{self.e1.pk}/mcu/").context["editing"], True)

    def test_audit_has_no_medical_content(self):
        self.save(); a = AuditLog.objects.get(action="mcu_create"); self.assertNotIn("Kolesterol", str(a.after)); self.assertNotIn("fit_catatan", str(a.after))

    def test_future_date_rejected(self):
        self.save(checkup_date=(T + timedelta(days=2)).isoformat()); self.assertFalse(MedicalCheckup.objects.exists())

    def test_file_access_replace_and_type_check(self):
        self.save(file=SimpleUploadedFile("hasil.pdf", b"%PDF-1.4 isi", content_type="application/pdf")); m = MedicalCheckup.objects.get(); self.assertEqual(m.original_name, "hasil.pdf")
        for u, code in (("poli", 200), ("hrd", 200), ("adm", 403)):
            self.login(u); self.assertEqual(self.client.get(f"/employees/{self.e1.pk}/mcu/file/").status_code, code)
        self.save(notes="ubah tanpa file baru"); m.refresh_from_db(); self.assertEqual(m.original_name, "hasil.pdf"); self.assertTrue(m.file)   # berkas lama tetap
        self.save(file=SimpleUploadedFile("x.pdf", b"bukan pdf", content_type="application/pdf")); m.refresh_from_db(); self.assertEqual(m.notes, "ubah tanpa file baru")  # file palsu ditolak
        self.save(emp=self.e2, file=SimpleUploadedFile("y.pdf", b"bukan pdf", content_type="application/pdf")); self.assertEqual(MedicalCheckup.objects.count(), 1)


class DrilldownTests(HrdBase):
    """Klik kartu → daftar hanya berisi baris yang dihitung kartu."""
    def card(self, key): self.login("hrd"); return self.client.get("/api/dashboard/").json()[key]

    def test_maternity_berjalan(self):
        MaternityLeave.objects.create(employee=self.e2, due_date=T + timedelta(days=30), start_date=T - timedelta(days=10), end_date=T + timedelta(days=60), state="aktif")
        MaternityLeave.objects.create(employee=self.e3, due_date=T + timedelta(days=200), start_date=T + timedelta(days=150), end_date=T + timedelta(days=250), state="aktif")   # belum mulai
        MaternityLeave.objects.create(employee=self.e1, due_date=T - timedelta(days=300), start_date=T - timedelta(days=340), end_date=T - timedelta(days=200), state="selesai")
        self.assertEqual(self.card("maternity_active"), 1)
        r = self.client.get("/hrd/maternity/?state=berjalan"); self.assertEqual(r.context["page"].paginator.count, 1)

    def test_bpjs_nonaktif_counts_employees_not_programs(self):
        for s in ("kes", "tk"): BpjsMembership.objects.create(employee=self.e1, scheme=s, status="nonaktif", effective_date=T)
        BpjsMembership.objects.create(employee=self.e_off, scheme="kes", status="nonaktif", effective_date=T)          # karyawan nonaktif: tidak dihitung
        self.assertEqual(self.card("bpjs_inactive"), 1)                                                                # 2 program, 1 karyawan
        r = self.client.get("/hrd/bpjs/?bpjs=nonaktif"); self.assertEqual(r.context["page"].paginator.count, 1)

    def test_contract_windows(self):
        mk = lambda e, n, end: Contract.objects.create(employee=e, number=n, kind="PKWT", start=T - timedelta(days=300), end=end, status="aktif")
        mk(self.e1, "K1", T + timedelta(days=5)); mk(self.e2, "K2", T + timedelta(days=45)); mk(self.e3, "K3", T - timedelta(days=3))
        for key, q in (("contract_le_7", "le7"), ("contract_le_60", "le60"), ("contract_expired", "expired"), ("contract_le_30", "expiring")):
            n = self.card(key); r = self.client.get(f"/api/employees/?status=aktif&contract={q}"); self.assertEqual(r.json()["count"], n, key)
        self.assertEqual(self.card("contract_le_7"), 1); self.assertEqual(self.card("contract_le_60"), 2)
