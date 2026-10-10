"""Putaran 33: UI/UX berbasis fungsi pada daftar Validasi Kehadiran (struktur halaman; perilaku tidak berubah)."""
from django.urls import reverse
from .test_attendance_check import Base


class ChecksListUxTests(Base):
    def test_hrd_sees_flow_primary_action_and_collapsed_filters(self):
        self.login("hrd"); r = self.client.get(reverse("attcheck_list"))
        self.assertContains(r, "Anda mengirim permintaan"); self.assertContains(r, "+ Permintaan baru")
        self.assertContains(r, "Lainnya ▾"); self.assertContains(r, "Daftar validasi · XLSX")
        self.assertContains(r, '<details class="flt">'); self.assertNotContains(r, "Reset filter")
        self.assertContains(r, "Belum ada permintaan validasi. Mulai dengan tombol")

    def test_admin_sees_own_flow_no_create_no_export(self):
        self.login("adm"); r = self.client.get(reverse("attcheck_list"))
        self.assertContains(r, "Anda menjawab"); self.assertNotContains(r, "+ Permintaan baru"); self.assertNotContains(r, "Lainnya ▾")
        self.assertContains(r, "Anda akan diberi notifikasi")

    def test_filter_opens_and_reset_appears_when_used(self):
        self.make(); r = self.client.get(reverse("attcheck_list") + "?status=todo")
        self.assertContains(r, '<details class="flt" open>'); self.assertContains(r, "Reset filter")
        self.assertContains(self.client.get(reverse("attcheck_list") + "?q=tidakada"), "Tidak ada permintaan yang cocok dengan filter")

    def test_row_action_label_matches_role_and_status(self):
        c = self.make(); self.login("adm"); self.assertContains(self.client.get(reverse("attcheck_list")), "Jawab ›")
        self.login("hrd"); self.assertNotContains(self.client.get(reverse("attcheck_list")), "Jawab ›")

    def test_search_form_keeps_active_filters(self):
        self.make(); r = self.client.get(reverse("attcheck_list") + "?status=todo&q=00")
        self.assertContains(r, '<input type="hidden" name="status" value="todo">')
