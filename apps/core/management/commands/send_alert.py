"""Kirim alarm lewat saluran di .env (dipakai scripts/backup.sh saat backup gagal).

    python manage.py send_alert "Backup GAGAL" "pg_dump keluar dengan kode 1"
Kode keluar 0 bila setidaknya satu saluran (webhook/email) terkirim; 1 bila tidak ada saluran yang berhasil (alarm tetap tercatat di log)."""
from django.core.management.base import BaseCommand, CommandError
from apps.core import alerts


class Command(BaseCommand):
    help = "Kirim alarm operasional (webhook/email dari .env)."

    def add_arguments(self, p): p.add_argument("subject"); p.add_argument("message", nargs="?", default="")

    def handle(self, *a, **o):
        r = alerts.send(o["subject"], o["message"])
        sent = [k for k in ("webhook", "email") if r[k]]
        if not sent: raise CommandError("Tidak ada saluran alarm yang berhasil (cek ALERT_WEBHOOK_URL / ALERT_EMAILS + EMAIL_*); alarm hanya tercatat di log.")
        self.stdout.write("Alarm terkirim via: " + ", ".join(sent))
