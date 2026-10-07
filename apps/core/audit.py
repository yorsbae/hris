from .models import AuditLog
from .net import client_ip
def log(request, module, action, obj=None, before=None, after=None):
    AuditLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        ip=client_ip(request),  # di belakang Nginx: lihat apps/core/net.py (TRUSTED_PROXY_IPS)
        module=module, action=action, object_type=obj.__class__.__name__ if obj else "",
        object_id=str(obj.pk) if obj else "", before=before, after=after)
