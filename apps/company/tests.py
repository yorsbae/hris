"""Tes Company Profile & Rekrutmen (putaran 48): situs publik (id/en), keamanan formulir & unggahan, alur lamaran, backend HRD (RBAC, CRUD, audit), retensi."""
import io
import re
import tempfile
from datetime import timedelta
from pathlib import Path
from cryptography.fernet import Fernet
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog, Notification, Role, User
from apps.hr.models import Department
from . import careers, safe_upload
from .forms import stamp
from .models import (Application, ApplicationEvent, Article, ContactMessage, Faq, Job, Page, Product, ProductCategory, SiteSetting, Stage, StoreLocation, hash_email)
from .templatetags.site_tags import rich

KEY = Fernet.generate_key().decode()
TMP = tempfile.mkdtemp()
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def ov(public=False, **k):
    if public: k["ROOT_URLCONF"] = "config.urls_public"   # situs publik sebenarnya (SITE_MODE=public): di akar, tanpa rute internal
    return override_settings(FIELD_ENCRYPTION_KEY=KEY, MEDIA_ROOT=TMP, PRIVATE_MEDIA_ROOT=TMP + "/priv", PUBLIC_BASE_URL="https://wadimor.test", CAREER_MIN_FILL_SECONDS=0, **k)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        d = Department.objects.create(code="PRD", name="Produksi")
        cls.hrd = User.objects.create_user("hrd", password="Pass#12345x", role=Role.HRD)
        cls.adm = User.objects.create_user("adm", password="Pass#12345x", role=Role.DEPT_ADMIN, department=d)
        cls.poli = User.objects.create_user("poli", password="Pass#12345x", role=Role.POLI)
        cls.sa = User.objects.create_user("sa", password="Pass#12345x", role=Role.SUPERADMIN)

    def login(self, name): self.client.login(username=name, password="Pass#12345x")

    def job(self, **k):
        d = dict(title="Operator Tenun", status="open", summary="Menenun sarung", questions=[{"label": "Pernah kerja di pabrik?", "required": True}])
        d.update(k); return Job.objects.create(**d)

    def apply_data(self, job, **k):
        d = {"full_name": "Budi Santoso", "email": "budi@example.com", "phone": "0812 3456 7890", "birth_date": "2000-05-04", "city": "Batang", "education": "sma",
             "institution": "SMK 1", "experience_years": 2, "last_position": "Operator", "cover_letter": "Saya rajin", "consent": "on", "q_0": "Ya", "website": "",
             "ts": stamp(), "idem": "a" * 32, "cv": SimpleUploadedFile("cv.pdf", PDF, content_type="application/pdf")}
        d.update(k); return d


