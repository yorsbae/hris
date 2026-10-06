from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .models import Notification


@login_required
def notification_list(request):
    qs = Notification.objects.filter(user=request.user)  # hanya milik sendiri
    only_unread = request.GET.get("unread") == "1"
    if only_unread: qs = qs.filter(is_read=False)
    page = Paginator(qs.order_by("-created_at"), 30).get_page(request.GET.get("page", 1))
    return render(request, "notifications.html", {"page": page, "only_unread": only_unread})


@require_POST
@login_required
def notification_open(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)  # milik orang lain → 404
    if not n.is_read: n.is_read = True; n.save(update_fields=["is_read"])
    link = n.link
    return redirect(link if link.startswith("/") and not link.startswith("//") else "/notifications/")  # cegah open-redirect


@require_POST
@login_required
def notification_read_all(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return redirect("/notifications/")
