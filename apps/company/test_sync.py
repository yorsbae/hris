"""Tes sinkronisasi HRIS <-> situs publik (apps/company/sync.py)."""
import io
import json
import zipfile
from pathlib import Path
from django.core import mail
from django.test import override_settings
from django.core.management import call_command
from django.db import connection
from apps.core.models import Notification
from . import sync, safe_upload
from .models import Application, ApplicationEvent, Article, Job, JobStatus, Product, ProductCategory, SiteSetting, Stage
from .tests import Base, ov, TMP, PNG

SECRET = "s" * 40


def sov(**k): return ov(SYNC_SHARED_SECRET=SECRET, **k)


@sov()
class ContentSyncTests(Base):
    def setUp(self):
        SiteSetting.get()
        cat = ProductCategory.objects.create(name_id="Bawahan")
        self.pub = Product.objects.create(name="Sarung A", category=cat, status="published", updated_by=self.hrd)
        self.draft = Product.objects.create(name="RAHASIA DRAF", category=cat, status="draft")
        self.job_open = self.job(status=JobStatus.OPEN, department=self.adm.department)
        self.job_draft = Job.objects.create(title="Lowongan Draf Rahasia", status=JobStatus.DRAFT)

    def test_draft_never_leaves_hris_and_no_internal_pointers(self):
        raw, info = sync.build_bundle()
        blob = zipfile.ZipFile(io.BytesIO(raw)).read("content.json").decode()
        self.assertNotIn("RAHASIA DRAF", blob); self.assertNotIn("Lowongan Draf Rahasia", blob)
        self.assertNotIn("updated_by", blob); self.assertNotIn("department_id", blob)
        self.assertIn("Sarung A", blob)

    def test_roundtrip_applies_and_removes_unpublished(self):
        raw, _ = sync.build_bundle()
        Job.objects.all().delete(); Product.objects.all().delete(); Product.objects.create(name="Sisa lama di publik", status="published")
        sync.apply_bundle(raw)
        self.assertEqual(list(Product.objects.values_list("name", flat=True)), ["Sarung A"])
        self.assertEqual(Job.objects.get(pk=self.job_open.pk).status, JobStatus.OPEN)
        self.assertIsNone(Job.objects.get(pk=self.job_open.pk).department_id)    # departemen HRIS tidak bocor / tidak dipakai di publik

    def test_tampered_missing_secret_and_replay_rejected(self):
        raw, _ = sync.build_bundle()
        z = zipfile.ZipFile(io.BytesIO(raw)); out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as w:
            for n in z.namelist(): w.writestr(n, z.read(n).replace(b"Sarung A", b"Sarung X") if n == "content.json" else z.read(n))
        with self.assertRaises(sync.SyncError): sync.apply_bundle(out.getvalue())
        with override_settings(SYNC_SHARED_SECRET="y" * 40), self.assertRaises(sync.SyncError): sync.apply_bundle(raw)   # rahasia beda = tanda tangan salah
        with override_settings(SYNC_SHARED_SECRET=""), self.assertRaises(sync.SyncError): sync.build_bundle()
        serial, *_ = sync.apply_bundle(raw)
        with self.assertRaises(sync.SyncError): sync.apply_bundle(raw, last_serial=serial)                              # replay

    def test_malicious_media_names_and_content_rejected(self):
        raw, _ = sync.build_bundle(); z = zipfile.ZipFile(io.BytesIO(raw)); files = {n: z.read(n) for n in z.namelist() if n not in ("MANIFEST.json", "SIGNATURE")}

        def rebuild(extra):
            import hashlib
            f = {**files, **extra}
            mf = json.dumps({"serial": "99999999999999999999", "files": {k: hashlib.sha256(v).hexdigest() for k, v in f.items()}}, sort_keys=True).encode()
            o = io.BytesIO()
            with zipfile.ZipFile(o, "w") as w:
                w.writestr("MANIFEST.json", mf); w.writestr("SIGNATURE", sync._sign(mf))
                for k, v in f.items(): w.writestr(k, v)
            return o.getvalue()
        with self.assertRaises(sync.SyncError): sync.apply_bundle(rebuild({"media/../../etc/cron.d/x": b"x"}))            # path traversal
        with self.assertRaises(sync.SyncError): sync.apply_bundle(rebuild({"media/company/product/" + "a" * 32 + ".php": b"<?php"}))
        with self.assertRaises(sync.SyncError): sync.apply_bundle(rebuild({"media/company/product/" + "a" * 32 + ".png": b"<?php system($_GET[0]);"}))  # ekstensi png, isi bukan png
        ok = rebuild({"media/company/product/" + "a" * 32 + ".png": PNG})
        sync.apply_bundle(ok); self.assertTrue(Path(TMP, "company/product/" + "a" * 32 + ".png").exists())

    def test_import_command_moves_rejected_files(self):
        import tempfile
        from django.core.management.base import CommandError
        inbox = Path(tempfile.mkdtemp()); (inbox / "site-20260101000000.zip").write_bytes(b"bukan zip")
        with override_settings(SYNC_STATE_FILE=str(inbox / "state")):
            with self.assertRaises(CommandError): call_command("import_site_bundle", str(inbox))
        self.assertTrue((inbox / "rejected" / "site-20260101000000.zip").exists())