@ov(public=True)
class PublicSiteTests(Base):
    def setUp(self):
        SiteSetting.get()
        self.cat = ProductCategory.objects.create(name_id="Sarung Tenun", name_en="Woven Sarong")
        self.p = Product.objects.create(name="Sarung Wadimor Emas", category=self.cat, status="published", featured=True, summary_id="Halus", summary_en="Smooth", price=150000)
        self.draft = Product.objects.create(name="Rahasia Draf", status="draft")
        Page.objects.create(slug="tentang-kami", title_id="Tentang Sarung", title_en="About Sarong", body_id="Sarung adalah **kain**.", status="published")
        Article.objects.create(title_id="Sejarah Sarung", title_en="Sarong History", body_id="Isi", status="published", published_at=timezone.now())
        StoreLocation.objects.create(name="Toko Pusat", city="Batang", address="Jl. A")
        Faq.objects.create(question_id="Bahan?", answer_id="Katun")

    def test_main_pages_render_id_and_en(self):
        for ns, pre in (("id", ""), ("en", "/en")):
            for name in ("home", "products", "about", "stores", "articles", "archive", "faq", "contact", "careers"):
                r = self.client.get(reverse(f"{ns}:{name}")); self.assertEqual(r.status_code, 200, f"{ns}:{name}")
            self.assertContains(self.client.get(reverse(f"{ns}:home")), 'lang="%s"' % ns)
        self.assertContains(self.client.get("/en/produk/"), "Smooth")
        self.assertContains(self.client.get("/produk/"), "Halus")

    def test_draft_hidden_publicly_visible_in_preview_only_for_hrd(self):
        self.assertNotContains(self.client.get("/produk/"), "Rahasia Draf")
        self.assertEqual(self.client.get(f"/produk/{self.draft.slug}/").status_code, 404)
        with self.settings(ROOT_URLCONF="config.urls"):   # pratinjau di aplikasi internal (/situs/)
            self.login("adm"); self.assertEqual(self.client.get(f"/situs/produk/{self.draft.slug}/").status_code, 404)   # Admin Dept tidak melihat draf
            self.client.logout(); self.login("hrd")
            r = self.client.get("/situs/produk/"); self.assertContains(r, "Rahasia Draf"); self.assertContains(r, "PRATINJAU")
            self.assertIn("noindex", r["X-Robots-Tag"])

    def test_security_headers_and_cache(self):
        r = self.client.get("/")
        self.assertNotIn("unsafe-inline", r["Content-Security-Policy"]); self.assertIn("public", r["Cache-Control"])
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("<style", self.client.get("/").content.decode()); self.assertNotIn(" style=", self.client.get("/").content.decode().split("<main")[0])
        r = self.client.get("/kontak/"); self.assertIn("no-store", r["Cache-Control"])

    def test_rich_text_escapes_html(self):
        out = rich("## Judul\n<script>alert(1)</script>\n\n- satu\n- **dua**\n\npar <b>x</b>")
        self.assertNotIn("<script>", out); self.assertNotIn("<b>", out); self.assertIn("&lt;script&gt;", out)
        self.assertIn("<h3>Judul</h3>", out); self.assertIn("<li><strong>dua</strong></li>", out)

    def test_banner_and_search_and_archive(self):
        s = SiteSetting.get(); s.banner_active = True; s.banner_id = "Website Dalam Pengembangan"; s.save()
        self.assertContains(self.client.get("/"), "Website Dalam Pengembangan")
        r = self.client.get("/cari/?q=Wadimor"); self.assertContains(r, "Sarung Wadimor Emas"); self.assertIn("noindex", r["X-Robots-Tag"])
        self.assertNotContains(self.client.get("/cari/?q=Rahasia"), "Rahasia Draf")
        y, m = timezone.now().year, timezone.now().month
        self.assertContains(self.client.get(f"/artikel/?y={y}&m={m}"), "Sejarah Sarung")
        self.assertNotContains(self.client.get(f"/artikel/?y={y - 5}&m=1"), "Sejarah Sarung")
        self.assertEqual(self.client.get("/artikel/?y=abc&m=zz").status_code, 200)

    def test_search_injection_chars_are_safe(self):
        r = self.client.get("/cari/", {"q": "<script>alert(1)</script>"}); self.assertNotContains(r, "<script>alert")

    def test_sitemap_robots_media(self):
        self.assertIn("/produk/%s/" % self.p.slug, self.client.get("/sitemap.xml").content.decode())
        self.assertNotIn(self.draft.slug, self.client.get("/sitemap.xml").content.decode())
        self.assertIn("Disallow: /karir/status/", self.client.get("/robots.txt").content.decode())
        self.assertEqual(self.client.get("/media/company/../../etc/passwd").status_code, 404)
        self.assertEqual(self.client.get("/media/other/x.png").status_code, 404)
        self.assertEqual(self.client.get("/healthz").content, b"ok")

    def test_media_serves_only_company_images(self):
        d = Path(TMP) / "company" / "product"; d.mkdir(parents=True, exist_ok=True)
        (d / "a.png").write_bytes(PNG); (d / "a.html").write_text("<script>1</script>")
        r = self.client.get("/media/company/product/a.png"); self.assertEqual(r.status_code, 200); self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.client.get("/media/company/product/a.html").status_code, 404)

    def test_contact_form(self):
        url = "/kontak/"
        data = {"name": "Ani", "email": "ani@example.com", "phone": "0812345678", "subject": "Tanya", "message": "Apakah ada grosir?", "website": "", "ts": stamp()}
        r = self.client.post(url, data); self.assertRedirects(r, "/kontak/terkirim/")
        m = ContactMessage.objects.get(); self.assertEqual(m.message, "Apakah ada grosir?")
        from django.db import connection
        with connection.cursor() as c: c.execute("SELECT email, message FROM company_contactmessage"); raw = c.fetchone()
        self.assertTrue(raw[0].startswith("enc1:") and raw[1].startswith("enc1:"))   # terenkripsi di database
        self.assertTrue(Notification.objects.filter(user=self.hrd, title__contains="Pesan baru").exists())
        for bad in ({"website": "http://spam"}, {"ts": "bukan-cap"}, {"message": "pendek"}, {"email": "x"}):
            self.assertEqual(self.client.post(url, {**data, **bad}).status_code, 200); 
        self.assertEqual(ContactMessage.objects.count(), 1)

    def test_contact_too_fast_rejected(self):
        with self.settings(CAREER_MIN_FILL_SECONDS=60):
            r = self.client.post("/kontak/", {"name": "A", "email": "a@b.co", "message": "halo halo halo", "website": "", "ts": stamp()})
        self.assertEqual(r.status_code, 200); self.assertEqual(ContactMessage.objects.count(), 0)

    def test_throttle(self):
        with self.settings(PUBLIC_THROTTLE={"contact": (2, 60)}):
            for i in range(2): self.client.post("/kontak/", {"website": "x"})
            self.assertEqual(self.client.post("/kontak/", {"website": "x"}).status_code, 429)


