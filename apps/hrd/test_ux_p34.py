"""Putaran 34: UI/UX berbasis fungsi — Potongan BPJS, Status BPJS, Katering (struktur halaman; perilaku data tidak berubah)."""
from datetime import date
from django.urls import reverse
from .models import CateringOrder
from .test_base import HrdBase


class BpjsDeductionsUxTests(HrdBase):
    def setUp(self): self.login("hrd")

    def test_flow_primary_action_and_folded_secondary(self):
        r = self.client.get(reverse("hrd_bpjs_deductions") + "?period=2026-10")
        self.assertContains(r, "Alur:"); self.assertContains(r, "+ Potongan"); self.assertContains(r, "Lainnya ▾")
        self.assertContains(r, "Impor CSV / XLSX"); self.assertContains(r, "Rekap potongan · XLSX"); self.assertContains(r, '<details class="flt">')
        self.assertNotContains(r, "Reset filter"); self.assertContains(r, "Belum ada potongan untuk periode 2026-10")

    def test_filter_opens_and_reset_only_when_chosen(self):
        r = self.client.get(reverse("hrd_bpjs_deductions") + "?period=2026-10&department=%d" % self.d1.pk)
        self.assertContains(r, '<details class="flt" open>'); self.assertContains(r, "Reset filter")
        self.assertContains(self.client.get(reverse("hrd_bpjs_deductions") + "?period=2026-10&q=zzz"), "Tidak ada potongan yang cocok dengan filter")

    def test_program_from_sidebar_path_is_not_a_chosen_filter(self):
        r = self.client.get(reverse("hrd_bpjs_deductions_kes") + "?period=2026-10")
        self.assertContains(r, "Kesehatan (K)"); self.assertNotContains(r, "Reset filter"); self.assertContains(r, '<details class="flt">')
        self.assertNotContains(self.client.get(reverse("hrd_bpjs_deductions_kes") + "?period=2026-10&scheme=kes"), "Reset filter")  # kiriman ulang program bawaan jalur
        self.assertContains(self.client.get(reverse("hrd_bpjs_deductions_kes") + "?period=2026-10&scheme="), "Reset filter")  # sengaja memilih "semua program"
        self.assertContains(self.client.get(reverse("hrd_bpjs_deductions_kes") + "?period=2026-10&scheme=tk"), "Reset filter")

    def test_anomaly_chips_keep_period_and_export_links_keep_format(self):
        r = self.client.get(reverse("hrd_bpjs_deductions_kes") + "?period=2026-10")
        self.assertContains(r, "period=2026-10&amp;scheme=kes&amp;anomaly=dipotong"); self.assertContains(r, "period=2026-10&amp;scheme=kes&amp;anomaly=belum")
        self.assertContains(r, "scheme=kes&export=1&format=xlsx")

    def test_search_form_keeps_active_filters(self):
        r = self.client.get(reverse("hrd_bpjs_deductions") + "?period=2026-10&anomaly=dipotong&department=%d" % self.d1.pk)
        self.assertContains(r, '<input type="hidden" name="anomaly" value="dipotong">'); self.assertContains(r, '<input type="hidden" name="department" value="%d">' % self.d1.pk)


class BpjsStatusUxTests(HrdBase):
    def setUp(self): self.login("hrd")

    def test_hint_quick_chips_and_collapsed_filters(self):
        r = self.client.get("/hrd/bpjs/")
        self.assertContains(r, "aktif atau nonaktif"); self.assertContains(r, "Karyawan nonaktif, BPJS masih aktif"); self.assertContains(r, "K belum dicatat"); self.assertContains(r, "Kelola ›")
        self.assertContains(r, '<details class="flt">'); self.assertNotContains(r, "Reset filter")
        self.assertNotContains(r, "0001234567890")  # nomor BPJS tetap tidak tampil

    def test_default_active_employees_is_not_a_chosen_filter_but_others_are(self):
        self.assertNotContains(self.client.get("/hrd/bpjs/?emp=aktif"), "Reset filter")
        for q in ("?emp=semua", "?kes=belum", "?tk=aktif", "?q=bud", "?anomaly=1&emp=nonaktif"):
            r = self.client.get("/hrd/bpjs/" + q); self.assertContains(r, "Reset filter", msg_prefix=q); self.assertContains(r, '<details class="flt" open>', msg_prefix=q)

    def test_empty_state_with_filter_offers_reset(self):
        r = self.client.get("/hrd/bpjs/?q=tidak-ada-orang"); self.assertContains(r, "Tidak ada karyawan yang cocok dengan filter")

    def test_search_form_keeps_selected_filters(self):
        r = self.client.get("/hrd/bpjs/?emp=semua&kes=belum&q=b")
        self.assertContains(r, '<input type="hidden" name="emp" value="semua">'); self.assertContains(r, '<input type="hidden" name="kes" value="belum">')


class CateringUxTests(HrdBase):
    def setUp(self): self.login("hrd")

    def mk(self, d, received=False, meal="1200"):
        o = CateringOrder.objects.create(date=d, meal=meal, qty_large=10, qty_small=5, created_by=self.hrd)
        if received: o.received_large, o.received_small = 10, 5; o.save()
        return o

    def test_flow_primary_action_and_empty_state(self):
        r = self.client.get(reverse("hrd_catering"))
        self.assertContains(r, "Alur:"); self.assertContains(r, "+ Rekap baru"); self.assertContains(r, "Belum ada rekap pada periode ini")
        self.assertContains(r, "Semua pesanan pada periode ini sudah dicatat diterima"); self.assertNotContains(r, "Reset filter")

    def test_pending_chip_filters_unreceived_and_row_action_names_the_task(self):
        d = date.today().replace(day=1); self.mk(d, received=True, meal="0900"); self.mk(d, received=False, meal="1200")
        r = self.client.get(reverse("hrd_catering")); self.assertContains(r, "Belum dicatat diterima (1) — perlu perhatian"); self.assertContains(r, "Catat diterima</a>")
        r = self.client.get(reverse("hrd_catering") + "?only=belum"); self.assertEqual(len(r.context["page"]), 1); self.assertContains(r, "Reset filter")
        self.assertContains(r, "Belum dicatat diterima (1)")  # angka chip tidak ikut menyempit oleh filter

    def test_reset_appears_only_when_user_picked_filter(self):
        self.mk(date.today().replace(day=1))
        self.assertNotContains(self.client.get(reverse("hrd_catering")), "Reset filter")
        self.assertContains(self.client.get(reverse("hrd_catering") + "?meal=0900"), "Tidak ada rekap yang cocok dengan filter")

    def test_delete_button_keeps_danger_class_for_central_confirmation(self):
        self.mk(date.today().replace(day=1)); self.assertContains(self.client.get(reverse("hrd_catering")), 'class="sm danger"')  # putaran 35: tombol Hapus terbaca (bukan pil merah tanpa teks) dan tetap memicu konfirmasi pusat
