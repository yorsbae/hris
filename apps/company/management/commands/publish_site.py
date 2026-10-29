"""HRIS: bangun bundle situs (konten TERBIT + status lamaran) dan, opsional, dorong ke host publik lewat rsync/SSH (koneksi KELUAR dari LAN saja).
  python manage.py publish_site --out /var/lib/hris/sync_out [--push deploy@pub.example.com:/srv/wadimor/inbox/]"""
import subprocess
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from apps.company import sync


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, p):
        p.add_argument("--out", default="sync_out"); p.add_argument("--push", default=""); p.add_argument("--keep", type=int, default=5)

    def handle(self, *a, out, push, keep, **o):
        try: raw, info = sync.build_bundle()
        except sync.SyncError as e: raise CommandError(str(e))
        d = Path(out); d.mkdir(parents=True, exist_ok=True)
        f = d / f"site-{timezone.now():%Y%m%d%H%M%S}.zip"; f.write_bytes(raw)
        self.stdout.write(f"Bundle {f} ({len(raw)//1024} KB): {info}")
        if push:
            r = subprocess.run(["rsync", "-e", "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes", "--chmod=F640", str(f), push], capture_output=True, text=True, timeout=300)
            if r.returncode: raise CommandError("Dorong gagal: " + r.stderr[-300:])
            self.stdout.write("Terkirim ke " + push)
        for old in sorted(d.glob("site-*.zip"))[:-keep]: old.unlink()
