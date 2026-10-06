def unread(request):
    """Badge di header. Dua query ringan per halaman (terindeks)."""
    u = request.user
    if not u.is_authenticated: return {}
    from apps.hr.info import unread_count
    from .models import Notification, Role
    out = {"unread_count": Notification.objects.filter(user=u, is_read=False).count()}
    if u.role in (Role.SUPERADMIN, Role.HRD, Role.DEPT_ADMIN, Role.POLI): out["unread_ann"] = unread_count(u)
    return out
