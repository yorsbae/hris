from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from apps.core.audit import log
from apps.core.models import Notification, Role
from apps.core.scope import require_roles
from . import info
from .models import Announcement, AnnouncementRead

VIEW_ROLES = (Role.HRD, Role.DEPT_ADMIN, Role.POLI)


def _get(user, pk):
    try: return info.visible_to(user, Announcement.objects.select_related("created_by")).get(pk=pk)
    except Announcement.DoesNotExist: raise Http404  # tidak membocorkan keberadaan


@login_required
@require_roles(*VIEW_ROLES)
def announcement_list(request):
    qs = info.visible_to(request.user)
    kind, unread = request.GET.get("kind", ""), request.GET.get("unread") == "1"
    read_ids = AnnouncementRead.objects.filter(user=request.user).values("announcement_id")
    if kind: qs = qs.filter(kind=kind)
    if unread: qs = qs.exclude(pk__in=read_ids)
    page = Paginator(qs.order_by("-created_at"), 20).get_page(request.GET.get("page", 1))
    read = set(AnnouncementRead.objects.filter(user=request.user, announcement__in=list(page)).values_list("announcement_id", flat=True))
    for a in page: a.is_read, a.kind_label = a.pk in read, info.KINDS.get(a.kind, a.kind)
    return render(request, "announcements_list.html", {"page": page, "kind": kind, "unread": unread, "kinds": info.KINDS,
                                                       "can_create": request.user.role in info.FULL_VIEW})


@login_required
@require_roles(Role.HRD)
def announcement_new(request):
    form = info.AnnouncementForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        a = form.save(commit=False); a.created_by = request.user; a.save(); form.save_m2m()
        n = info.notify(a, request.user)
        log(request, "hr", "announcement_create", a, None, {"kind": a.kind, "title": a.title, "notified": n})
        messages.success(request, f"Terkirim ke {n} penerima.")
        return redirect("announcement_detail", pk=a.pk)
    return render(request, "announcement_form.html", {"form": form})


@login_required
@require_roles(*VIEW_ROLES)
def announcement_detail(request, pk):
    a = _get(request.user, pk)
    AnnouncementRead.objects.get_or_create(announcement=a, user=request.user)  # unique (announcement,user): aman dipanggil berulang
    Notification.objects.filter(user=request.user, is_read=False, link=f"/announcements/{pk}/").update(is_read=True)
    ctx = {"a": a, "kind_label": info.KINDS.get(a.kind, a.kind)}
    if request.user.role in info.FULL_VIEW:  # status baca hanya untuk HRD/Superadmin
        aud = info.audience(a); read_ids = set(AnnouncementRead.objects.filter(announcement=a).values_list("user_id", flat=True))
        unread = [u for u in aud.select_related("department").order_by("username") if u.pk not in read_ids]
        total = aud.count()
        ctx.update(total=total, read_n=total - len(unread), unread_users=unread, show_stats=True,
                   targets=[d.name for d in a.departments.all()])
    return render(request, "announcement_detail.html", ctx)


@login_required
@require_roles(*VIEW_ROLES)
def announcement_file(request, pk):
    a = _get(request.user, pk)  # izin file = izin pengumuman
    if not a.attachment: raise Http404
    log(request, "hr", "announcement_download", a)
    return FileResponse(a.attachment.open("rb"), as_attachment=True, filename=a.attachment.name.rsplit("/", 1)[-1])