@sov(public=True)
class ApplicationSyncTests(Base):
    def setUp(self):
        self.j = self.job(status=JobStatus.OPEN)
        with override_settings(CAREER_REQUIRE_EMAIL_VERIFY=False):
            self.client.post(f"/karir/{self.j.slug}/lamar/", self.apply_data(self.j))
        self.a = Application.objects.get(); self.assertEqual(self.a.stage, Stage.NEW)
        Notification.objects.all().delete()

    def _export(self, since=None): return [json.dumps(d) for d in sync.export_applications(since)]

    def test_pull_creates_in_hris_reencrypts_and_is_idempotent(self):
        lines = self._export(); self.assertIn("budi@example.com", lines[0])
        cv_path = self.a.cv_path; pdf = safe_upload.read_private(cv_path)
        ApplicationEvent.objects.filter(application=self.a).delete(); self.a.__class__.objects.filter(pk=self.a.pk).delete() if False else None
        # simulasi HRIS: DB bersih
        with connection.cursor() as c: c.execute("DELETE FROM company_applicationevent"); c.execute("DELETE FROM company_application")
        new, upd, skip, latest = sync.import_applications(lines); self.assertEqual((new, upd, skip), (1, 0, 0)); self.assertTrue(latest)
        b = Application.objects.get(); self.assertEqual(b.email, "budi@example.com"); self.assertEqual(safe_upload.read_private(b.cv_path), pdf)
        self.assertTrue(Notification.objects.filter(title__contains="Lamaran baru").exists())
        with connection.cursor() as c: c.execute("SELECT email FROM company_application"); self.assertTrue(c.fetchone()[0].startswith("enc1:"))
        n = ApplicationEvent.objects.count()
        self.assertEqual(sync.import_applications(lines)[:2], (0, 1)); self.assertEqual(ApplicationEvent.objects.count(), n)   # idempoten

    def test_hris_stage_flows_down_and_public_pii_minimized(self):
        a = self.a; a.refresh_from_db()
        from . import careers
        careers.move(a, Stage.INTERVIEW, self.sa, public_message="Wawancara Senin", notify=False)
        raw, info = sync.build_bundle(); self.assertEqual(info["applications"], 1)
        # sisi publik: salinan lamaran masih berstatus NEW dengan data pribadi lengkap, lalu bundle diterapkan
        Application.objects.filter(pk=a.pk).update(stage=Stage.NEW); ApplicationEvent.objects.filter(application=a, kind="stage").delete()
        sync.apply_bundle(raw)
        a.refresh_from_db(); self.assertEqual(a.stage, Stage.INTERVIEW)
        self.assertTrue(ApplicationEvent.objects.filter(application=a, public_message="Wawancara Senin").exists())
        self.assertEqual((a.phone, a.cover_letter, a.cv_path, a.birth_date), ("", "", "", ""))     # diminimalkan setelah ditarik
        self.assertEqual(a.email, "budi@example.com"); self.assertTrue(a.token_hash)                # tetap: kirim ulang tautan & lacak status
        self.assertEqual(ApplicationEvent.objects.filter(application=a, kind="pulled").count(), 1)
        sync.apply_bundle(sync.build_bundle()[0].replace(b"", b""), last_serial="")                 # kedua kali: tidak menggandakan 'pulled'
        self.assertEqual(ApplicationEvent.objects.filter(application=a, kind="pulled").count(), 1)

    def test_withdrawn_by_applicant_not_overwritten_and_flows_up(self):
        a = self.a; from . import careers; careers.withdraw(a)
        lines = self._export(); self.assertEqual(json.loads(lines[0])["stage"], Stage.WITHDRAWN)
        raw, _ = sync.build_bundle(); Application.objects.filter(pk=a.pk).update(stage=Stage.SCREENING)
        a.refresh_from_db(); self.assertEqual(a.stage, Stage.SCREENING)   # (HRIS salah-posisi) tidak dipakai; yang diuji: publik tidak menimpa 'withdrawn'
        Application.objects.filter(pk=a.pk).update(stage=Stage.WITHDRAWN)
        sync._apply_statuses([{"ref": a.ref, "stage": Stage.SCREENING, "anonymized": False, "events": []}])
        a.refresh_from_db(); self.assertEqual(a.stage, Stage.WITHDRAWN)

    def test_unverified_not_exported(self):
        Application.objects.filter(pk=self.a.pk).update(stage=Stage.UNVERIFIED); self.assertEqual(self._export(), [])