@ov(public=True)
class ApplyFlowTests(Base):
    def setUp(self): self.j = self.job()

    def post(self, **k):
        return self.client.post(f"/karir/{self.j.slug}/lamar/", self.apply_data(self.j, **k))

    def token_from_mail(self):
        m = re.search(r"https://wadimor\.test(/(?:en/)?karir/status/[\w-]+/)", mail.outbox[-1].body); self.assertTrue(m, mail.outbox[-1].body); return m.group(1)

    def test_apply_verify_and_track(self):
        r = self.post(); self.assertRedirects(r, "/karir/terkirim/")
        a = Application.objects.get(); self.assertEqual(a.stage, Stage.UNVERIFIED); self.assertTrue(a.ref.startswith("LMR-"))
        self.assertEqual(len(mail.outbox), 1); self.assertIn(a.ref, mail.outbox[0].body); self.assertEqual(mail.outbox[0].to, ["budi@example.com"])
        self.assertFalse(Notification.objects.filter(title__contains="Lamaran baru").exists())   # HRD belum diberi tahu sebelum email terbukti
        url = self.token_from_mail()
        r = self.client.get(url); self.assertContains(r, a.ref); self.assertContains(r, "Diterima"); self.assertEqual(r["Referrer-Policy"], "no-referrer")
        a.refresh_from_db(); self.assertEqual(a.stage, Stage.NEW); self.assertIsNotNone(a.verified_at)
        self.assertTrue(Notification.objects.filter(user=self.hrd, title__contains="Lamaran baru").exists())
        self.assertEqual(ApplicationEvent.objects.filter(application=a, kind="verified").count(), 1)
        self.assertEqual(self.client.get(url).status_code, 200); self.assertEqual(ApplicationEvent.objects.filter(application=a, kind="verified").count(), 1)  # idempoten

    def test_personal_data_encrypted_and_cv_private(self):
        self.post(); a = Application.objects.get()
        from django.db import connection
        with connection.cursor() as c: c.execute("SELECT email, phone, birth_date, cover_letter, answers, token_enc FROM company_application"); row = c.fetchone()
        self.assertTrue(all(v.startswith("enc1:") for v in row), row)
        p = Path(TMP, "priv", a.cv_path); self.assertTrue(p.exists()); self.assertNotIn(b"%PDF", p.read_bytes())   # berkas terenkripsi
        self.assertEqual(safe_upload.read_private(a.cv_path), PDF)
        self.assertEqual(self.client.get("/media/" + a.cv_path).status_code, 404)

    def test_idempotent_double_submit_and_duplicate(self):
        self.post(); self.post()
        self.assertEqual(Application.objects.count(), 1)
        self.post(idem="b" * 32)   # email sama, lowongan sama → tidak ada baris baru
        self.assertEqual(Application.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 2)   # verifikasi + tautan ulang; kirim ganda (idem sama) tidak mengirim email lagi

    def test_validation_errors(self):
        for bad, field in (({"consent": ""}, "consent"), ({"email": "x"}, "email"), ({"phone": "abc"}, "phone"), ({"birth_date": "2024-01-01"}, "birth_date"),
                           ({"q_0": ""}, "q_0"), ({"idem": "zz"}, "idem"), ({"website": "bot"}, None), ({"ts": "x"}, None)):
            r = self.post(**bad); self.assertEqual(r.status_code, 200, bad)
            if field: self.assertTrue(r.context["form"].errors.get(field), bad)
        self.assertEqual(Application.objects.count(), 0)

    def test_upload_rules(self):
        for name, data in (("cv.exe", b"MZ" + b"0" * 50), ("cv.pdf", b"<html>bukan pdf"), ("cv.pdf", PDF + b"/JavaScript"), ("cv.pdf", b""),
                           ("cv.docx", b"PK\x03\x04" + b"0" * 30), ("cv.svg", b"<svg onload=alert(1)>"), ("cv.pdf", b"%PDF-" + b"0" * (4 * 1024 * 1024))):
            r = self.post(cv=SimpleUploadedFile(name, data)); self.assertEqual(r.status_code, 200, name); self.assertTrue(r.context["form"].errors.get("cv"), name)
        self.assertEqual(Application.objects.count(), 0)
        self.assertEqual(self.post(cv=SimpleUploadedFile("foto.PNG", PNG), idem="c" * 32).status_code, 302)

    def test_closed_or_draft_job(self):
        self.j.status = "closed"; self.j.save()
        self.assertEqual(self.post().status_code, 302); self.assertEqual(Application.objects.count(), 0)
        d = self.job(title="Rahasia", status="draft"); self.assertEqual(self.client.get(f"/karir/{d.slug}/").status_code, 404)
        self.j.status = "open"; self.j.closes_on = timezone.localdate() - timedelta(days=1); self.j.save()
        self.assertNotContains(self.client.get("/karir/"), "Operator Tenun")

    def test_status_token_isolated_and_unknown_404(self):
        self.post(); self.assertEqual(self.client.get("/karir/status/salah-token/").status_code, 404)
        self.assertEqual(self.client.get("/karir/status/" + "x" * 300 + "/").status_code, 404)

    def test_lookup_does_not_leak_existence(self):
        self.post(); mail.outbox.clear()
        a = self.client.post("/karir/lupa-tautan/", {"email": "budi@example.com", "website": ""}); b = self.client.post("/karir/lupa-tautan/", {"email": "tak-ada@example.com", "website": ""})
        self.assertEqual(a.content.count(b"Bila email itu"), b.content.count(b"Bila email itu")); self.assertEqual(len(mail.outbox), 1)

    def test_withdraw_delete_and_export(self):
        self.post(); a = Application.objects.get(); url = self.token_from_mail(); self.client.get(url)
        r = self.client.get(url + "data.json"); self.assertEqual(r.json()["name"], "Budi Santoso"); self.assertIn("attachment", r["Content-Disposition"])
        self.client.post(url, {"action": "withdraw"}); a.refresh_from_db(); self.assertEqual(a.stage, Stage.WITHDRAWN)
        self.client.post(url, {"action": "delete"}); a.refresh_from_db(); self.assertIsNone(a.anonymized_at)    # tanpa centang konfirmasi → tidak menghapus
        self.client.post(url, {"action": "delete", "confirm": "1"}); a.refresh_from_db()
        self.assertIsNotNone(a.anonymized_at); self.assertEqual((a.email, a.phone, a.cv_path), ("", "", "")); self.assertFalse(list(Path(TMP, "priv", "career").glob("*.bin")) and Path(TMP, "priv", a.cv_path or "x").exists())
        self.assertEqual(self.client.get(url).status_code, 404)   # tautan mati setelah data dihapus

    def test_mail_failure_does_not_lose_application(self):
        with self.settings(EMAIL_BACKEND="apps.company.tests.BrokenBackend"):
            self.post()
        a = Application.objects.get(); self.assertTrue(a.events.filter(kind="mail_failed").exists())

    def test_clamav_unavailable_blocks_when_configured(self):
        with self.settings(CLAMAV_ADDRESS="tcp:127.0.0.1:1"):
            r = self.post(); self.assertEqual(r.status_code, 200)
        self.assertEqual(Application.objects.count(), 0)

    def test_email_daily_cap(self):
        for i in range(3):
            Application.objects.create(ref=f"R{i}", job=self.j, full_name="x", email="x", email_hash=hash_email("budi@example.com"), consent_at=timezone.now(), token_hash="t%d" % i,
                                        idem_key="k%d" % i, retain_until=timezone.localdate())
        r = self.post(idem="d" * 32); self.assertEqual(r.status_code, 200); self.assertEqual(Application.objects.filter(idem_key="d" * 32).count(), 0)

    def test_english_flow_sends_english_email(self):
        self.client.post(f"/en/karir/{self.j.slug}/lamar/", self.apply_data(self.j))
        self.assertIn("Thank you for applying", mail.outbox[0].body); self.assertIn("/en/karir/status/", mail.outbox[0].body)

    def test_jobposting_jsonld_only_when_open(self):
        self.assertContains(self.client.get(f"/karir/{self.j.slug}/"), "JobPosting")
        self.j.status = "closed"; self.j.save(); self.assertNotContains(self.client.get(f"/karir/{self.j.slug}/"), "JobPosting")

    def test_preview_never_creates_application(self):
        with self.settings(ROOT_URLCONF="config.urls"):
            self.login("hrd"); self.client.post(f"/situs/karir/{self.j.slug}/lamar/", self.apply_data(self.j)); self.assertEqual(Application.objects.count(), 0)


