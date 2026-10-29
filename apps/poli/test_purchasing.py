"""Pesan obat (dari stok habis/menipis, bisa dipilah) & terima obat (langsung menambah stok + kartu stok)."""
from datetime import date, timedelta
from django.db import IntegrityError, transaction
from apps.core.models import AuditLog
from apps.poli import purchasing, services
from apps.poli.models import GoodsReceipt, Medicine, PurchaseOrder, StockMovement
from apps.poli.test_poli import PoliBase


class PurchasingBase(PoliBase):
    def setUp(self):
        self.empty = Medicine.objects.create(code="HBS", name="Obat Habis", unit="tablet", min_stock=10)             # stok 0 → habis
        self.low = Medicine.objects.create(code="MNP", name="Obat Menipis", unit="botol", min_stock=20)
        services.stock_in(self.low.pk, 15, self.poli)                                                                  # 15 <= 20 → menipis
        self.login("poli")

    def new_order(self, sel, qtys=None, **extra):
        d = {"sel": [str(m.pk) for m in sel], **{f"qty_{m.pk}": str((qtys or {}).get(m.pk, 10)) for m in sel}, **extra}
        return self.client.post("/poli/orders/new/", d)


class OrderFormTests(PurchasingBase):
    def test_form_lists_only_low_and_empty_with_suggestions(self):
        r = self.client.get("/poli/orders/new/"); self.assertEqual(r.status_code, 200)
        names = [x["m"].code for x in r.context["rows"]]
        self.assertEqual(names[:2], ["HBS", "MNP"]); self.assertNotIn("PCT", names)                                   # PCT stok 50 > min 5: tidak ditawarkan; habis di atas menipis
        rows = {x["m"].code: x for x in r.context["rows"]}
        self.assertEqual((rows["HBS"]["state"], rows["HBS"]["suggest"]), ("habis", 20))                               # target 2×10 − 0
        self.assertEqual((rows["MNP"]["state"], rows["MNP"]["suggest"]), ("menipis", 25))                             # 2×20 − 15
        self.assertEqual(len(self.client.get("/poli/orders/new/?all=1").context["rows"]), Medicine.objects.count())

    def test_only_selected_are_ordered_and_stock_unchanged(self):
        before = Medicine.objects.get(pk=self.empty.pk).stock
        r = self.new_order([self.empty], {self.empty.pk: 30}, supplier="PT Farma", note="urgent")
        o = PurchaseOrder.objects.get(); self.assertRedirects(r, f"/poli/orders/{o.pk}/")
        self.assertEqual([(l.medicine.code, l.qty_ordered) for l in o.lines.all()], [("HBS", 30)])                  # MNP tidak dipilih → tidak ikut
        self.assertEqual(Medicine.objects.get(pk=self.empty.pk).stock, before)                                        # memesan TIDAK mengubah stok
        self.assertTrue(o.number.startswith("PO-")); self.assertEqual(o.status, "ordered")
        self.assertTrue(AuditLog.objects.filter(module="poli", action="po_create").exists())

    def test_pending_order_reduces_next_suggestion(self):
        self.new_order([self.empty], {self.empty.pk: 20})
        rows = {x["m"].code: x for x in self.client.get("/poli/orders/new/").context["rows"]}
        self.assertEqual((rows["HBS"]["pending"], rows["HBS"]["suggest"]), (20, 0))                                   # sudah dipesan cukup → tidak disarankan lagi

    def test_validation(self):
        self.assertEqual(self.client.post("/poli/orders/new/", {}).status_code, 200); self.assertEqual(PurchaseOrder.objects.count(), 0)   # tidak ada pilihan
        for bad in ("0", "-5", "abc", "", "1000001"):
            self.new_order([self.empty], {self.empty.pk: bad}); self.assertEqual(PurchaseOrder.objects.count(), 0, bad)
        self.client.post("/poli/orders/new/", {"sel": ["999999"], "qty_999999": "5"}); self.assertEqual(PurchaseOrder.objects.count(), 0)


