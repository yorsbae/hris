"""Kalender (putaran 66): agenda rapat/event/pelatihan/pengingat. Cuti, libur, kontrak, ulang tahun diambil dari modul lain (lihat feed.py)."""
from django.conf import settings
from django.db import models


class AgendaEvent(models.Model):
    KINDS = [("rapat", "Rapat"), ("event", "Event"), ("pelatihan", "Pelatihan"), ("pengingat", "Pengingat")]
    AUD = [("semua", "Semua akun"), ("dept", "Satu departemen"), ("role", "Satu peran akun")]
    title = models.CharField("Judul", max_length=160); kind = models.CharField("Jenis", max_length=10, choices=KINDS, default="rapat", db_index=True)
    start_date = models.DateField("Mulai", db_index=True); end_date = models.DateField("Selesai", null=True, blank=True, help_text="Kosongkan bila satu hari.")
    start_time = models.TimeField("Jam mulai", null=True, blank=True); end_time = models.TimeField("Jam selesai", null=True, blank=True)
    location = models.CharField("Lokasi / tautan", max_length=160, blank=True); description = models.TextField("Keterangan", blank=True)
    audience = models.CharField("Dilihat oleh", max_length=6, choices=AUD, default="semua")
    department = models.ForeignKey("hr.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="Departemen")
    audience_role = models.CharField("Peran", max_length=20, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["start_date", "start_time", "id"]
        constraints = [models.CheckConstraint(condition=models.Q(end_date__isnull=True) | models.Q(end_date__gte=models.F("start_date")), name="agenda_range_valid")]
    def __str__(self): return self.title
    @property
    def last(self): return self.end_date or self.start_date
