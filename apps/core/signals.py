from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from .models import AuditLog
def _log(action):
    def h(sender, request=None, user=None, **kw):
        AuditLog.objects.create(user=user if getattr(user, "pk", None) else None,
            ip=request.META.get("REMOTE_ADDR") if request else None, module="auth", action=action)
    return h
user_logged_in.connect(_log("login")); user_logged_out.connect(_log("logout")); user_login_failed.connect(_log("login_failed"))
