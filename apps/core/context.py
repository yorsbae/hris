from django.conf import settings

def unread(request):
    """Badge di header + menu samping per peran. Dua query ringan per halaman (terindeks)."""
    u = request.user
    out = {"company_name": settings.COMPANY_NAME, "app_version": settings.APP_VERSION}
    if not u.is_authenticated: return out
    from apps.hr.info import unread_count
    from . import navigation
    from .models import Notification, Role
    out["unread_count"] = Notification.objects.filter(user=u, is_read=False).count()
    if u.role in (Role.SUPERADMIN, Role.HRD, Role.DEPT_ADMIN, Role.POLI): out["unread_ann"] = unread_count(u)
    out["nav_groups"], out["crumb"] = navigation.build(u, request.path)
    return out