from django.core.mail.backends.base import BaseEmailBackend  # noqa: E402


class BrokenBackend(BaseEmailBackend):
    def send_messages(self, messages): raise OSError("smtp down")


@ov()
class BackendTests(Base):
    def setUp(self):
        self.j = self.job()
        self.client.post(f"/situs/karir/{self.j.slug}/lamar/", self.apply_data(self.j))   # pratinjau tidak membuat lamaran → buat lewat urlconf publik
        with self.settings(ROOT_URLCONF="config.urls_public"):
            self.client.post(f"/karir/{self.j.slug}/lamar/", self.apply_data(self.j)); self.a = Application.objects.get()
            m = re.search(r"(/karir/status/[\w-]+/)", mail.outbox[-1].body); self.client.get(m.group(1)); self.a.refresh_from_db()
        self.client.logout()

    def urls(self):
        return ["/company/", "/company/settings/", "/company/products/", "/company/products/new/", "/company/articles/", "/company/pages/", "/company/stores/", "/company/faqs/",
                "/company/categories/", "/company/messages/", "/recruitment/", "/recruitment/jobs/", "/recruitment/jobs/new/", f"/recruitment/jobs/{self.j.pk}/", "/recruitment/applicants/",
                f"/recruitment/applicants/{self.a.pk}/", f"/recruitment/applicants/{self.a.pk}/cv/", f"/recruitment/applicants/{self.a.pk}/export/"]

    def test_anonymous_redirected_and_other_roles_forbidden(self):
        for u in self.urls(): self.assertEqual(self.client.get(u).status_code, 302, u)
        for who in ("adm", "poli"):
            self.login(who)
            for u in self.urls(): self.assertEqual(self.client.get(u).status_code, 403, f"{who} {u}")
            for u in (f"/recruitment/applicants/{self.a.pk}/move/", f"/recruitment/applicants/{self.a.pk}/anonymize/", "/recruitment/applicants/bulk/", "/company/products/new/"):
                self.assertEqual(self.client.post(u, {"to": "interview"}).status_code, 403, f"{who} {u}")
            self.client.logout()
        self.a.refresh_from_db(); self.assertIsNone(self.a.anonymized_at); self.assertEqual(self.a.stage, Stage.NEW)

    def test_hrd_and_superadmin_pages_ok(self):
        for who in ("hrd", "sa"):
            self.login(who)
            for u in self.urls():
                if u.endswith("/cv/"): continue
                self.assertEqual(self.client.get(u).status_code, 200, f"{who} {u}")
            self.client.logout()

    def test_sidebar_menu_for_hrd_only(self):
        self.login("hrd"); self.assertContains(self.client.get("/"), "/company/products/")
        self.client.logout(); self.login("adm"); self.assertNotContains(self.client.get("/"), "/company/")

    def test_move_stage_notifies_applicant_and_audits(self):
        self.login("hrd"); mail.outbox.clear()
        r = self.client.post(f"/recruitment/applicants/{self.a.pk}/move/", {"to": "interview", "public_message": "Datang Senin 09.00 ya", "note": "bagus"})
        self.assertRedirects(r, f"/recruitment/applicants/{self.a.pk}/"); self.a.refresh_from_db(); self.assertEqual(self.a.stage, Stage.INTERVIEW)
        self.assertEqual(len(mail.outbox), 1); self.assertIn("Datang Senin 09.00 ya", mail.outbox[0].body)
        ev = self.a.events.filter(kind="stage").last(); self.assertEqual((ev.from_stage, ev.to_stage, ev.by), ("new", "interview", self.hrd))
        log = AuditLog.objects.filter(module="recruitment", action="move").last(); self.assertEqual(log.after, {"stage": "interview"})
        page = self.client.get(f"/recruitment/applicants/{self.a.pk}/"); self.assertContains(page, "Wawancara")
        self.client.logout()
        # pelamar melihat pesan publik tetapi bukan catatan internal
        m = re.search(r"(/karir/status/[\w-]+/)", mail.outbox[0].body)
        with self.settings(ROOT_URLCONF="config.urls_public"): pub = self.client.get(m.group(1))
        self.assertContains(pub, "Datang Senin 09.00 ya"); self.assertNotContains(pub, "bagus")

    def test_move_rules(self):
        self.login("hrd")
        for to in ("unverified", "withdrawn", "ngawur"): self.client.post(f"/recruitment/applicants/{self.a.pk}/move/", {"to": to}); self.a.refresh_from_db(); self.assertEqual(self.a.stage, Stage.NEW)

    def test_events_are_append_only(self):
        e = self.a.events.first()
        with self.assertRaises(PermissionError): e.delete()
        e.note = "x"
        with self.assertRaises(PermissionError): e.save()
        with self.assertRaises(PermissionError): self.a.delete()

    def test_bulk_move_and_cap(self):
        self.login("hrd")
        self.client.post("/recruitment/applicants/bulk/", {"ids": [self.a.pk], "to": "rejected", "public_message": "Terima kasih"}); self.a.refresh_from_db(); self.assertEqual(self.a.stage, Stage.REJECTED)
        r = self.client.post("/recruitment/applicants/bulk/", {"ids": [self.a.pk], "to": "unverified", "next": "https://evil.example/"}); self.assertRedirects(r, "/recruitment/applicants/", fetch_redirect_response=False)

    def test_cv_download_is_attachment_and_logged(self):
        self.login("hrd"); r = self.client.get(f"/recruitment/applicants/{self.a.pk}/cv/")
        self.assertEqual(r.content, PDF); self.assertIn("attachment", r["Content-Disposition"]); self.assertEqual(r["X-Content-Type-Options"], "nosniff"); self.assertIn("no-store", r["Cache-Control"])
        self.assertTrue(self.a.events.filter(kind="download", by=self.hrd).exists()); self.assertTrue(AuditLog.objects.filter(module="recruitment", action="download").exists())

    def test_audit_never_contains_personal_data(self):
        self.login("hrd"); self.client.get(f"/recruitment/applicants/{self.a.pk}/"); self.client.get(f"/recruitment/applicants/{self.a.pk}/export/")
        blob = " ".join(f"{l.before} {l.after}" for l in AuditLog.objects.filter(module="recruitment"))
        for secret in ("budi@example.com", "Budi Santoso", "0812"): self.assertNotIn(secret, blob)

    def test_anonymize_by_hrd(self):
        self.login("hrd"); self.client.post(f"/recruitment/applicants/{self.a.pk}/anonymize/", {"reason": "x"}); self.a.refresh_from_db()
        self.assertIsNotNone(self.a.anonymized_at); self.assertEqual(self.client.get(f"/recruitment/applicants/{self.a.pk}/cv/").status_code, 404)
        self.assertEqual(self.client.post(f"/recruitment/applicants/{self.a.pk}/move/", {"to": "hired"}).status_code, 302); self.a.refresh_from_db(); self.assertNotEqual(self.a.stage, Stage.HIRED)

    def test_product_crud_with_image_and_cleanup(self):
        self.login("hrd")
        r = self.client.post("/company/products/new/", {"name": "Sarung Baru", "summary_id": "x", "status": "published", "order": 100, "show_price": "", "image": SimpleUploadedFile("a.png", PNG)})
        p = Product.objects.get(name="Sarung Baru"); self.assertRedirects(r, f"/company/products/{p.pk}/"); self.assertTrue(p.image.name.startswith("company/product/"))
        old = Path(TMP, p.image.name); self.assertTrue(old.exists())
        self.client.post(f"/company/products/{p.pk}/", {"name": "Sarung Baru", "status": "published", "order": 100, "image": SimpleUploadedFile("b.png", PNG)})
        p.refresh_from_db(); self.assertFalse(old.exists()); self.assertTrue(Path(TMP, p.image.name).exists())   # gambar lama dibersihkan
        r = self.client.post("/company/products/new/", {"name": "Jahat", "status": "draft", "order": 1, "image": SimpleUploadedFile("x.svg", b"<svg onload=1>")}); self.assertEqual(r.status_code, 200); self.assertFalse(Product.objects.filter(name="Jahat").exists())
        r = self.client.post("/company/products/new/", {"name": "Harga", "status": "draft", "order": 1, "show_price": "on"}); self.assertFalse(Product.objects.filter(name="Harga").exists())
        self.client.post(f"/company/products/{p.pk}/delete/"); self.assertFalse(Product.objects.filter(pk=p.pk).exists())
        self.assertTrue(AuditLog.objects.filter(module="company", action="delete").exists())

    def test_delete_requires_post(self):
        self.login("hrd"); p = Product.objects.create(name="X"); self.assertEqual(self.client.get(f"/company/products/{p.pk}/delete/").status_code, 405); self.assertTrue(Product.objects.filter(pk=p.pk).exists())

    def test_unknown_kind_404(self):
        self.login("hrd"); self.assertEqual(self.client.get("/company/ngawur/").status_code, 404)

    def test_job_form_questions_and_slug(self):
        self.login("hrd")
        r = self.client.post("/recruitment/jobs/new/", {"title": "Staf Gudang", "employment_type": "kontrak", "openings": 2, "status": "open", "questions_text": "*Punya SIM?\nBisa shift?"})
        j = Job.objects.get(title="Staf Gudang"); self.assertEqual(j.questions, [{"label": "Punya SIM?", "required": True}, {"label": "Bisa shift?", "required": False}]); self.assertEqual(j.slug, "staf-gudang")
        self.assertContains(self.client.get(f"/situs/karir/{j.slug}/lamar/"), "Punya SIM?")
        j2 = self.client.post("/recruitment/jobs/new/", {"title": "Staf Gudang", "employment_type": "kontrak", "openings": 1, "status": "draft"}); self.assertEqual(Job.objects.filter(slug="staf-gudang-2").count(), 1)
        bad = self.client.post("/recruitment/jobs/new/", {"title": "Z", "employment_type": "kontrak", "openings": 1, "status": "draft", "questions_text": "\n".join(f"Pertanyaan {i}" for i in range(12))}); self.assertEqual(bad.status_code, 200)

    def test_settings_save_and_banner(self):
        self.login("hrd"); data = {"brand_name": "Wadimor", "banner_active": "on", "banner_id": "Website Dalam Pengembangan", "whatsapp": "+62 812-345-678"}
        r = self.client.post("/company/settings/", data); self.assertRedirects(r, "/company/settings/"); s = SiteSetting.get(); self.assertEqual(s.whatsapp, "62812345678")
        self.assertContains(self.client.get("/situs/"), "Website Dalam Pengembangan"); self.assertEqual(SiteSetting.objects.count(), 1)
        with self.assertRaises(PermissionError): s.delete()

    def test_messages_inbox(self):
        m = ContactMessage.objects.create(name="Ani", email="a@b.co", message="halo halo")
        self.login("hrd"); self.assertContains(self.client.get(f"/company/messages/{m.pk}/"), "halo halo"); m.refresh_from_db(); self.assertEqual(m.status, "read")
        self.client.post(f"/company/messages/{m.pk}/", {"status": "done", "note": "dibalas"}); m.refresh_from_db(); self.assertEqual((m.status, m.handled_by), ("done", self.hrd))


