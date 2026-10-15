"""Periksa kesehatan sistem dan kirim alarm bila ada yang gagal (jalankan dari cron tiap 5–15 menit; lihat scripts/cron-hris.example).

    python manage.py check_health            # cetak hasil; keluar dengan kode 1 bila ada yang gagal
    python manage.py check_health --notify   # + kirim alarm (webhook/email dari .env) saat ada yang gagal, dan pesan "pulih" saat sudah normal

Anti-banjir: alarm dikirim saat himpunan masalah BERUBAH, atau diulang tiap ALERT_REPEAT_HOURS (bawaan 6 jam) selama masalah belum teratasi."""
import json
from datetime import datetime, timedelta, timezone as dt_tz
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from apps.core import alerts, health


class Command(BaseCommand):
    help = "Periksa kesehatan sistem; --notify mengirim alarm saat gagal."

    def add_arguments(self, p): p.add_argument("--notify", action="store_true", help="kirim alarm (webhook/email) saat gagal atau pulih")

    def handle(self, *a, **o):
        r = health.collect(); bad = health.failing(r)
        for k, c in r["checks"].items(): self.stdout.write(f"{'OK  ' if c['ok'] else 'GAGAL'} {k}: {c['detail']}")
        if o["notify"]: self._notify(r, bad)
        if bad: raise CommandError("Pemeriksaan gagal: " + ", ".join(bad))
        self.stdout.write(self.style.SUCCESS("Semua pemeriksaan baik."))

    def _notify(self, r, bad):
        path = Path(getattr(settings, "HEALTH_STATE_FILE", settings.BASE_DIR / ".health_state.json")); now = datetime.now(dt_tz.utc)
        try: st = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError): st = {}
        prev, last = st.get("failing", []), st.get("alerted_at")
        try: last = datetime.fromisoformat(last) if last else None
        except ValueError: last = None
        repeat = timedelta(hours=getattr(settings, "ALERT_REPEAT_HOURS", 6))
        if bad and (bad != prev or last is None or now - last >= repeat):
            alerts.send("Pemeriksaan kesehatan GAGAL", "\n".join(f"- {k}: {r['checks'][k]['detail']}" for k in bad)); st = {"failing": bad, "alerted_at": now.isoformat()}
        elif not bad and prev:
            alerts.send("Pemeriksaan kesehatan PULIH", "Semua pemeriksaan kembali baik (sebelumnya gagal: " + ", ".join(prev) + ")."); st = {"failing": [], "alerted_at": None}
        else: return
        try: path.write_text(json.dumps(st), encoding="utf-8")
        except OSError as e: self.stderr.write(f"Peringatan: status alarm tidak dapat disimpan ({e}); alarm bisa terkirim berulang.")
