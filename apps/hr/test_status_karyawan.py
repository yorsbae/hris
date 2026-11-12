"""Status karyawan: bulanan, harian, borongan, magang harian, magang borongan, prakerin."""
from django.test import SimpleTestCase, TestCase
from .importer import norm_basis
from .models import Employee, Holiday


class StatusKaryawanChoices(SimpleTestCase):
    def test_semua_status_tersedia(self):
        self.assertEqual([k for k, _ in Employee.PAY_BASIS],
                         ["bulanan", "harian", "borongan", "magang_harian", "magang_borongan", "prakerin"])

    def test_nilai_terpanjang_muat_di_kolom(self):
        f = Employee._meta.get_field("pay_basis")
        self.assertTrue(all(len(k) <= f.max_length for k, _ in Employee.PAY_BASIS))

    def test_normalisasi_impor(self):
        for raw in ("Magang Harian", "magang-harian", " MAGANG_HARIAN "):
            self.assertEqual(norm_basis(raw), "magang_harian")
        self.assertEqual(norm_basis("Prakerin"), "prakerin")
        self.assertIn(norm_basis("Magang Borongan"), dict(Employee.PAY_BASIS))

    def test_hari_libur_harian_dan_magang_harian_masuk_lainnya_tidak(self):
        h = Holiday(duty="none", daily_work=True)
        for basis, masuk in (("harian", True), ("magang_harian", True), ("bulanan", False), ("borongan", False),
                             ("magang_borongan", False), ("prakerin", False)):
            self.assertEqual(h.on_duty(Employee(pay_basis=basis)), masuk, basis)
