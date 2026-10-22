"""Verifikasi database SETELAH restore (dipakai scripts/restore.sh dan scripts/restore_drill.sh). Hanya membaca.

  python manage.py verify_restore --manifest /var/backups/hris/hris_XXXX.manifest.json

Pemeriksaan: (1) jumlah baris SEMUA tabel sama dengan manifest saat backup dibuat; (2) kunci enkripsi di .env sama dengan kunci saat backup (sidik jari)
dan benar-benar bisa membuka kolom terenkripsi (contoh baris dari tiap kolom); (3) integritas Payroll: jumlah slip & total bersih tiap run = isi slipnya.
Keluar dengan kode 1 bila ada yang GAGAL, supaya restore yang 'selesai' tetapi datanya tidak utuh/tidak terbaca tidak dianggap berhasil."""
import hashlib
import json
from decimal import Decimal

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Sum

from apps.core.crypto import EncryptedTextField

SKIP_COUNT = {"django_session", "django_migrations", "django_content_type", "auth_permission", "django_admin_log"}   # berubah sendiri oleh migrate/sesi
SAMPLE = 200


def key_fingerprint(raw=None):
    k = (raw if raw is not None else getattr(settings, "FIELD_ENCRYPTION_KEY", "")) or ""
    return hashlib.sha256(k.encode()).hexdigest()[:12] if k else ""


class Command(BaseCommand):
    help = "Verifikasi hasil restore: jumlah baris vs manifest, kunci enkripsi, integritas Payroll."

    def add_arguments(self, p): p.add_argument("--manifest", help="hris_XXXX.manifest.json dari backup.sh (opsional; tanpa ini hanya cek kunci & payroll)")

    def handle(self, *a, **o):
        bad = []
        def ok(msg): self.stdout.write(self.style.SUCCESS("OK   " + msg))
        def warn(msg): self.stdout.write(self.style.WARNING("PERINGATAN " + msg))
        def fail(msg): bad.append(msg); self.stdout.write(self.style.ERROR("GAGAL " + msg))

        man = {}
        if o["manifest"]:
            try: man = json.load(open(o["manifest"], encoding="utf-8"))
            except (OSError, ValueError) as e: raise CommandError(f"Manifest tidak terbaca: {e}")
        # 1. jumlah baris
        want = {t: int(v) for t, v in (man.get("counts") or {}).items() if str(v).isdigit() and t not in SKIP_COUNT}
        if want:
            have = set(connection.introspection.table_names()); diff = 0
            with connection.cursor() as c:
                for t, w in sorted(want.items()):
                    if t not in have: fail(f"tabel {t} tidak ada setelah restore"); diff += 1; continue
                    c.execute(f'select count(*) from "{t}"'); g = c.fetchone()[0]
                    if g != w: fail(f"{t}: manifest {w}, database {g}"); diff += 1
            if not diff: ok(f"jumlah baris {len(want)} tabel sama dengan manifest")
        elif o["manifest"]: warn("manifest tidak memuat jumlah baris (backup lama) — jumlah baris tidak diverifikasi")
        # 2. kunci enkripsi
        fp = key_fingerprint()
        if man.get("key_fingerprint"):
            if man["key_fingerprint"] == fp: ok("sidik jari FIELD_ENCRYPTION_KEY sama dengan saat backup")
            else: fail("FIELD_ENCRYPTION_KEY di .env BERBEDA dari kunci saat backup — kolom terenkripsi tidak akan terbaca. Pakai kunci asli.")
        n_dec = 0
        for m in apps.get_models():
            for f in m._meta.get_fields():
                if not isinstance(f, EncryptedTextField): continue
                try: n_dec += sum(1 for v in m._base_manager.exclude(**{f.name: ""}).values_list(f.name, flat=True)[:SAMPLE] if v)
                except Exception as e: fail(f"kolom terenkripsi {m._meta.label}.{f.name} tidak terbaca: {str(e)[:120]}")
        if not any(b.startswith("kolom terenkripsi") for b in bad): ok(f"{n_dec} nilai kolom terenkripsi berhasil dibuka") if n_dec else warn("tidak ada nilai terenkripsi untuk diuji")
        # 3. integritas payroll
        if "payroll_payrollrun" in connection.introspection.table_names():
            from apps.payroll.models import PayrollRun
            n = 0
            for r in PayrollRun.objects.exclude(status="dibatalkan"):
                agg = r.slips.aggregate(n=Sum("net_pay")); net = agg["n"] or Decimal(0); cnt = r.slips.count()
                if cnt != r.n_slips: fail(f"run {r.period}: n_slips={r.n_slips} tetapi slip di database {cnt}")
                elif abs(net - (r.total_net or 0)) > Decimal("0.01"): fail(f"run {r.period}: total bersih {r.total_net} ≠ jumlah slip {net}")
                n += 1
            if not any(b.startswith("run ") for b in bad): ok(f"{n} run gaji konsisten dengan slipnya")
        if bad: raise CommandError(f"{len(bad)} pemeriksaan GAGAL.")
