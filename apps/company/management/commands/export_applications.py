"""PUBLIK (dipanggil HRIS lewat SSH): cetak lamaran sebagai JSON per baris. Tidak mengubah apa pun selain membaca."""
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime
import json
from apps.company import sync


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, p): p.add_argument("--since", default="")

    def handle(self, *a, since, **o):
        for d in sync.export_applications(parse_datetime(since) if since else None): self.stdout.write(json.dumps(d, ensure_ascii=False))
