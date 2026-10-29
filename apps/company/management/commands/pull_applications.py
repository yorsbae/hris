"""HRIS: tarik lamaran dari host publik lewat SSH (HRIS yang menghubungi; publik tidak pernah menghubungi LAN). Jadwalkan tiap 5 menit.
  python manage.py pull_applications --ssh deploy@pub.example.com --remote-dir /srv/wadimor/app
  python manage.py pull_applications --file lamaran.jsonl      (uji / impor manual)"""
import subprocess
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from apps.company import sync


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, p):
        p.add_argument("--ssh", default=""); p.add_argument("--remote-dir", default=""); p.add_argument("--file", default="")
        p.add_argument("--python", default="venv/bin/python")

    def handle(self, *a, ssh, remote_dir, file, python, **o):
        state = Path(settings.PULL_STATE_FILE); since = state.read_text().strip() if state.exists() else ""
        if file: lines = Path(file).read_text().splitlines()
        else:
            if not (ssh and remote_dir): raise CommandError("Isi --ssh dan --remote-dir (atau --file).")
            cmd = f"cd {remote_dir} && DJANGO_SETTINGS_MODULE=config.settings_public {python} manage.py export_applications" + (f" --since '{since}'" if since else "")
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", ssh, cmd], capture_output=True, text=True, timeout=300)
            if r.returncode: raise CommandError("SSH gagal: " + r.stderr[-300:])
            lines = r.stdout.splitlines()
        new, upd, skip, latest = sync.import_applications(lines)
        if latest: state.write_text(latest)
        self.stdout.write(f"Lamaran: {new} baru, {upd} diperbarui, {skip} dilewati.")
