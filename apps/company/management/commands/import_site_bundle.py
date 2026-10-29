"""PUBLIK: terapkan bundle terbaru dari folder inbox (tanda tangan, hash, nama berkas, isi gambar diperiksa; atomik; anti-replay). Jadwalkan tiap menit.
  python manage.py import_site_bundle /srv/wadimor/inbox"""
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from apps.company import sync


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, p): p.add_argument("inbox")

    def handle(self, *a, inbox, **o):
        d = Path(inbox); state = Path(settings.SYNC_STATE_FILE)
        files = sorted(d.glob("site-*.zip"))
        if not files: return
        last = state.read_text().strip() if state.exists() else ""
        f = files[-1]
        try: serial, info, refs = sync.apply_bundle(f.read_bytes(), last)
        except sync.SyncError as e:
            (d / "rejected").mkdir(exist_ok=True); f.rename(d / "rejected" / f.name); raise CommandError(f"{f.name} DITOLAK: {e}")
        state.write_text(serial); (d / "done").mkdir(exist_ok=True)
        for x in files: x.rename(d / "done" / x.name)
        for old in sorted((d / "done").glob("site-*.zip"))[:-5]: old.unlink()
        self.stdout.write(f"Diterapkan {f.name}: {info}")
