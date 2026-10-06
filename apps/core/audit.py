from .models import AuditLog
def log(request, module, action, obj=None, before=None, after=None):
    AuditLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        ip=request.META.get("REMOTE_ADDR"),  # di belakang nginx: gunakan header terpercaya
        module=module, action=action, object_type=obj.__class__.__name__ if obj else "",
        object_id=str(obj.pk) if obj else "", before=before, after=after)
