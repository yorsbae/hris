"""Ringkasan pemakaian penyimpanan berkas (bukti pengajuan, dokumen karyawan, dst): ukuran per folder, ruang disk bebas,
dan BERKAS YATIM (ada di disk tetapi tidak dirujuk baris database mana pun, mis. sisa unggahan yang transaksinya dibatalkan).
Hanya membaca; tidak menghapus apa pun. Contoh: `python manage.py attachment_usage --list-orphans`."""
import shutil
from pathlib import Path
from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import models

SKIP = {"attendance_stage"}  # berkas impor absensi sementara (dihapus otomatis setelah impor)


def mb(n): return f"{n / 1048576:,.1f} MB"


class Command(BaseCommand):
    help = "Pemakaian penyimpanan media: ukuran per folder, sisa disk, dan berkas yatim."

    def add_arguments(self, p): p.add_argument("--list-orphans", action="store_true", help="tampilkan daftar berkas yatim (maks 200)")

    def handle(self, *a, **o):
        root = Path(settings.MEDIA_ROOT)
        if not root.exists(): self.stdout.write(f"{root} belum ada."); return
        used = {}
        for sub in sorted(x for x in root.iterdir() if x.is_dir() and x.name not in SKIP):
            fs = [f for f in sub.rglob("*") if f.is_file()]
            used[sub.name] = (len(fs), sum(f.stat().st_size for f in fs))
        total = sum(s for _, s in used.values())
        self.stdout.write(f"Folder media: {root}")
        for k, (n, s) in sorted(used.items(), key=lambda kv: -kv[1][1]): self.stdout.write(f"  {k:<28}{n:>8} berkas  {mb(s):>12}")
        du = shutil.disk_usage(root)
        self.stdout.write(f"Total media {mb(total)} · disk bebas {mb(du.free)} dari {mb(du.total)} ({du.free * 100 // du.total}% bebas)")
        known = set()
        for m in apps.get_models():
            for f in m._meta.concrete_fields:
                if isinstance(f, models.FileField): known.update(n for n in m.objects.exclude(**{f.name: ""}).values_list(f.name, flat=True) if n)
        orphans = [p for p in root.rglob("*") if p.is_file() and p.relative_to(root).parts[0] not in SKIP and p.relative_to(root).as_posix() not in known]
        self.stdout.write(f"Berkas yatim: {len(orphans)} ({mb(sum(p.stat().st_size for p in orphans))})")
        if o["list_orphans"]:
            for p in orphans[:200]: self.stdout.write(f"  {p.relative_to(root)}")
