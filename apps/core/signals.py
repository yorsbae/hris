from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from .models import AuditLog
from .net import client_ip


# PENTING: receiver sinyal Django disimpan sebagai weak reference. Versi lama menyambungkan closure hasil `_log(...)` yang tak punya
# referensi lain → langsung dibuang garbage collector, sehingga login/logout/login_failed TIDAK PERNAH tercatat. Handler bernama di level modul
# (dan dispatch_uid) menjamin tetap hidup. Ada tes regresi di apps/core/test_net_password.py.
def _write(action, request, user):
    AuditLog.objects.create(user=user if getattr(user, "pk", None) else None,
                            ip=client_ip(request) if request else None, module="auth", action=action)


@receiver(user_logged_in, dispatch_uid="audit_login")
def on_login(sender, request=None, user=None, **kw): _write("login", request, user)


@receiver(user_logged_out, dispatch_uid="audit_logout")
def on_logout(sender, request=None, user=None, **kw): _write("logout", request, user)


@receiver(user_login_failed, dispatch_uid="audit_login_failed")
def on_login_failed(sender, request=None, credentials=None, **kw): _write("login_failed", request, None)  # username yang dicoba sengaja tidak disimpan (sering berisi sandi yang salah ketik)
