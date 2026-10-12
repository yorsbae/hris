"""Putaran 51: foto/berkas bukti opsional di Stand By & Lembur (satu bukti untuk satu regu) dan di jawaban Validasi Kehadiran;
penanda 📎 di daftar Pengajuan. Aturan keamanan sama dengan putaran 50 (nama acak, ber-scope, gambar dikenali dari isi, diaudit)."""
import shutil, tempfile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from apps.core.models import AuditLog
from apps.hr import attachments
from apps.hr.models import AttendanceCheck as AC, AttendanceCheckAttachment as ACA, ChangeRequest, RequestAttachment
from apps.hr.test_extra_work import ExtraWorkBase
from apps.hr.test_attendance_check import Base as CheckBase

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
MEDIA = tempfile.mkdtemp(prefix="hris_media51_")


def tearDownModule(): shutil.rmtree(MEDIA, ignore_errors=True)


def up(name="daftar.png", data=PNG): return SimpleUploadedFile(name, data)


@override_settings(MEDIA_ROOT=MEDIA)
class ExtraWorkProofTests(ExtraWorkBase):
    def test_one_proof_is_shared_by_whole_batch_and_stored_once(self):
        r = self.post(photos=[up(), up("b.pdf", b"%PDF-1.4 x")]); self.assertEqual(r.status_code, 302)
        reqs = list(ChangeRequest.objects.order_by("id")); self.assertEqual(len(reqs), 2)
        for q in reqs: self.assertEqual(q.attachments.count(), 2)
        a0, a1 = reqs[0].attachments.first(), reqs[1].attachments.first()
        self.assertEqual(a0.file.name, a1.file.name)  # berkas di disk satu salinan
        self.assertEqual(a0.content_type, "image/png"); self.assertEqual(reqs[0].attachments.last().content_type, "")
        self.assertTrue(AuditLog.objects.filter(action="request_attach", after__requests=2).exists())

    def test_proof_is_optional(self):
        self.assertEqual(self.post().status_code, 302); self.assertEqual(RequestAttachment.objects.count(), 0)

    def test_invalid_proof_blocks_whole_submission(self):
        r = self.post(photos=[up("kosong.png", b"")]); self.assertEqual(r.status_code, 200)
        self.assertEqual(ChangeRequest.objects.count(), 0); self.assertContains(r, "kosong")
        with override_settings(REQUEST_ATTACH_MAX_FILES=1):
            r = self.post(photos=[up(), up("x.png")]); self.assertEqual(r.status_code, 200); self.assertEqual(ChangeRequest.objects.count(), 0)

    def test_form_is_multipart_and_has_input(self):
        self.client.force_login(self.adm); h = self.client.get("/requests/g/lembur/new/").content.decode()
        self.assertIn('enctype="multipart/form-data"', h); self.assertIn('name="photos"', h)

    def test_scope_and_list_marker(self):
        self.post(photos=[up()]); q = ChangeRequest.objects.first(); a = q.attachments.first()
        self.client.force_login(self.adm2); self.assertEqual(self.client.get(f"/requests/{q.pk}/foto/{a.pk}/").status_code, 404)
        self.client.force_login(self.adm); resp = self.client.get(f"/requests/{q.pk}/foto/{a.pk}/")
        self.assertEqual(resp.status_code, 200); self.assertEqual(resp["Content-Type"], "image/png"); self.assertEqual(resp["X-Content-Type-Options"], "nosniff")
        self.client.force_login(self.hrd); self.assertContains(self.client.get("/requests/g/lembur/"), "📎1")

    def test_attach_types_cover_extra_work(self):
        self.assertIn("lembur", attachments.ATTACH_TYPES); self.assertIn("standby", attachments.ATTACH_TYPES)


@override_settings(MEDIA_ROOT=MEDIA)
class CheckProofTests(CheckBase):
    def answer(self, pk, user="adm", **kw):
        self.login(user); d = {"answer": "sakit", "note": "Surat dokter"}; d.update(kw); return self.client.post(reverse("attcheck_answer", args=[pk]), d)

    def test_answer_with_proof_saves_files_and_audits(self):
        a = self.make(); r = self.answer(a.pk, photos=[up(), up("s.pdf", b"%PDF-1.4 x")]); self.assertEqual(r.status_code, 302)
        a.refresh_from_db(); self.assertEqual(a.status, "dijawab"); self.assertEqual(a.attachments.count(), 2)
        f = a.attachments.first(); self.assertEqual(f.content_type, "image/png"); self.assertNotIn("daftar", f.file.name)  # nama di disk acak
        self.assertTrue(AuditLog.objects.filter(action="attendance_check_attach").exists())

    def test_proof_optional_and_answer_without_files_still_works(self):
        a = self.make(); self.answer(a.pk, answer="hadir", note=""); a.refresh_from_db(); self.assertEqual(a.status, "dijawab"); self.assertEqual(a.attachments.count(), 0)

    def test_bad_file_does_not_send_answer(self):
        a = self.make(); self.answer(a.pk, photos=[up("k.png", b"")]); a.refresh_from_db()
        self.assertEqual(a.status, "diminta"); self.assertEqual(ACA.objects.count(), 0)

    def test_rejected_answer_saves_no_files(self):
        a = self.make(); self.answer(a.pk, answer="izin", note="", photos=[up()]); a.refresh_from_db()  # keterangan wajib untuk Izin → ditolak
        self.assertEqual(a.status, "diminta"); self.assertEqual(ACA.objects.count(), 0)

    def test_detail_shows_proof_to_hrd_and_serves_scoped(self):
        a = self.make(); self.answer(a.pk, photos=[up()]); f = a.attachments.first()
        self.login("hrd"); html = self.client.get(reverse("attcheck_detail", args=[a.pk])).content.decode()
        self.assertIn("daftar.png", html); resp = self.client.get(reverse("attcheck_attachment", args=[a.pk, f.pk])); self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png"); self.assertTrue(AuditLog.objects.filter(action="attendance_check_attachment_view").exists())
        self.assertEqual(self.client.get(reverse("attcheck_attachment", args=[a.pk, f.pk]) + "?dl=1")["Content-Type"], "application/octet-stream")

    def test_other_department_and_poli_scope_404(self):
        a = self.make(); self.answer(a.pk, photos=[up()]); f = a.attachments.first(); url = reverse("attcheck_attachment", args=[a.pk, f.pk])
        from apps.core.models import Role, User
        other = User.objects.create_user("adm2", password="x", role=Role.DEPT_ADMIN, department=self.d2); self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.logout(); self.assertEqual(self.client.get(url).status_code, 302)  # anonim → login

    def test_fake_png_html_never_inline(self):
        a = self.make(); self.answer(a.pk, photos=[up("x.png", b"<html><script>1</script></html>")]); f = a.attachments.first()
        self.assertEqual(f.content_type, ""); self.login("hrd"); resp = self.client.get(reverse("attcheck_attachment", args=[a.pk, f.pk]))
        self.assertIn("attachment", resp["Content-Disposition"]); self.assertEqual(resp["Content-Type"], "application/octet-stream")

    def test_foreign_attachment_id_404(self):
        a = self.make("001"); b = self.make("003"); self.answer(b.pk, photos=[up()]); f = b.attachments.first()
        self.login("hrd"); self.assertEqual(self.client.get(reverse("attcheck_attachment", args=[a.pk, f.pk])).status_code, 404)

    def test_files_cannot_be_deleted_or_checks_with_files_removed(self):
        a = self.make(); self.answer(a.pk, photos=[up()])
        with self.assertRaises(Exception): a.delete()  # PROTECT
