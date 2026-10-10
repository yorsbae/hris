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


class CheckDetailUxTests(Base):
    """Putaran 34: halaman detail (alur, giliran siapa, aksi per peran) dan form baru (langkah bernomor)."""

    def page(self, who, pk): self.login(who); return self.client.get(reverse("attcheck_detail", args=[pk]))

    def test_steps_follow_status(self):
        from apps.hr.attendance_views import flow_steps
        st = lambda s: [x["state"] for x in flow_steps(s)]
        self.assertEqual(st("diminta"), ["done", "now", "todo"]); self.assertEqual(st("dikembalikan"), ["done", "now", "todo"])
        self.assertEqual(st("dijawab"), ["done", "done", "now"]); self.assertEqual(st("diverifikasi"), ["done", "done", "done"]); self.assertEqual(st("dibatalkan"), ["done", "off", "off"])

    def test_admin_turn_and_answer_form_only_when_it_is_their_turn(self):
        a = self.make(); r = self.page("adm", a.pk)
        self.assertContains(r, "Giliran Anda: jawab"); self.assertContains(r, "Kirim jawaban ke HRD"); self.assertNotContains(r, "Terima jawaban"); self.assertNotContains(r, "Tindakan lain")
        self.act("adm", "attcheck_answer", a.pk, answer="hadir"); r = self.page("adm", a.pk)
        self.assertContains(r, "Menunggu verifikasi HRD"); self.assertContains(r, '<div class="note wait">'); self.assertNotContains(r, '<div class="note now">'); self.assertNotContains(r, "Kirim jawaban ke HRD")

    def test_hrd_waits_then_verifies_with_three_labelled_buttons(self):
        a = self.make(); r = self.page("hrd", a.pk)
        self.assertContains(r, "Menunggu jawaban Admin Departemen"); self.assertNotContains(r, "Terima jawaban"); self.assertContains(r, "Tindakan lain"); self.assertContains(r, "Batalkan permintaan")
        self.act("adm", "attcheck_answer", a.pk, answer="izin", note="Surat dokter"); r = self.page("hrd", a.pk)
        self.assertContains(r, "Giliran Anda: periksa jawaban"); self.assertContains(r, 'name="action" value="terima"'); self.assertContains(r, 'name="action" value="kembalikan"'); self.assertContains(r, 'name="action" value="ubah"')
        self.assertContains(r, "Surat dokter")

    def test_decision_buttons_post_the_same_contract(self):
        a = self.make(); self.act("adm", "attcheck_answer", a.pk, answer="hadir")
        self.act("hrd", "attcheck_verify", a.pk, action="kembalikan", note="Cek ulang"); a.refresh_from_db(); self.assertEqual(a.status, "dikembalikan")
        r = self.page("adm", a.pk); self.assertContains(r, "HRD mengembalikannya"); self.assertContains(r, "Kirim jawaban ke HRD")

    def test_verified_shows_done_note_and_correction_is_secondary(self):
        a = self.make(); self.act("adm", "attcheck_answer", a.pk, answer="hadir"); self.act("hrd", "attcheck_verify", a.pk, action="terima")
        r = self.page("hrd", a.pk); self.assertContains(r, "Hasil sudah dikunci"); self.assertContains(r, "Koreksi hasil"); self.assertContains(r, "Catat koreksi"); self.assertNotContains(r, "Batalkan permintaan")
        self.assertNotContains(self.page("adm", a.pk), "Koreksi hasil")

    def test_cancelled_has_no_actions(self):
        a = self.make(); self.act("hrd", "attcheck_cancel", a.pk, reason="Salah tanggal"); r = self.page("hrd", a.pk)
        self.assertContains(r, "Permintaan ini dibatalkan"); self.assertContains(r, "Salah tanggal"); self.assertNotContains(r, "Tindakan lain"); self.assertNotContains(r, "Terima jawaban")

    def test_new_form_numbered_steps_and_overview_open_only_when_admin_missing(self):
        self.login("hrd"); r = self.client.get(reverse("attcheck_new"))
        for t in ("Siapa yang ditanyakan?", "Tanggal berapa?", "Catatan untuk Admin"): self.assertContains(r, t)
        self.assertContains(r, "belum punya Admin Departemen aktif"); self.assertContains(r, 'Admin Departemen per departemen')
        self.assertContains(r, '<details class="flt" style="max-width:720px;margin-top:14px" open>')

    def test_new_form_keeps_nik_rows_and_department_after_error(self):
        self.login("hrd"); r = self.ask("001", self.days(1)); self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="001"'); r = self.ask("", self.days(1), department=self.d1.pk); self.assertContains(r, '<details class="flt" open>')
