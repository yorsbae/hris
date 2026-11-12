"""Absensi manual Prakerin (lembar tanda tangan): tanpa mesin, diisi HRD/Admin Dept per tanggal."""
from datetime import timedelta
from unittest import mock
from django.urls import reverse
from django.utils import timezone
from apps.hr import fingerspot_users
from apps.hr.models import AttendanceDaily, ManualAttendance
from apps.hr.test_attendance import Base, workday


class ManualBase(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.e1.pay_basis = "prakerin"; cls.e1.save()     # e1: shift Pagi 07:00–15:00

    def save(self, d, emp=None, **kw):
        emp = emp or self.e1
        data = {"date": str(d), f"st_{emp.pk}": kw.get("st", ""), f"in_{emp.pk}": kw.get("t_in", ""), f"out_{emp.pk}": kw.get("t_out", ""), f"note_{emp.pk}": kw.get("note", "")}
        return self.client.post(reverse("att_manual"), data, follow=True)


class ManualCalcTests(ManualBase):
    def test_prakerin_tidak_dinilai_dari_mesin_dan_tidak_tanpa_scan(self):
        d = workday(); self.run_calc(d)
        self.assertFalse(AttendanceDaily.objects.filter(employee=self.e1, date=d).exists())      # bukan tanpa_scan/alfa
        self.assertTrue(AttendanceDaily.objects.filter(employee=self.e3, date=d).exists())       # karyawan biasa tetap diolah

    def test_scan_mesin_prakerin_diabaikan(self):
        d = workday(); self.scan(self.e1, d, (7, 0), (15, 0)); self.run_calc(d)
        self.assertFalse(AttendanceDaily.objects.filter(employee=self.e1, date=d).exists())

    def test_hadir_dengan_jam_menghitung_terlambat_dan_pulang_cepat(self):
        self.login("hrd"); d = workday()
        self.save(d, st="hadir", t_in="07:12", t_out="14:40", note="TTD ada")
        r = self.row(self.e1, d)
        self.assertEqual((r.status, r.source, r.late_minutes, r.early_minutes, r.scan_count, r.incomplete), ("hadir", "manual", 12, 20, 0, False))
        self.assertIn("TTD ada", r.notes)

    def test_izin_mengabaikan_jam(self):
        self.login("hrd"); d = workday(); self.save(d, st="izin", t_in="07:00", t_out="15:00")
        m = ManualAttendance.objects.get(employee=self.e1, date=d)
        self.assertEqual((m.status, m.time_in, m.time_out), ("izin", None, None)); self.assertEqual(self.row(self.e1, d).status, "izin")

    def test_status_kosong_menghapus_isian_dan_baris(self):
        self.login("hrd"); d = workday(); self.save(d, st="sakit"); self.assertTrue(AttendanceDaily.objects.filter(employee=self.e1, date=d).exists())
        self.save(d, st="")
        self.assertFalse(ManualAttendance.objects.filter(employee=self.e1, date=d).exists()); self.assertFalse(AttendanceDaily.objects.filter(employee=self.e1, date=d).exists())

    def test_olah_ulang_tidak_menghapus_baris_manual_tetapi_membersihkan_sisa_mesin(self):
        d = workday(); self.login("hrd"); self.save(d, st="hadir")
        AttendanceDaily.objects.create(employee=self.e1, date=workday(4), status="tanpa_scan", source="mesin")    # sisa dari sebelum jadi Prakerin
        self.run_calc(workday(4), d)
        self.assertEqual(self.row(self.e1, d).source, "manual"); self.assertFalse(AttendanceDaily.objects.filter(employee=self.e1, date=workday(4)).exists())


class ManualPageTests(ManualBase):
    def test_akses(self):
        self.client.logout(); self.assertIn(self.client.get(reverse("att_manual")).status_code, (401, 302))
        self.login("poli"); self.assertEqual(self.client.get(reverse("att_manual")).status_code, 403)
        for who in ("hrd", "adm"): self.login(who); self.assertEqual(self.client.get(reverse("att_manual")).status_code, 200, who)

    def test_hanya_prakerin_yang_tampil(self):
        self.login("hrd"); r = self.client.get(reverse("att_manual"))
        self.assertContains(r, self.e1.name); self.assertNotContains(r, f'name="st_{self.e3.pk}"')

    def test_admin_dept_hanya_departemennya(self):
        self.login("adm"); other = [e for e in (self.e2, self.e3) if e.department_id != self.e1.department_id]
        for e in other: e.pay_basis = "prakerin"; e.save()
        r = self.client.get(reverse("att_manual"))
        for e in other: self.assertNotContains(r, f'name="st_{e.pk}"')
        d = workday(); self.save(d, st="hadir"); self.assertTrue(ManualAttendance.objects.filter(employee=self.e1, date=d).exists())

    def test_tanggal_masa_depan_ditolak(self):
        self.login("hrd"); f = timezone.localdate() + timedelta(days=2); self.save(f, st="hadir")
        self.assertFalse(ManualAttendance.objects.filter(date=f).exists())

    def test_periode_tertutup_ditolak(self):
        self.login("hrd"); d = workday()
        with mock.patch("apps.hr.periods.open_segments", return_value=[]): self.save(d, st="hadir")
        self.assertFalse(ManualAttendance.objects.filter(employee=self.e1, date=d).exists())

    def test_jam_salah_format_tidak_menyimpan_apa_pun(self):
        self.login("hrd"); d = workday(); self.save(d, st="hadir", t_in="7 pagi")
        self.assertFalse(ManualAttendance.objects.filter(employee=self.e1, date=d).exists())

    def test_hari_ini_langsung_masuk_rekap(self):
        self.login("hrd"); t = timezone.localdate(); self.save(t, st="hadir", t_in="07:00", t_out="15:00")
        self.assertEqual(self.row(self.e1, t).source, "manual")


class FingerspotExclusionTests(ManualBase):
    def test_prakerin_tidak_ada_di_daftar_karyawan_tanpa_pin(self):
        f = {"q": "", "dept": []}
        ids = {e.pk for e in fingerspot_users.nopin_employees(f)}
        self.assertNotIn(self.e1.pk, ids); self.assertIn(self.e3.pk, ids)
