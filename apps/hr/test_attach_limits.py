"""Bukti pengajuan: semua jenis boleh melampirkan, batas total ukuran, pemeriksaan ruang disk, galat I/O tidak membuat pengajuan setengah jadi,
dan perintah attachment_usage (berkas yatim)."""
import io, shutil, tempfile
from datetime import date
from unittest import mock
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from apps.core.models import Role, User
from . import attachments
from .models import ChangeRequest, Department, Employee, Position, RequestAttachment, Shift

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
MEDIA = tempfile.mkdtemp(prefix="hris_media_lim_")


def tearDownModule(): shutil.rmtree(MEDIA, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA)
class AttachLimitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.d1, cls.d2 = Department.objects.create(code="PRD", name="Produksi"), Department.objects.create(code="GDG", name="Gudang")
        pos = Position.objects.create(name="Staff")
        cls.emp = Employee.objects.create(nik="P01", name="Andi", gender="L", department=cls.d1, position=pos, join_date=date(2024, 1, 1))
        cls.hrd = User.objects.create_user("hrd", password="x", role=Role.HRD)

    def setUp(self): self.client.force_login(self.hrd)

    def post_mutasi(self, files):
        return self.client.post("/requests/new/", {"type": "mutasi_dept", "nik": "P01", "department": self.d2.pk, "effective_date": "2030-01-01",
                                                    "reason": "Penataan", "submit": "1", "photos": files})

    def test_every_request_type_accepts_proof(self):
        self.assertEqual(set(attachments.ATTACH_TYPES), set(ChangeRequest.TYPES))
        self.post_mutasi([SimpleUploadedFile("sk.png", PNG)])
        r = ChangeRequest.objects.get(); self.assertEqual((r.status, r.attachments.count()), ("pending", 1))

    def test_total_size_limit(self):
        with override_settings(REQUEST_ATTACH_MAX_TOTAL_MB=0):
            resp = self.post_mutasi([SimpleUploadedFile("a.png", PNG)])
        self.assertContains(resp, "Total ukuran berkas"); self.assertEqual(ChangeRequest.objects.count(), 0)

    def test_total_counts_existing_files_when_adding_later(self):
        self.post_mutasi([SimpleUploadedFile("a.png", PNG)]); r = ChangeRequest.objects.get()
        with override_settings(REQUEST_ATTACH_MAX_TOTAL_MB=0):
            self.client.post(f"/requests/{r.pk}/foto/", {"photos": SimpleUploadedFile("b.png", PNG)})
        self.assertEqual(r.attachments.count(), 1)

    def test_low_disk_blocks_upload_but_not_request_without_proof(self):
        with mock.patch("apps.hr.attachments.shutil.disk_usage", return_value=mock.Mock(free=10 * 1024 * 1024)):
            resp = self.post_mutasi([SimpleUploadedFile("a.png", PNG)])
            self.assertContains(resp, "Penyimpanan server hampir penuh"); self.assertEqual(ChangeRequest.objects.count(), 0)
            self.post_mutasi([])  # tanpa bukti tetap bisa
        self.assertEqual((ChangeRequest.objects.count(), RequestAttachment.objects.count()), (1, 0))

    def test_disk_error_while_writing_leaves_no_half_request(self):
        with mock.patch("django.core.files.storage.FileSystemStorage._save", side_effect=OSError(28, "No space left on device")):
            resp = self.post_mutasi([SimpleUploadedFile("a.png", PNG)])
        self.assertEqual(resp.status_code, 200); self.assertContains(resp, "Berkas gagal disimpan")
        self.assertEqual((ChangeRequest.objects.count(), RequestAttachment.objects.count()), (0, 0))

    def test_attachment_usage_reports_orphans(self):
        self.post_mutasi([SimpleUploadedFile("a.png", PNG)])
        import os; os.makedirs(f"{MEDIA}/request_attachments/999", exist_ok=True)
        open(f"{MEDIA}/request_attachments/999/yatim.bin", "wb").write(b"x" * 10)
        out = io.StringIO(); call_command("attachment_usage", "--list-orphans", stdout=out); t = out.getvalue()
        self.assertIn("request_attachments", t); self.assertIn("Berkas yatim: 1", t); self.assertIn("yatim.bin", t)
