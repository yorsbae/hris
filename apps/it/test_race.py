"""Race condition IT: keputusan tiket / permintaan barang membaca ulang status di bawah kunci baris; stok tidak masuk dobel."""
from django.test import TestCase
from apps.core.models import Role, User
from apps.hr.models import Department
from . import models as m, inv_views as iv, views as v


class DecisionRaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.it = User.objects.create_user("it1", password="x", role=Role.IT); cls.d = Department.objects.create(code="PR", name="Produksi")
        cls.item = m.StockItem.objects.create(sku="K1", name="Kabel", qty=0)
        cls.w = m.Wish.objects.create(title="Kabel LAN", qty=5, item=cls.item, department=cls.d, status="dipesan")
        cls.t = m.Ticket.objects.create(title="Printer macet", detail="x", requester="a", department=cls.d, status="baru")

    def test_receiving_wish_twice_adds_stock_once(self):
        iv.decide_wish(self.w.pk, "diterima", "", self.it)
        with self.assertRaises(ValueError): iv.decide_wish(self.w.pk, "diterima", "", self.it)   # klik kedua / staf lain: status sudah 'diterima'
        self.item.refresh_from_db(); self.assertEqual(self.item.qty, 5); self.assertEqual(m.StockMovement.objects.filter(item=self.item).count(), 1)

    def test_stale_copy_cannot_replay_transition(self):
        stale = m.Wish.objects.get(pk=self.w.pk)   # salinan lama di memori sebuah request lain: masih 'dipesan'
        iv.decide_wish(self.w.pk, "diterima", "", self.it)
        self.assertEqual(stale.status, "dipesan")
        with self.assertRaises(ValueError): iv.decide_wish(stale.pk, "diterima", "", self.it)
        self.item.refresh_from_db(); self.assertEqual(self.item.qty, 5)

    def test_wish_failure_rolls_back_status_and_stock(self):
        m.Wish.objects.filter(pk=self.w.pk).update(qty=0)   # record() menolak jumlah 0 → seluruh keputusan batal
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError): iv.decide_wish(self.w.pk, "diterima", "", self.it)
        self.w.refresh_from_db(); self.assertEqual(self.w.status, "dipesan"); self.assertFalse(self.w.received_logged)

    def test_ticket_second_decision_is_rejected(self):
        t, old = v.decide_ticket(self.t.pk, "selesai" if False else "proses", ""); self.assertEqual((old, t.status), ("baru", "proses"))
        with self.assertRaises(ValueError): v.decide_ticket(self.t.pk, "proses", "")
        v.decide_ticket(self.t.pk, "selesai", ""); self.t.refresh_from_db(); self.assertIsNotNone(self.t.resolved_at)
        with self.assertRaises(ValueError): v.decide_ticket(self.t.pk, "ditolak", "alasan")

    def test_reject_needs_note_and_views_report_error(self):
        with self.assertRaises(ValueError): v.decide_ticket(self.t.pk, "ditolak", "")
        self.client.force_login(self.it)
        self.client.post(f"/it/tickets/{self.t.pk}/", {"to": "proses"}); r = self.client.post(f"/it/tickets/{self.t.pk}/", {"to": "proses"}, follow=True)
        self.assertContains(r, "tidak diizinkan"); self.t.refresh_from_db(); self.assertEqual(self.t.status, "proses")
        self.client.post(f"/it/wishlist/{self.w.pk}/", {"to": "diterima"}); self.client.post(f"/it/wishlist/{self.w.pk}/", {"to": "diterima"})
        self.item.refresh_from_db(); self.assertEqual(self.item.qty, 5)
