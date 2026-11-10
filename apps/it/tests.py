from datetime import date, timedelta
from django.test import TestCase
from django.core.exceptions import ValidationError
from apps.core.models import Role, User
from apps.hr.models import Department, Employee, ChangeRequest, Holiday
from apps.agenda import feed
from apps.agenda.models import AgendaEvent
from . import models as m


def mk(username, role, dept=None): return User.objects.create_user(username, password="x" * 12, role=role, department=dept, must_change_password=False)


class ItAccess(TestCase):
    def setUp(self):
        self.d = Department.objects.create(code="PRD", name="Produksi")
        self.it, self.hrd, self.adm = mk("it", Role.IT), mk("hrd", Role.HRD), mk("adm", Role.DEPT_ADMIN, self.d)
    def get(self, u, url): self.client.force_login(u); return self.client.get(url)
    def test_only_it_and_superadmin(self):
        for url in ("/it/", "/it/assets/", "/it/ip/", "/it/licenses/", "/it/domains/", "/it/tickets/"):
            self.assertEqual(self.get(self.it, url).status_code, 200, url)
            self.assertEqual(self.get(self.hrd, url).status_code, 403, url)
            self.assertEqual(self.get(self.adm, url).status_code, 403, url)
    def test_it_home_redirect_and_menu(self):
        self.assertEqual(self.get(self.it, "/").status_code, 302)
        r = self.get(self.it, "/it/assets/"); self.assertContains(r, "/it/ip/"); self.assertNotContains(r, "/hrd/")
        self.assertNotContains(self.get(self.hrd, "/calendar/"), "/it/assets/")
    def test_asset_crud_status_history_and_audit(self):
        self.client.force_login(self.it)
        r = self.client.post("/it/assets/new/", {"tag": "LPT-1", "name": "Laptop", "category": "laptop", "status": "stok"}); self.assertEqual(r.status_code, 302)
        a = m.Asset.objects.get(tag="LPT-1")
        self.client.post(f"/it/assets/{a.pk}/", {"status": "dipakai", "note": "ke HRD"}); a.refresh_from_db()
        self.assertEqual(a.status, "dipakai"); self.assertTrue(a.events.filter(action="status").exists())
        self.assertEqual(self.client.get(f"/it/assets/{a.pk}/").status_code, 200)
        self.assertEqual(self.client.get("/it/assets/?q=LPT&export=1&format=csv").status_code, 200)
        self.client.post(f"/it/assets/{a.pk}/hapus/"); self.assertFalse(m.Asset.objects.exists())
    def test_license_key_encrypted_and_hidden(self):
        l = m.License.objects.create(name="Office", seats=2, used=1, key="SECRET-KEY-123")
        self.assertNotContains(self.get(self.it, "/it/licenses/"), "SECRET-KEY-123")
        from django.db import connection
        with connection.cursor() as c: c.execute("select key from it_license"); self.assertNotIn("SECRET-KEY-123", c.fetchone()[0])
        with self.assertRaises(ValidationError): m.License(name="x", seats=1, used=2).full_clean()


class Ipam(TestCase):
    def test_subnet_validation_next_free_and_conflicts(self):
        s = m.Subnet(name="LAN", cidr="192.168.1.0/24", gateway="192.168.1.1"); s.full_clean(); s.save()
        self.assertEqual(s.next_free(), "192.168.1.2")
        with self.assertRaises(ValidationError): m.Subnet(name="x", cidr="192.168.1.128/25").full_clean()          # tumpang tindih
        with self.assertRaises(ValidationError): m.Subnet(name="x", cidr="192.168.1.5/24").full_clean()            # bukan alamat jaringan
        with self.assertRaises(ValidationError): m.Subnet(name="x", cidr="10.0.0.0/8").full_clean()               # terlalu besar
        ip = m.IPAddress(subnet=s, address="192.168.1.2", mac="AA-BB-CC-DD-EE-01"); ip.full_clean(); ip.save(); self.assertEqual(ip.mac, "aa:bb:cc:dd:ee:01")
        self.assertEqual(s.next_free(), "192.168.1.3")
        for bad in (dict(address="192.168.2.5"), dict(address="192.168.1.0"), dict(address="192.168.1.9", mac="aa:bb:cc:dd:ee:01"), dict(address="192.168.1.2")):
            with self.assertRaises(ValidationError): m.IPAddress(subnet=s, **bad).full_clean()
    def test_ip_page_adds_and_deletes(self):
        u = mk("it", Role.IT); s = m.Subnet.objects.create(name="LAN", cidr="10.1.0.0/29"); self.client.force_login(u)
        self.assertEqual(self.client.post(f"/it/ip/{s.pk}/", {"address": "10.1.0.1", "kind": "statis"}).status_code, 302); self.assertEqual(s.ips.count(), 1)
        self.client.post(f"/it/ip/{s.pk}/", {"del": s.ips.first().pk}); self.assertEqual(s.ips.count(), 0)