@ov()
class RetentionTests(Base):
    def make(self, **k):
        j = Job.objects.first() or self.job()
        d = dict(ref="R%d" % Application.objects.count(), job=j, full_name="Siti", email="s@x.id", email_hash=hash_email("s@x.id"), consent_at=timezone.now(), token_hash="t%d" % Application.objects.count(),
                 idem_key="k%d" % Application.objects.count(), retain_until=timezone.localdate() - timedelta(days=1), stage=Stage.REJECTED)
        d.update(k); return Application.objects.create(**d)

    def test_purge_command(self):
        old = self.make(); keep = self.make(retain_until=timezone.localdate() + timedelta(days=5)); hired = self.make(stage=Stage.HIRED)
        stale = self.make(stage=Stage.UNVERIFIED, retain_until=timezone.localdate() + timedelta(days=300)); Application.objects.filter(pk=stale.pk).update(created_at=timezone.now() - timedelta(days=30))
        out = io.StringIO(); call_command("purge_applicants", "--dry-run", stdout=out); self.assertIn("1 lamaran lewat masa simpan, 1 belum", out.getvalue()); old.refresh_from_db(); self.assertIsNone(old.anonymized_at)
        call_command("purge_applicants", stdout=io.StringIO())
        for x in (old, keep, hired, stale): x.refresh_from_db()
        self.assertIsNotNone(old.anonymized_at); self.assertIsNone(keep.anonymized_at); self.assertIsNone(hired.anonymized_at); self.assertIsNotNone(stale.anonymized_at)
        self.assertEqual(old.full_name, "(dianonimkan)"); self.assertTrue(old.events.filter(kind="anonymized").exists())

    def test_talent_pool_doubles_retention(self):
        from .models import default_retain_until
        self.assertGreater(default_retain_until(True), default_retain_until(False))


