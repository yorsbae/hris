"""Jalankan harian via cron: pengingat pengajuan Stand By & Lembur yang masih menunggu keputusan HRD (putaran 32).
Hanya yang tanggal kerjanya sudah tiba/lewat atau besok (paling mendesak). Satu notifikasi belum-dibaca per HRD per judul → tidak menumpuk."""
from datetime import date, timedelta
from django.core.management.base import BaseCommand
from apps.core.models import Notification, Role, User
from apps.hr.models import ChangeRequest


def run(today=None):
    today = today or date.today()
    qs = ChangeRequest.objects.filter(type__in=("standby", "lembur"), status="pending", payload__date__lte=(today + timedelta(days=1)).isoformat())
    n = qs.count()
    if not n: return 0
    late = qs.filter(payload__date__lt=today.isoformat()).count()
    title = f"Stand By & Lembur: {n} pengajuan menunggu keputusan" + (f" ({late} untuk tanggal yang sudah lewat)" if late else "")
    sent = 0
    for u in User.objects.filter(role=Role.HRD, is_active=True):
        if Notification.objects.filter(user=u, kind="request", is_read=False, title__startswith="Stand By & Lembur:").exists(): continue
        Notification.objects.create(user=u, kind="request", title=title, link="/requests/g/lembur/?status=pending"); sent += 1
    return sent


class Command(BaseCommand):
    help = "Pengingat pengajuan stand by/lembur yang belum diputuskan (jalankan harian via cron)"
    def handle(self, *a, **k): self.stdout.write(f"{run()} notifikasi dikirim")