class ReceiveTests(PurchasingBase):
    def make_order(self):
        self.new_order([self.empty, self.low], {self.empty.pk: 30, self.low.pk: 10}); return PurchaseOrder.objects.get()

    def lines(self, o): return {l.medicine.code: l for l in o.lines.select_related("medicine")}

    def receive(self, o, qtys, notes=None, done=(), **extra):
        d = {"order": o.pk, "received_on": date.today().isoformat(), "ref": "FKT-77", "supplier": "PT Farma", **extra}
        for code, q in qtys.items(): d[f"qty_{self.lines(o)[code].pk}"] = str(q)
        for code, n in (notes or {}).items(): d[f"note_{self.lines(o)[code].pk}"] = n
        for code in done: d[f"done_{self.lines(o)[code].pk}"] = "1"
        return self.client.post("/poli/receipts/new/", d)

    def test_partial_then_full_updates_stock_card_and_status(self):
        o = self.make_order()
        r = self.receive(o, {"HBS": 12, "MNP": 0}); g = GoodsReceipt.objects.get(); self.assertRedirects(r, f"/poli/receipts/{g.pk}/")
        self.empty.refresh_from_db(); self.assertEqual(self.empty.stock, 12)                                          # stok langsung bertambah
        mv = StockMovement.objects.filter(medicine=self.empty).first()
        self.assertEqual((mv.qty, mv.balance_after, mv.reason, mv.ref), (12, 12, "purchase", g.number))               # kartu stok tercatat, ref = nomor penerimaan
        self.assertEqual(StockMovement.objects.filter(medicine=self.low).count(), 1)                                   # MNP qty 0: tidak ada mutasi baru (hanya stok awal)
        o.refresh_from_db(); self.assertEqual(o.status, "partial")
        self.receive(o, {"HBS": 18, "MNP": 10}); o.refresh_from_db(); self.assertEqual(o.status, "received")
        self.empty.refresh_from_db(); self.low.refresh_from_db(); self.assertEqual((self.empty.stock, self.low.stock), (30, 25))
        self.assertEqual(self.receive(o, {"HBS": 1}).status_code, 302); self.empty.refresh_from_db(); self.assertEqual(self.empty.stock, 30)   # pesanan selesai: ditolak

    def test_all_zero_future_date_and_bad_qty_rejected_atomically(self):
        o = self.make_order()
        self.receive(o, {"HBS": 0, "MNP": 0}); self.receive(o, {"HBS": 5}, received_on=(date.today() + timedelta(days=2)).isoformat())
        self.receive(o, {"HBS": "x"}); self.receive(o, {"HBS": 5, "MNP": -1})
        self.assertEqual(GoodsReceipt.objects.count(), 0); self.empty.refresh_from_db(); self.assertEqual(self.empty.stock, 0)

    def test_direct_receipt_without_order(self):
        r = self.client.post("/poli/receipts/new/", {"received_on": date.today().isoformat(), "rows": "3", "med_0": self.empty.pk, "mqty_0": "7", "med_1": self.low.pk, "mqty_1": "5", "med_2": "", "mqty_2": "", "ref": "HIBAH"})
        g = GoodsReceipt.objects.get(); self.assertIsNone(g.order); self.assertRedirects(r, f"/poli/receipts/{g.pk}/")
        self.empty.refresh_from_db(); self.low.refresh_from_db(); self.assertEqual((self.empty.stock, self.low.stock), (7, 20))
        self.client.post("/poli/receipts/new/", {"received_on": date.today().isoformat(), "rows": "1", "med_0": self.empty.pk, "mqty_0": ""}); self.assertEqual(GoodsReceipt.objects.count(), 1)

    def test_cannot_receive_a_line_of_another_order(self):
        o = self.make_order(); other = purchasing.create_order(self.poli, [(self.empty.pk, 5)])
        with self.assertRaises(ValueError): purchasing.receive(self.poli, [(self.empty.pk, 1, self.lines(o)["HBS"].pk, "", False)], date.today(), order=other)
        self.assertEqual(GoodsReceipt.objects.count(), 0)

    def test_cancel_and_close_rules(self):
        o = self.make_order(); self.receive(o, {"HBS": 5}); self.client.post(f"/poli/orders/{o.pk}/cancel/"); o.refresh_from_db(); self.assertEqual(o.status, "partial")  # sudah ada barang: tak bisa batal
        self.client.post(f"/poli/orders/{o.pk}/close/", {"note": ""}); o.refresh_from_db(); self.assertEqual(o.status, "partial")                                           # alasan wajib
        self.client.post(f"/poli/orders/{o.pk}/close/", {"note": "pemasok kehabisan"}); o.refresh_from_db(); self.assertEqual(o.status, "closed")
        self.assertEqual(self.receive(o, {"HBS": 1}).status_code, 302); self.assertEqual(GoodsReceipt.objects.count(), 1)
        o2 = purchasing.create_order(self.poli, [(self.low.pk, 3)]); self.client.post(f"/poli/orders/{o2.pk}/cancel/"); o2.refresh_from_db(); self.assertEqual(o2.status, "cancelled")

    def test_records_cannot_be_deleted(self):
        o = self.make_order(); self.receive(o, {"HBS": 5}); g = GoodsReceipt.objects.get()
        with self.assertRaises(PermissionError): g.delete()
        with self.assertRaises(PermissionError): o.delete()


