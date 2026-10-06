"""Tahap 4 (Informasi): siapa yang berhak melihat pengumuman, siapa audiensnya, form pembuatan. Satu sumber aturan untuk view & notifikasi."""
import os
from django import forms
from django.db.models import Q
from apps.core.models import Notification, Role, User
from .models import Announcement, AnnouncementRead, Department

KINDS = {"pengumuman": "Pengumuman", "peraturan": "Peraturan", "pemberitahuan": "Pemberitahuan"}
MAX_UPLOAD = 5 * 1024 * 1024
ALLOWED_EXT = {".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg"}
FULL_VIEW = (Role.HRD, Role.SUPERADMIN)


def visible_to(user, qs=None):
    """Pengumuman yang boleh dilihat user. HRD/Superadmin: semua. Admin Dept: semua-departemen, departemennya, atau ditujukan langsung. Poli: semua-departemen atau langsung."""
    qs = Announcement.objects.all() if qs is None else qs
    if user.role in FULL_VIEW: return qs
    q = Q(recipients=user)
    if user.role in (Role.DEPT_ADMIN, Role.POLI): q |= Q(all_departments=True)
    if user.role == Role.DEPT_ADMIN: q |= Q(departments=user.department_id)
    return qs.filter(q).distinct()


def audience(a):
    """Penerima yang diharapkan membaca (dasar status sudah/belum dibaca & notifikasi). Harus konsisten dengan visible_to."""
    q = Q(pk__in=a.recipients.values("pk"))
    q |= Q(role__in=(Role.DEPT_ADMIN, Role.POLI)) if a.all_departments else Q(role=Role.DEPT_ADMIN, department__in=a.departments.all())
    return User.objects.filter(is_active=True).filter(q).distinct()


def notify(a, sender):
    users = audience(a).exclude(pk=sender.pk)
    Notification.objects.bulk_create([Notification(user=u, kind="rule" if a.kind == "peraturan" else "info",
        title=f"{KINDS.get(a.kind, a.kind)}: {a.title}"[:200], link=f"/announcements/{a.pk}/") for u in users])
    return users.count()


def unread_count(user):
    return visible_to(user).exclude(pk__in=AnnouncementRead.objects.filter(user=user).values("announcement_id")).count()


class AnnouncementForm(forms.ModelForm):
    kind = forms.ChoiceField(label="Jenis", choices=list(KINDS.items()))
    departments = forms.ModelMultipleChoiceField(Department.objects.order_by("name"), required=False, label="Departemen tujuan")
    recipients = forms.ModelMultipleChoiceField(User.objects.none(), required=False, label="Penerima tertentu (opsional)")

    class Meta:
        model = Announcement
        fields = ("kind", "title", "body", "all_departments", "departments", "recipients", "attachment")
        labels = {"title": "Judul", "body": "Isi", "all_departments": "Kirim ke semua departemen (termasuk Poli)", "attachment": "Lampiran (PDF/DOCX/XLSX/gambar, maks 5 MB)"}
        widgets = {"body": forms.Textarea(attrs={"rows": 8})}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["recipients"].queryset = User.objects.filter(is_active=True, role__in=(Role.DEPT_ADMIN, Role.POLI)).order_by("username")

    def clean_attachment(self):
        f = self.cleaned_data.get("attachment")
        if f:
            if os.path.splitext(f.name)[1].lower() not in ALLOWED_EXT: raise forms.ValidationError("Tipe file tidak diizinkan.")
            if f.size > MAX_UPLOAD: raise forms.ValidationError("Ukuran file maksimal 5 MB.")
        return f

    def clean(self):
        d = super().clean()
        if not (d.get("all_departments") or d.get("departments") or d.get("recipients")):
            raise forms.ValidationError("Pilih tujuan: semua departemen, departemen tertentu, atau penerima tertentu.")
        return d