class PublicModeTests(SimpleTestCase):
    def test_public_urlconf_has_no_internal_routes(self):
        from django.urls import resolve, Resolver404
        from config import urls_public
        from django.urls import get_resolver
        r = get_resolver("config.urls_public")
        for path in ("/admin/", "/login/", "/employees/", "/poli/", "/hrd/", "/api/employees/", "/company/", "/recruitment/", "/users/", "/audit/"):
            with self.assertRaises(Resolver404, msg=path): r.resolve(path)
        self.assertTrue(r.resolve("/produk/")); self.assertTrue(r.resolve("/en/karir/"))


class UploadValidationTests(SimpleTestCase):
    def test_docx_requires_word_part_and_no_macro(self):
        import zipfile
        def z(names):
            b = io.BytesIO()
            with zipfile.ZipFile(b, "w") as f:
                for n in names: f.writestr(n, "x")
            return b.getvalue()
        from django.core.exceptions import ValidationError
        self.assertEqual(safe_upload.check_content("a.docx", z(["word/document.xml"]), safe_upload.CV_TYPES, 10 ** 6), "docx")
        for names in (["xl/a.xml"], ["word/document.xml", "word/vbaProject.bin"]):
            with self.assertRaises(ValidationError): safe_upload.check_content("a.docx", z(names), safe_upload.CV_TYPES, 10 ** 6)