class CalendarFeed(TestCase):
    def setUp(self):
        t = date.today(); self.t = t; self.lo, self.hi = t.replace(day=1), t.replace(day=28)
        self.d1, self.d2 = Department.objects.create(code="PRD", name="Produksi"), Department.objects.create(code="GDG", name="Gudang")
        mkE = lambda nik, nm, d: Employee.objects.create(nik=nik, name=nm, department=d, join_date=date(2020, 1, 1), birth_date=date(1990, t.month, 15))
        self.e1, self.e2 = mkE("A1", "Andi", self.d1), mkE("B1", "Budi", self.d2)
        self.hrd, self.adm, self.pay, self.it = mk("hrd", Role.HRD), mk("adm", Role.DEPT_ADMIN, self.d1), mk("pay", Role.PAYROLL), mk("it", Role.IT)
        for e, st in ((self.e1, "approved"), (self.e2, "pending")):
            ChangeRequest.objects.create(type="cuti", employee=e, department=e.department, status=st, payload={"start_date": str(self.lo), "end_date": str(self.lo + timedelta(days=2))}, requested_by=self.hrd)
        Holiday.objects.create(date=self.lo + timedelta(days=4), name="Libur Uji")
        AgendaEvent.objects.create(title="Rapat HRD", kind="rapat", start_date=self.lo + timedelta(days=6), audience="role", audience_role="hrd", created_by=self.hrd)
        AgendaEvent.objects.create(title="Rapat Produksi", kind="rapat", start_date=self.lo + timedelta(days=7), audience="dept", department=self.d1, created_by=self.hrd)
        m.Domain.objects.create(name="uji.example", kind="domain", expires=self.lo + timedelta(days=10))
    def titles(self, u, **k): return [e["title"] for e in feed.build(u, self.lo, self.hi, **k)]
    def test_hrd_sees_all(self):
        T = " | ".join(self.titles(self.hrd)); [self.assertIn(w, T) for w in ("Andi · Cuti", "Budi · Cuti", "Libur Uji", "Rapat HRD", "Rapat Produksi")]; self.assertNotIn("uji.example", T)
        self.assertEqual({e["layer"] for e in feed.build(self.hrd, self.lo, self.hi) if "Andi" in e["title"] and "Cuti" in e["title"]}, {"cuti"})
        self.assertIn("ajuan", {e["layer"] for e in feed.build(self.hrd, self.lo, self.hi)})
    def test_dept_admin_only_own_department(self):
        T = " | ".join(self.titles(self.adm)); self.assertIn("Andi", T); self.assertNotIn("Budi", T); self.assertIn("Rapat Produksi", T); self.assertNotIn("Rapat HRD", T)
    def test_payroll_counts_without_names(self):
        T = self.titles(self.pay); self.assertFalse(any("Andi" in x or "Budi" in x for x in T)); self.assertTrue(any(x.startswith("1 karyawan cuti") for x in T)); self.assertIn("Libur Uji", T)
        self.assertNotIn("ajuan", {e["layer"] for e in feed.build(self.pay, self.lo, self.hi)})
    def test_it_sees_only_it_layers_and_no_people(self):
        T = " | ".join(self.titles(self.it)); self.assertIn("uji.example", T); self.assertNotIn("Andi", T); self.assertNotIn("Budi", T); self.assertNotIn("ulang tahun", T.lower())
    def test_heat_counts_distinct_people(self):
        h = feed.heat(feed.build(self.hrd, self.lo, self.hi)); self.assertEqual(h[self.lo], 1); self.assertNotIn(self.lo + timedelta(days=3), h)
    def test_pages_for_every_role(self):
        for u in (self.hrd, self.adm, self.pay, self.it):
            self.client.force_login(u)
            for q in ("", "?v=heat", f"?m={self.lo:%Y-%m}&go=1&l=libur", "?m=bukan-bulan"): self.assertEqual(self.client.get("/calendar/" + q).status_code, 200, (u.role, q))
            r = self.client.get("/calendar/export.ics"); self.assertEqual(r.status_code, 200); self.assertIn("BEGIN:VCALENDAR", r.content.decode())
    def test_event_permissions(self):
        self.client.force_login(self.adm)
        r = self.client.post("/calendar/event/new/", {"title": "Briefing", "kind": "rapat", "start_date": str(self.t)}); self.assertEqual(r.status_code, 302)
        e = AgendaEvent.objects.get(title="Briefing"); self.assertEqual((e.audience, e.department_id), ("dept", self.d1.pk))   # dipaksa ke departemennya
        other = AgendaEvent.objects.get(title="Rapat HRD"); self.assertEqual(self.client.get(f"/calendar/event/{other.pk}/").status_code, 404)
        hrd_ev = AgendaEvent.objects.get(title="Rapat Produksi"); self.assertEqual(self.client.post(f"/calendar/event/{hrd_ev.pk}/hapus/").status_code, 403)
        self.assertEqual(self.client.post(f"/calendar/event/{e.pk}/hapus/").status_code, 302)
        self.assertEqual(self.client.post("/calendar/event/new/", {"title": "x", "kind": "rapat", "start_date": str(self.t), "end_date": str(self.t - timedelta(days=1))}).status_code, 200)


