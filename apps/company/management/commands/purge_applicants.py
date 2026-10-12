"""Retensi data pelamar (UU PDP): anonimkan lamaran lewat masa simpan (kecuali yang diterima bekerja) dan lamaran yang tak pernah diverifikasi.
Jadwalkan harian (contoh di scripts/cron-hris.example). `--dry-run` hanya menghitung."""
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.company import careers
from apps.company.models import Application, Stage
from django.conf import settings
from datetime import timedelta


class Command(BaseCommand):
    help = "Anonimkan data pelamar yang melewati masa simpan."

    def add_arguments(self, parser): parser.add_argument("--dry-run", action="store_true")

    def handle(self, *a, **o):
        if o["dry_run"]:
            today = timezone.localdate()
            n = Application.objects.filter(anonymized_at__isnull=True, retain_until__lt=today).exclude(stage=Stage.HIRED).count()
            m = Application.objects.filter(anonymized_at__isnull=True, stage=Stage.UNVERIFIED, created_at__lt=timezone.now() - timedelta(days=settings.CAREER_UNVERIFIED_DAYS)).count()
            self.stdout.write(f"[simulasi] {n} lamaran lewat masa simpan, {m} belum diverifikasi")
            return
        n, m = careers.purge_expired()
        self.stdout.write(f"{n} lamaran lewat masa simpan dianonimkan; {m} lamaran tanpa verifikasi dianonimkan")