class NoInlineStyleTests(SimpleTestCase):
    """CSP situs publik tanpa 'unsafe-inline': templat publik tidak boleh memakai style=, <style>, atau onclick=."""
    def test_templates_have_no_inline_style_or_handlers(self):
        import glob, re as _re
        for f in glob.glob("apps/company/templates/site/*.html"):
            s = open(f, encoding="utf-8").read()
            self.assertIsNone(_re.search(r'\sstyle=|<style|\son\w+=', s), f)


@ov()
class SeedCompanyTests(Base):
    def test_seed_is_idempotent_and_does_not_overwrite(self):
        call_command("seed_company", stdout=io.StringIO()); n = (Product.objects.count(), Article.objects.count(), Job.objects.count(), Faq.objects.count())
        Product.objects.update(name="Diubah HRD"); s = SiteSetting.get(); s.tagline_id = "Slogan HRD"; s.save()
        call_command("seed_company", stdout=io.StringIO())
        self.assertEqual(n, (Product.objects.count(), Article.objects.count(), Job.objects.count(), Faq.objects.count()))
        self.assertTrue(Product.objects.filter(name="Diubah HRD").exists()); self.assertEqual(SiteSetting.get().tagline_id, "Slogan HRD")
        self.assertTrue(Page.objects.filter(slug="tentang-kami", status="published").exists())