class InventoryAndNet(TestCase):
    def setUp(self):
        self.d = Department.objects.create(code="PRD", name="Produksi"); self.d2 = Department.objects.create(code="GDG", name="Gudang")
        self.it, self.adm, self.hrd = mk("it", Role.IT), mk("adm", Role.DEPT_ADMIN, self.d), mk("hrd", Role.HRD)
        self.i = m.StockItem.objects.create(sku="MSE", name="Mouse", unit="pcs", min_stock=2)
    def test_stock_in_out_never_negative_and_audit(self):
        self.client.force_login(self.it); u = f"/it/stock/{self.i.pk}/"
        self.assertEqual(self.client.post(u, {"direction": "masuk", "qty": 5, "date": "2026-11-01", "party": "Toko A"}).status_code, 302)
        r = self.client.post(u, {"direction": "keluar", "qty": 9, "date": "2026-11-02", "party": "Budi", "department": self.d.pk}); self.assertEqual(r.status_code, 200); self.assertContains(r, "tidak cukup")
        self.assertEqual(self.client.post(u, {"direction": "keluar", "qty": 4, "date": "2026-11-02", "party": "Budi", "department": self.d.pk}).status_code, 302)
        self.i.refresh_from_db(); self.assertEqual(self.i.qty, 1); self.assertTrue(self.i.low)
        r = self.client.post(u, {"direction": "keluar", "qty": 1, "date": "2026-11-02", "party": "Budi"}); self.assertEqual(r.status_code, 200)   # departemen wajib
        self.assertContains(self.client.get("/it/stock/moves/"), "Budi"); self.assertContains(self.client.get("/it/stock/?low=1"), "Mouse")
        self.client.post(f"/it/stock/{self.i.pk}/hapus/"); self.assertTrue(m.StockItem.objects.filter(pk=self.i.pk).exists())   # punya riwayat → tidak terhapus
        self.assertEqual(self.client.get("/it/stock/moves/?export=1&format=csv").status_code, 200)
    def test_wish_submit_scope_flow_and_stock_receive(self):
        self.client.force_login(self.adm)
        self.assertEqual(self.client.post("/it/wishlist/ajukan/", {"title": "Monitor", "qty": 2, "priority": "sedang", "reason": "pendek"}).status_code, 200)
        self.assertEqual(self.client.post("/it/wishlist/ajukan/", {"title": "Monitor", "qty": 2, "priority": "sedang", "reason": "Layar lama pecah dan sering berkedip."}).status_code, 302)
        w = m.Wish.objects.get(); self.assertEqual(w.department_id, self.d.pk)
        m.Wish.objects.create(title="Rahasia Gudang", department=self.d2, reason="x" * 12); self.assertNotContains(self.client.get("/it/wishlist/ajukan/"), "Rahasia Gudang")
        self.assertEqual(self.client.get("/it/wishlist/").status_code, 403)
        from apps.core.models import Notification; self.assertTrue(Notification.objects.filter(user=self.it, title__contains="Monitor").exists())
        self.client.force_login(self.it); u = f"/it/wishlist/{w.pk}/"
        self.client.post(u, {"to": "diterima"}); w.refresh_from_db(); self.assertEqual(w.status, "diajukan")            # tidak boleh loncat
        self.client.post(u, {"to": "ditolak"}); w.refresh_from_db(); self.assertEqual(w.status, "diajukan")            # tolak wajib catatan
        w.item = self.i; w.save(); [self.client.post(u, {"to": s}) for s in ("disetujui", "dipesan", "diterima")]; w.refresh_from_db(); self.i.refresh_from_db()
        self.assertEqual((w.status, self.i.qty, w.received_logged), ("diterima", 2, True)); self.client.post(u, {"to": "diterima"}); self.i.refresh_from_db(); self.assertEqual(self.i.qty, 2)
        self.assertTrue(Notification.objects.filter(user=self.adm, title__contains="Monitor").exists())
    def test_help_request_creates_ticket_scoped_and_notifies(self):
        from apps.core.models import Notification
        self.client.force_login(self.adm)
        r = self.client.post("/it/wishlist/ajukan/", {"jenis": "jasa", "category": "cetak", "title": "Printer macet", "detail": "pendek", "priority": "sedang"}); self.assertEqual(r.status_code, 200)
        r = self.client.post("/it/wishlist/ajukan/", {"jenis": "jasa", "category": "cetak", "title": "Printer macet", "detail": "Printer ruang HRD tidak bisa mencetak sejak Senin.", "priority": "sedang", "location": "Lt.2", "due": "2000-01-01"}); self.assertEqual(r.status_code, 200)
        r = self.client.post("/it/wishlist/ajukan/", {"jenis": "jasa", "category": "cetak", "title": "Printer macet", "detail": "Printer ruang HRD tidak bisa mencetak sejak Senin.", "priority": "sedang", "location": "Lt.2"}); self.assertEqual(r.status_code, 302)
        t = m.Ticket.objects.get(); self.assertEqual((t.department_id, t.created_by_id, t.status), (self.d.pk, self.adm.pk, "baru"))
        m.Ticket.objects.create(title="Tiket Gudang", department=self.d2, created_by=self.hrd, detail="x" * 12)
        r = self.client.get("/it/wishlist/ajukan/"); self.assertContains(r, "Printer macet"); self.assertNotContains(r, "Tiket Gudang")
        self.assertTrue(Notification.objects.filter(user=self.it, title__contains="Printer macet").exists())
        self.client.force_login(self.it); self.client.post(f"/it/tickets/{t.pk}/", {"title": t.title, "category": "cetak", "detail": t.detail, "requester": "x", "department": self.d.pk, "priority": "sedang", "status": "selesai"})
        self.assertTrue(Notification.objects.filter(user=self.adm, title__contains="Selesai").exists())
    def test_net_groups_pages(self):
        self.client.force_login(self.it)
        m.NetDevice.objects.create(name="CCTV-1", kind="cctv"); m.NetDevice.objects.create(name="FP-1", kind="fingerprint"); m.NetDevice.objects.create(name="RTR-1", kind="router")
        for g, yes, no in (("cctv", "CCTV-1", "FP-1"), ("fingerprint", "FP-1", "RTR-1"), ("router", "RTR-1", "CCTV-1")):
            r = self.client.get(f"/it/net/{g}/"); self.assertContains(r, yes); self.assertNotContains(r, no)
        self.assertEqual(self.client.get("/it/net/").status_code, 200); self.assertEqual(self.client.get("/it/net/ngawur/").status_code, 200)
        self.assertEqual(self.client.post("/it/net/new/", {"name": "AP-1", "kind": "ap", "status": "aktif"}).status_code, 302)
        self.assertContains(self.client.get("/it/net/new/?kind=nvr"), 'value="nvr" selected')
        self.assertContains(self.client.get("/it/"), "Stok menipis"); self.assertContains(self.client.get("/it/"), "/it/net/fingerprint/")
