"""Laporan (hanya baca) scan ganda yang LOLOS dari aturan lama: PIN sama tanpa nol depan + waktu sama, dengan mesin 'tak diketahui' (FINGERSPOT) atau varian PIN.
Log mentah tidak boleh diubah/dihapus dari aplikasi, jadi perintah ini TIDAK menghapus apa pun; ia memberi daftar id untuk keputusan HRD/DBA (backup dulu).
  python manage.py audit_attendance_dupes [--limit 20]"""
from collections import defaultdict
from django.core.management.base import BaseCommand
from apps.hr.attendance_import import DEFAULT_DEVICE, _npin
from apps.hr.models import AttendanceRaw


class Command(BaseCommand):
    help = "Laporan scan ganda (hanya baca; tidak menghapus)."

    def add_arguments(self, p): p.add_argument("--limit", type=int, default=20)

    def handle(self, *a, **o):
        groups = defaultdict(list)
        for pk, pin, dev, at in AttendanceRaw.objects.values_list("pk", "pin", "device_id", "punched_at").iterator(chunk_size=20000): groups[(_npin(pin), at)].append((pk, pin, dev))
        dup = [(k, v) for k, v in groups.items() if len(v) > 1 and (len({d for _, _, d in v}) < len(v) or any(d == DEFAULT_DEVICE for _, _, d in v))]
        extra = sum(len(v) - 1 for _, v in dup)
        self.stdout.write(f"{len(groups)} scan berbeda; {len(dup)} kelompok ganda; {extra} baris berlebih (kandidat dibersihkan).")
        for (n, at), v in dup[:o["limit"]]: self.stdout.write(f"  PIN {n} @ {at:%Y-%m-%d %H:%M:%S}: " + ", ".join(f"#{pk}({pin}/{dev})" for pk, pin, dev in v))
        if extra: self.stdout.write("Tidak ada yang dihapus. Backup dulu, lalu putuskan baris mana yang dibuang (log mentah tidak dapat dihapus dari aplikasi).")