@ov(public=True)
class Putaran53VisualTests(Base):
    """Putaran 53: ilustrasi, ikon, motif otomatis, data demo lengkap — semuanya server-side & patuh CSP publik (tanpa style/skrip inline)."""
    def setUp(self):
        super().setUp(); call_command("seed_company", stdout=io.StringIO())

    def test_demo_seed_is_rich_and_idempotent_by_slug(self):
        self.assertGreaterEqual(Product.objects.count(), 8); self.assertGreaterEqual(StoreLocation.objects.count(), 4)
        self.assertGreaterEqual(Faq.objects.count(), 6); self.assertGreaterEqual(Article.objects.count(), 4); self.assertGreaterEqual(Job.objects.filter(status="open").count(), 4)
        n = (Product.objects.count(), Article.objects.count(), StoreLocation.objects.count(), Faq.objects.count(), Job.objects.count())
        Product.objects.update(name="Diubah HRD"); StoreLocation.objects.update(name="Toko Diubah"); Job.objects.update(title="Judul Diubah")
        call_command("seed_company", stdout=io.StringIO())
        self.assertEqual(n, (Product.objects.count(), Article.objects.count(), StoreLocation.objects.count(), Faq.objects.count(), Job.objects.count()))
        self.assertEqual(Product.objects.filter(price__isnull=False, show_price=True).count() >= 7, True)
        self.assertTrue(StoreLocation.objects.exclude(maps_url="").exists())

    def test_seed_fills_blank_site_contact_but_keeps_hrd_edits(self):
        s = SiteSetting.get(); s.phone = ""; s.address = "Alamat HRD"; s.save()
        call_command("seed_company", stdout=io.StringIO()); s = SiteSetting.get()
        self.assertEqual(s.address, "Alamat HRD"); self.assertTrue(s.phone)

    def test_pages_render_with_illustrations_and_no_inline_style(self):
        for path in ("/", "/en/", "/produk/", "/tentang-kami/", "/lokasi-toko/", "/artikel/", "/karir/", "/kontak/", "/faq/"):
            r = self.client.get(path); self.assertEqual(r.status_code, 200, path)
            html = r.content.decode(); self.assertNotRegex(html, r'\sstyle=|<style|\son\w+=', path)
        self.assertContains(self.client.get("/"), "class=\"ill\""); self.assertContains(self.client.get("/karir/"), "Ilustrasi tim pengrajin")
        self.assertContains(self.client.get("/lokasi-toko/"), "google.com/maps"); self.assertContains(self.client.get("/kontak/"), "Ilustrasi surat")

    def test_product_without_photo_gets_motif_not_blank_placeholder(self):
        r = self.client.get("/produk/"); self.assertContains(r, "class=\"motif\""); self.assertNotContains(r, "class=\"ph\"")
        p = Product.objects.get(slug="sarung-wadimor-songket"); self.assertContains(self.client.get(f"/produk/{p.slug}/"), "Rp 325.000")

    def test_store_card_has_directions_and_phone_link(self):
        r = self.client.get("/lokasi-toko/"); self.assertContains(r, "Petunjuk arah"); self.assertContains(r, "google.com/maps"); self.assertContains(r, "tel:")

    def test_motif_tag_is_stable_and_escapes_name(self):
        from apps.company.templatetags.site_tags import motif
        class O: pk = 3; name = "<b>x</b>"; motif = "songket"; colors = "biru"
        a, b = motif({}, O()), motif({}, O()); self.assertEqual(a, b); self.assertNotIn("<b>x", a); self.assertIn("&lt;b&gt;", a)

    def test_icon_tag_unknown_is_empty(self):
        from apps.company.templatetags.site_tags import icon
        self.assertEqual(icon("tidak-ada"), ""); self.assertIn("<svg", icon("shield"))



@ov(public=True)
class Putaran54Tests(Base):
    """Putaran 54: logo di header, ikon bahasa kanan, pita produk geser, peta Google tersemat, beranda gelap."""
    def setUp(self):
        super().setUp(); call_command("seed_company", stdout=io.StringIO())

    def test_header_logo_and_language_icon(self):
        h = self.client.get("/").content.decode()
        head = h[h.index("<header"):h.index("</header>")]
        self.assertIn("company/logo.svg", head); self.assertNotIn("<span>PT", head)
        self.assertIn('class="lang"', head); self.assertIn("<svg", head[head.index('class="lang"'):])
        self.assertLess(head.index("</nav>"), head.index('class="lang"'))  # ikon bahasa setelah menu = pojok kanan
        self.assertIn("/en/", head)

    def test_home_has_marquee_hero3_and_no_inline_style(self):
        h = self.client.get("/").content.decode()
        self.assertIn('class="hero3"', h); self.assertIn('class="marq', h); self.assertEqual(h.count("mset"), 2)
        self.assertIn('inert aria-hidden="true"', h); self.assertNotIn("style=", h)

    def test_products_page_marquee_only_without_filter(self):
        self.assertContains(self.client.get("/produk/"), 'class="marq')

    def test_google_map_embed_and_csp(self):
        r = self.client.get("/lokasi-toko/")
        self.assertContains(r, '<iframe class="gmap" src="https://www.google.com/maps?q=')
        self.assertIn("frame-src https://www.google.com", r["Content-Security-Policy"]); self.assertNotIn("unsafe-inline", r["Content-Security-Policy"])
        self.assertContains(self.client.get("/kontak/"), "google.com/maps")

    def test_gmap_tag_escapes_and_accepts_embed_url_only_from_google(self):
        from django.template import Context
        from apps.company.templatetags.site_tags import gmap
        c = Context({"lang": "en"})
        self.assertEqual(gmap(c, "", "", ""), "")
        u = gmap(c, "Jl. <b>X</b> & Y\n12", "Tegal", "")
        self.assertTrue(u.startswith("https://www.google.com/maps?q=")); self.assertNotIn("<", u); self.assertIn("hl=en", u)
        self.assertEqual(gmap(c, "a", "b", "https://evil.example/maps/embed"), gmap(c, "a", "b", ""))
        ok = "https://www.google.com/maps/embed?pb=!1m18"; self.assertEqual(gmap(c, "a", "b", ok), ok)

    def test_english_pages(self):
        h = self.client.get("/en/").content.decode()
        self.assertIn("Switch language to Bahasa Indonesia", h); self.assertIn("<b>ID</b>", h); self.assertIn("Our Collection", h)
