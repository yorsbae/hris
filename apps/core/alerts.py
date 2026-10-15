"""Pengirim alarm operasional (backup gagal, health check gagal). Saluran dipilih lewat .env: ALERT_WEBHOOK_URL (Slack/Mattermost/Discord/Telegram-bridge:
badan JSON memuat kunci `text` dan `content`) dan/atau ALERT_EMAILS (butuh EMAIL_* di .env). Selalu juga dicatat ke log (ERROR → errors.log).
Alarm TIDAK PERNAH melempar galat: kegagalan mengirim alarm tidak boleh menutupi masalah aslinya; hasilnya dikembalikan sebagai daftar saluran yang berhasil/gagal."""
import json
import logging
import urllib.request
from urllib.parse import urlparse
from django.conf import settings
from django.core.mail import send_mail

log = logging.getLogger("hris.alert")
TIMEOUT = 10  # detik


def send(subject, message):
    """Kirim ke semua saluran yang dikonfigurasi. Kembalikan {"log": True, "webhook": True/False/None, "email": True/False/None} (None = tidak dikonfigurasi)."""
    log.error("ALARM %s — %s", subject, message)
    res = {"log": True, "webhook": None, "email": None}
    url = getattr(settings, "ALERT_WEBHOOK_URL", "") or ""
    if url:
        try:
            if urlparse(url).scheme not in ("http", "https"): raise ValueError("ALERT_WEBHOOK_URL harus http(s)")
            body = json.dumps({"text": f"{subject}\n{message}", "content": f"{subject}\n{message}"}).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r: res["webhook"] = 200 <= r.status < 300
        except Exception as e:  # noqa: BLE001 — alarm tidak boleh melempar
            log.error("Alarm via webhook gagal: %s", e); res["webhook"] = False
    to = list(getattr(settings, "ALERT_EMAILS", []) or [])
    if to:
        try: send_mail(f"[HRIS] {subject}", message, None, to, fail_silently=False); res["email"] = True
        except Exception as e:  # noqa: BLE001
            log.error("Alarm via email gagal: %s", e); res["email"] = False
    return res
