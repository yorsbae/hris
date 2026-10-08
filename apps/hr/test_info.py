import atexit, shutil, tempfile
from datetime import date
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from apps.core.models import AuditLog, Notification, Role, User
from .models import Announcement, AnnouncementRead, Department

MEDIA = tempfile.mkdtemp()
atexit.register(shutil.rmtree, MEDIA, ignore_errors=True)  # dibagi ke worker --parallel; jangan dihapus per kelas (lihat test_docs_import)


@override_settings(MEDIA_ROOT=MEDIA)
class InfoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d1, cls.d2 = Department.objects.create(code="A", name="Produksi"), Department.objects.create(code="B", name="Gudang")
        pw = "kata-sandi-panjang-123"
        cls.hrd = User.objects.create_user("hrd", password=pw, role=Role.HRD)
        cls.a1 = User.objects.create_user("adm1", password=pw, role=Role.DEPT_ADMIN, department=cls.d1)
        cls.a2 = User.objects.create_user("adm2", password=pw, role=Role.DEPT_ADMIN, department=cls.d2)
        cls.poli = User.objects.create_user("poli", password=pw, role=Role.POLI)

    def login(self, n): self.client.force_login(User.objects.get(username=n))

    def create(self, **kw):
        self.login("hrd")
        data = {"kind": "pengumuman", "title": "Libur", "body": "Isi\nbaris dua", **kw}
        return self.client.post("/announcements/new/", data)

    def test_only_hrd_creates(self):
        self.login("adm1"); self.assertEqual(self.client.get("/announcements/new/").status_code, 403)
        self.assertEqual(self.client.post("/announcements/new/", {"kind": "pengumuman", "title": "x", "body": "x", "all_departments": "on"}).status_code, 403)

    def test_requires_a_target(self):
        r = self.create(); self.assertEqual(r.status_code, 200); self.assertFalse(Announcement.objects.exists())

    def test_department_targeting_visibility_and_notification(self):
        r = self.create(departments=[self.d1.pk]); a = Announcement.objects.get()
        self.assertRedirects(r, f"/announcements/{a.pk}/")
        self.assertTrue(Notification.objects.filter(user=self.a1, link=f"/announcements/{a.pk}/").exists())
        self.assertFalse(Notification.objects.filter(user=self.a2).exists() or Notification.objects.filter(user=self.poli).exists())
        self.login("adm1"); self.assertEqual(self.client.get(f"/announcements/{a.pk}/").status_code, 200)
        self.login("adm2"); self.assertEqual(self.client.get(f"/announcements/{a.pk}/").status_code, 404)
        self.assertNotContains(self.client.get("/announcements/"), "Libur")
        self.login("poli"); self.assertEqual(self.client.get(f"/announcements/{a.pk}/").status_code, 404)

    def test_all_departments_reaches_dept_admins_and_poli_not_hrd(self):
        self.create(all_departments="on")
        self.assertEqual(set(Notification.objects.values_list("user__username", flat=True)), {"adm1", "adm2", "poli"})

    def test_specific_recipient(self):
        self.create(recipients=[self.poli.pk]); a = Announcement.objects.get()
        self.login("poli"); self.assertEqual(self.client.get(f"/announcements/{a.pk}/").status_code, 200)
        self.login("adm1"); self.assertEqual(self.client.get(f"/announcements/{a.pk}/").status_code, 404)

    def test_read_tracking_and_hrd_stats(self):
        self.create(all_departments="on"); a = Announcement.objects.get()
        self.login("adm1"); self.client.get(f"/announcements/{a.pk}/"); self.client.get(f"/announcements/{a.pk}/")  # idempoten
        self.assertEqual(AnnouncementRead.objects.filter(announcement=a, user=self.a1).count(), 1)
        self.assertTrue(Notification.objects.get(user=self.a1).is_read)
        self.login("hrd"); r = self.client.get(f"/announcements/{a.pk}/")
        self.assertContains(r, "1 / 3"); self.assertContains(r, "adm2"); self.assertNotContains(r, "Belum membaca:</b> adm1")
        self.login("adm1"); self.assertNotContains(self.client.get(f"/announcements/{a.pk}/"), "penerima sudah membaca")

    def test_attachment_permission_validation_and_audit(self):
        pdf = SimpleUploadedFile("peraturan.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        self.create(departments=[self.d1.pk], attachment=pdf); a = Announcement.objects.get(); self.assertTrue(a.attachment)
        self.login("adm1"); r = self.client.get(f"/announcements/{a.pk}/file/")
        self.assertEqual((r.status_code, b"".join(r.streaming_content)), (200, b"%PDF-1.4 test")); self.assertIn("attachment", r["Content-Disposition"])
        self.assertTrue(AuditLog.objects.filter(action="announcement_download").exists())
        self.login("adm2"); self.assertEqual(self.client.get(f"/announcements/{a.pk}/file/").status_code, 404)
        Announcement.objects.all().delete()
        bad = SimpleUploadedFile("x.exe", b"MZ"); self.create(all_departments="on", attachment=bad); self.assertFalse(Announcement.objects.exists())
        big = SimpleUploadedFile("b.pdf", b"0" * (5 * 1024 * 1024 + 1)); self.create(all_departments="on", attachment=big); self.assertFalse(Announcement.objects.exists())

    def test_body_is_escaped(self):
        self.create(all_departments="on", body="<script>alert(1)</script>"); a = Announcement.objects.get()
        self.assertNotContains(self.client.get(f"/announcements/{a.pk}/"), "<script>alert(1)</script>")

    def test_notification_center(self):
        self.create(all_departments="on"); a = Announcement.objects.get()
        self.login("adm1"); n = Notification.objects.get(user=self.a1)
        self.assertContains(self.client.get("/notifications/"), "Libur")
        self.assertContains(self.client.get("/"), 'class="badge" aria-label="1 belum dibaca">1</span>', html=False)
        r = self.client.post(f"/notifications/{n.pk}/open/"); self.assertRedirects(r, f"/announcements/{a.pk}/", fetch_redirect_response=False)
        n.refresh_from_db(); self.assertTrue(n.is_read)
        other = Notification.objects.get(user=self.a2)
        self.assertEqual(self.client.post(f"/notifications/{other.pk}/open/").status_code, 404)  # milik orang lain
        n2 = Notification.objects.create(user=self.a1, kind="x", title="evil", link="//evil.example")
        self.assertRedirects(self.client.post(f"/notifications/{n2.pk}/open/"), "/notifications/", fetch_redirect_response=False)
        Notification.objects.create(user=self.a1, kind="x", title="y")
        self.client.post("/notifications/read-all/"); self.assertFalse(Notification.objects.filter(user=self.a1, is_read=False).exists())
        self.assertTrue(Notification.objects.filter(user=self.a2, is_read=False).exists())  # tidak menyentuh user lain

    def test_list_filters(self):
        self.create(all_departments="on"); self.create(all_departments="on", kind="peraturan", title="Aturan Helm")
        self.login("adm1")
        r = self.client.get("/announcements/?kind=peraturan"); self.assertContains(r, "Aturan Helm"); self.assertNotContains(r, "Libur")
        a = Announcement.objects.get(title="Libur"); self.client.get(f"/announcements/{a.pk}/")
        r = self.client.get("/announcements/?unread=1"); self.assertNotContains(r, ">Libur<")
