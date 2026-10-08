"""Penguncian akun per username dengan jeda progresif (putaran 21, P1).

Aturan: `LOGIN_LOCK_THRESHOLD` kali salah sandi berturut-turut (hitungan kembali nol bila kegagalan terakhir > `LOGIN_LOCK_RESET_HOURS` lalu,
atau setelah login berhasil) → akun terkunci `LOGIN_LOCK_STEPS_MIN[n]` menit; setiap salah sandi SESUDAH kunci habis menaikkan jenjang.
Selama terkunci, sandi (benar maupun salah) ditolak TANPA menambah hitungan dan dengan pesan yang sama seperti salah sandi biasa (tidak membocorkan
apakah username ada/terkunci). Kunci selalu sementara (maks. jenjang terakhir) → penyerang tidak dapat mengunci akun selamanya; tiap penguncian
tercatat di audit (`auth/account_locked`) dan Superadmin dapat membukanya (`/users/<id>/unlock/`).
Risiko yang disadari: pihak lain yang tahu username dapat memicu kunci sementara (denial-of-service ringan) — diredam oleh rate limit per IP."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone


def is_locked(user) -> bool:
    return bool(user.locked_until and user.locked_until > timezone.now())


def remaining_seconds(user) -> int:
    return max(0, int((user.locked_until - timezone.now()).total_seconds())) if user.locked_until else 0


def record_failure(user, request=None):
    """Tambah hitungan gagal secara atomik (baris dikunci agar dua percobaan bersamaan tak saling menimpa). Mengembalikan menit kunci (0 = belum terkunci)."""
    from .models import AuditLog, User
    from .net import client_ip
    steps = tuple(settings.LOGIN_LOCK_STEPS_MIN)
    with transaction.atomic():
        u = User.objects.select_for_update().get(pk=user.pk)
        now = timezone.now()
        if u.last_failed_at and now - u.last_failed_at > timedelta(hours=settings.LOGIN_LOCK_RESET_HOURS): u.failed_logins = 0
        u.failed_logins = min(u.failed_logins + 1, 32000); u.last_failed_at = now
        minutes = 0
        if u.failed_logins >= settings.LOGIN_LOCK_THRESHOLD:
            minutes = steps[min(u.failed_logins - settings.LOGIN_LOCK_THRESHOLD, len(steps) - 1)]
            u.locked_until = now + timedelta(minutes=minutes)
        u.save(update_fields=["failed_logins", "last_failed_at", "locked_until"])
        if minutes: AuditLog.objects.create(user=u, ip=client_ip(request) if request else None, module="auth", action="account_locked",
                                            after={"minutes": minutes, "failed": u.failed_logins})
    return minutes


def clear(user):
    """Login berhasil / Superadmin membuka kunci / sandi direset. Tidak menulis bila tidak ada yang perlu dibersihkan (hemat 1 UPDATE per login)."""
    if not (user.failed_logins or user.locked_until or user.last_failed_at): return False
    type(user).objects.filter(pk=user.pk).update(failed_logins=0, last_failed_at=None, locked_until=None)
    user.failed_logins, user.last_failed_at, user.locked_until = 0, None, None
    return True