class AccessAndPagesTests(PurchasingBase):
    URLS = ["/poli/orders/", "/poli/orders/new/", "/poli/receipts/", "/poli/receipts/new/"]

    def test_poli_and_superadmin_ok_others_forbidden(self):
        for u in ("poli", "su"):
            self.login(u)
            for url in self.URLS: self.assertEqual(self.client.get(url).status_code, 200, (u, url))
        for u in ("hrd", "adm"):
            self.login(u)
            for url in self.URLS: self.assertEqual(self.client.get(url).status_code, 403, (u, url))
            self.assertEqual(self.client.post("/poli/orders/new/", {"sel": [self.empty.pk], f"qty_{self.empty.pk}": "5"}).status_code, 403)
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_detail_export_hub_and_list_pages(self):
        self.new_order([self.empty]); o = PurchaseOrder.objects.get()
        for url in (f"/poli/orders/{o.pk}/", f"/poli/orders/{o.pk}/?export=1&format=csv", f"/poli/receipts/new/?order={o.pk}", "/poli/orders/?status=open", "/poli/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get(f"/poli/orders/{o.pk}/"), "Giliran Anda: terima barang"); self.assertContains(self.client.get("/poli/"), "pesanan menunggu barang"); self.assertContains(self.client.get("/poli/medicines/"), "Pesan obat")


class ShortfallTests(PurchasingBase):
    """Pesan 8 datang 4; ada yang tidak datang; semuanya dengan keterangan."""
    lines = ReceiveTests.lines; receive = ReceiveTests.receive
    def test_ordered_8_arrived_4_rest_waits_with_note(self):
        o = purchasing.create_order(self.poli, [(self.empty.pk, 8), (self.low.pk, 6)])
        self.receive(o, {"HBS": 4, "MNP": ""}, notes={"HBS": "sisa 4 menyusul Senin", "MNP": "belum dikirim"})
        l = self.lines(o); self.empty.refresh_from_db(); self.assertEqual(self.empty.stock, 4)                          # hanya yang datang yang masuk stok
        self.assertEqual((l["HBS"].qty_received, l["HBS"].outstanding, l["HBS"].gap, l["HBS"].state), (4, 4, 4, "menunggu"))
        self.assertEqual(l["HBS"].shortfall_note, "sisa 4 menyusul Senin"); self.assertEqual(l["MNP"].shortfall_note, "belum dikirim")
        self.assertEqual(l["MNP"].state, "menunggu"); o.refresh_from_db(); self.assertEqual(o.status, "partial")
        gl = GoodsReceipt.objects.get().lines.get(); self.assertEqual((gl.qty, gl.note), (4, "sisa 4 menyusul Senin"))
        self.assertIn("sisa 4 menyusul Senin", StockMovement.objects.filter(medicine=self.empty).first().note)            # keterangan juga ada di kartu stok

    def test_rest_will_not_come_requires_reason_and_closes_line(self):
        o = purchasing.create_order(self.poli, [(self.empty.pk, 8), (self.low.pk, 6)])
        self.receive(o, {"HBS": 4}, done=["HBS"]); self.assertEqual(GoodsReceipt.objects.count(), 0)                    # tanpa alasan: ditolak, stok tak berubah
        self.receive(o, {"HBS": 4}, notes={"HBS": "pemasok kehabisan"}, done=["HBS"])
        l = self.lines(o); self.assertEqual((l["HBS"].closed, l["HBS"].outstanding, l["HBS"].state), (True, 0, "kurang_final"))
        o.refresh_from_db(); self.assertEqual(o.status, "partial")                                                       # MNP masih ditunggu
        self.receive(o, {"MNP": 6}); o.refresh_from_db(); self.assertEqual(o.status, "closed")                          # semua beres, ada selisih final → "Ditutup"
        self.assertEqual(self.client.get(f"/poli/orders/{o.pk}/").status_code, 200)

    def test_line_that_never_arrived_no_stock_movement(self):
        o = purchasing.create_order(self.poli, [(self.empty.pk, 8), (self.low.pk, 6)])
        n = StockMovement.objects.count()
        r = self.receive(o, {"HBS": ""}, notes={"HBS": "tidak tersedia di pemasok"}, done=["HBS"])
        self.assertRedirects(r, f"/poli/orders/{o.pk}/"); self.assertEqual(GoodsReceipt.objects.count(), 0); self.assertEqual(StockMovement.objects.count(), n)   # tanpa barang masuk
        l = self.lines(o); self.assertEqual((l["HBS"].state, l["HBS"].shortfall_note), ("tidak_datang", "tidak tersedia di pemasok"))
        self.receive(o, {"MNP": 6}); o.refresh_from_db(); self.assertEqual(o.status, "closed")
        self.empty.refresh_from_db(); self.assertEqual(self.empty.stock, 0)

    def test_cannot_mark_not_coming_when_already_complete_and_over_receipt_flagged(self):
        o = purchasing.create_order(self.poli, [(self.empty.pk, 8)])
        self.receive(o, {"HBS": 8}, notes={"HBS": "x"}, done=["HBS"]); self.assertEqual(GoodsReceipt.objects.count(), 0)
        self.receive(o, {"HBS": 10}, notes={"HBS": "bonus 2"}); l = self.lines(o)["HBS"]
        self.assertEqual((l.state, l.gap), ("lebih", -2)); o.refresh_from_db(); self.assertEqual(o.status, "received")

    def test_close_whole_order_marks_lines_with_reason_and_export_has_gap(self):
        o = purchasing.create_order(self.poli, [(self.empty.pk, 8)]); self.receive(o, {"HBS": 3})
        self.client.post(f"/poli/orders/{o.pk}/close/", {"note": "pemasok berhenti"}); l = self.lines(o)["HBS"]
        self.assertEqual((l.closed, l.outstanding, l.shortfall_note), (True, 0, "pemasok berhenti"))
        r = self.client.get(f"/poli/orders/{o.pk}/?export=1&format=csv"); self.assertContains(r, "selisih"); self.assertContains(r, "pemasok berhenti")
