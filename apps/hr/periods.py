"""Tutup periode (tutup buku) dengan RENTANG TANGGAL BEBAS + checklist + serah-terima ke Payroll (putaran 59).
Satu sumber: dipakai halaman Tutup Periode, perhitungan absensi, tagihan Poli, dan Payroll (rentang data gaji bulan tertentu).

  • Rentang tertutup tidak boleh saling tumpang tindih; satu label gaji (YYYY-MM) hanya boleh punya satu periode TERTUTUP.
  • Menutup = membuat baris riwayat + snapshot XLSX 5 sheet (period_export.py) dalam SATU transaksi: tanpa berkas serah-terima, periode tidak tertutup.
  • Membuka kembali hanya Superadmin (dicek di view); baris lama tetap tersimpan sebagai riwayat."""
import calendar
from datetime import date, timedelta

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import PeriodClose

MAX_DAYS = 93   # pagar salah ketik (mis. tahun keliru): satu periode gaji tidak lebih dari ±3 bulan
ONE = timedelta(days=1)


def key(d): return f"{d.year:04d}-{d.month:02d}"


def month_range(period):
    y, m = int(period[:4]), int(period[5:7])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def valid(period):
    try: y, m = int(period[:4]), int(period[5:7]); return period[4] == "-" and len(period) == 7 and 2000 < y < 2100 and 1 <= m <= 12
    except (ValueError, IndexError, TypeError): return False


def fmt_range(start, end): return f"{start:%d-%m-%Y} s/d {end:%d-%m-%Y}"


# ---------------------------------------------------------------- penguncian
def closed_ranges():
    """[(mulai, akhir)] semua periode yang sedang TERTUTUP, urut mulai."""
    return sorted(PeriodClose.objects.filter(closed=True).values_list("start_date", "end_date"))


def is_closed(d, ranges=None):
    """Apakah tanggal `d` berada di dalam rentang yang sudah ditutup? `ranges` (hasil closed_ranges) boleh diberikan agar tidak query berulang."""
    return any(a <= d <= b for a, b in (closed_ranges() if ranges is None else ranges))


def closed_row_for(d):
    """Baris periode tertutup yang memuat tanggal `d` (untuk pesan galat), atau None."""
    return PeriodClose.objects.filter(closed=True, start_date__lte=d, end_date__gte=d).first()


def open_segments(start, end):
    """Pecah [start, end] menjadi rentang tanggal yang BELUM ditutup (rekap pada rentang tertutup tidak boleh berubah)."""
    out, cur = [], start
    for a, b in closed_ranges():
        if b < cur or a > end: continue
        if a > cur: out.append((cur, a - ONE))
        cur = max(cur, b + ONE)
        if cur > end: break
    if cur <= end: out.append((cur, end))
    return out


def overlapping(start, end, exclude_pk=None):
    """Periode tertutup yang bersinggungan dengan [start, end]."""
    qs = PeriodClose.objects.filter(closed=True, start_date__lte=end, end_date__gte=start)
    return qs.exclude(pk=exclude_pk) if exclude_pk else qs


def closed_for_label(period): return PeriodClose.objects.filter(closed=True, period=period).first()


def bounds_for(period):
    """Rentang data untuk bulan gaji `period` (YYYY-MM): rentang periode tertutup bila ada, selain itu satu bulan kalender. Dipakai Payroll."""
    row = closed_for_label(period)
    return (row.start_date, row.end_date) if row else month_range(period)


def next_start():
    """Usulan tanggal mulai periode berikutnya = sehari setelah akhir periode tertutup terakhir (None bila belum ada)."""
    last = PeriodClose.objects.filter(closed=True).order_by("-end_date").values_list("end_date", flat=True).first()
    return last + ONE if last else None


# ---------------------------------------------------------------- checklist
def checklist(start, end, label=None):
    """Butir yang harus beres sebelum menutup rentang. → list[dict(label, n, ok, url, blocking, hint)]; blocking = menghalangi penutupan (kecuali 'tutup paksa')."""
    from .models import AttendanceCheck, AttendanceDaily as AD, ChangeRequest
    today = timezone.localdate()
    rows = AD.objects.filter(date__gte=start, date__lte=end)
    items = [
        {"label": "Rekap absensi sudah diolah (ada data harian)", "n": 0 if rows.exists() else 1, "url": "/absensi/impor/", "blocking": True, "hint": "Impor scanlog lalu olah rekap."},
        {"label": "Hari 'Tanpa scan' belum jelas", "n": rows.filter(status="tanpa_scan").count(), "url": f"/absensi/?from={start}&to={end}&status=review", "blocking": True, "hint": "Minta validasi kehadiran atau koreksi."},
        {"label": "Absensi tidak lengkap / konflik (perlu dicek HRD)", "n": rows.filter(Q(incomplete=True) | ~Q(conflict="")).count(), "url": f"/absensi/?from={start}&to={end}&status=incomplete", "blocking": False, "hint": "Sebaiknya diperiksa dulu."},
        {"label": "Validasi kehadiran belum diverifikasi", "n": AttendanceCheck.objects.filter(date__gte=start, date__lte=end, status__in=("diminta", "dijawab", "dikembalikan")).count(), "url": "/validasi/", "blocking": True, "hint": "Selesaikan / verifikasi."},
        {"label": "Pengajuan masih berjalan (belum dilaksanakan)", "n": ChangeRequest.objects.filter(status__in=("submitted", "pending", "approved")).count(), "url": "/requests/", "blocking": False, "hint": "Cek pengajuan yang menunggu."},
    ]
    if label and valid(label):
        from apps.hrd.models import BpjsDeduction, BpjsMembership
        members = BpjsMembership.objects.filter(status="aktif").values("employee_id").distinct().count()
        missing = 1 if members and not BpjsDeduction.objects.filter(period=label).exists() else 0
        items.append({"label": f"Potongan BPJS untuk gaji {label} belum diisi", "n": missing, "url": "/hrd/bpjs/deductions/", "blocking": False, "hint": "Payroll memakai hitungan persen bila kosong; isi bila BPJS menagih angka pasti."})
    for it in items: it["ok"] = it["n"] == 0
    if end >= today: items.insert(0, {"label": "Tanggal akhir periode sudah lewat", "n": 1, "ok": False, "url": "", "blocking": True, "hint": "Periode baru bisa ditutup setelah tanggal akhirnya lewat."})
    return items


def blockers(items): return [i for i in items if i["blocking"] and not i["ok"]]


# ---------------------------------------------------------------- tutup / buka
def close(start, end, label, user, note="", force=False):
    """Tutup rentang [start, end] untuk gaji bulan `label`. Atomik: baris riwayat + snapshot XLSX. Raise ValueError (pesan siap tampil) bila ditolak.
    → (PeriodClose, forced: bool)."""
    from . import period_export
    if not (start and end): raise ValueError("Isi tanggal mulai dan tanggal akhir.")
    if end < start: raise ValueError("Tanggal akhir tidak boleh sebelum tanggal mulai.")
    if (end - start).days + 1 > MAX_DAYS: raise ValueError(f"Rentang terlalu panjang (maks {MAX_DAYS} hari). Periksa tahun/tanggal yang diisi.")
    if end >= timezone.localdate(): raise ValueError("Tanggal akhir harus sudah lewat (hari ini belum selesai). Periode tidak bisa mengunci tanggal yang belum berlalu, walau dipaksa.")
    label = (label or "").strip() or key(end)
    if not valid(label): raise ValueError("Label gaji harus berformat YYYY-MM (mis. 2026-10).")
    with transaction.atomic():
        clash = overlapping(start, end).first()
        if clash: raise ValueError(f"Rentang bertumpuk dengan periode yang sudah ditutup ({fmt_range(clash.start_date, clash.end_date)}, gaji {clash.period}). Buka kembali periode itu (Superadmin) atau sesuaikan tanggal.")
        taken = closed_for_label(label)
        if taken: raise ValueError(f"Gaji {label} sudah punya periode tertutup ({fmt_range(taken.start_date, taken.end_date)}). Pilih label bulan gaji lain atau buka kembali periode itu (Superadmin).")
        stops = blockers(checklist(start, end, label)); forced = bool(stops)
        if stops and not force: raise ValueError("Belum bisa ditutup: " + "; ".join(f"{i['label']} ({i['n']})" for i in stops) + ". Selesaikan dulu, atau centang 'Tutup paksa'.")
        row = PeriodClose(period=label, start_date=start, end_date=end, closed=True, note=(note or "").strip()[:300], closed_by=user, closed_at=timezone.now())
        try:
            with transaction.atomic(): row.save()
        except IntegrityError: raise ValueError(f"Gaji {label} sudah punya periode tertutup.")
        data, counts = period_export.build(start, end, label)
        row.export_file.save(f"periode-{start}-{end}.xlsx", ContentFile(data), save=False)
        row.export_at, row.export_counts = timezone.now(), counts
        row.save(update_fields=["export_file", "export_at", "export_counts"])
        _notify_payroll(f"Periode gaji {label} ditutup HRD ({fmt_range(start, end)}) — data siap diunduh", "/payroll/periods/")
    return row, forced


def reopen(pk, user):
    """Buka kembali periode tertutup (Superadmin; dicek di view). Berkas snapshot lama tetap sebagai riwayat. → PeriodClose atau raise ValueError."""
    with transaction.atomic():
        row = PeriodClose.objects.select_for_update().filter(pk=pk).first()
        if row is None: raise ValueError("Periode tidak ditemukan.")
        if not row.closed: raise ValueError("Periode ini sudah dalam keadaan terbuka.")
        row.closed, row.reopened_by, row.reopened_at = False, user, timezone.now(); row.save(update_fields=["closed", "reopened_by", "reopened_at"])
        _notify_payroll(f"Periode gaji {row.period} DIBUKA KEMBALI ({fmt_range(row.start_date, row.end_date)}) — data bisa berubah; tunggu penutupan ulang oleh HRD", "/payroll/periods/")
    return row


def rebuild(pk, user):
    """Buat ulang snapshot XLSX periode yang sedang TERTUTUP dari data terkini (mis. berkas periode lama belum ada, atau BPJS/seragam diperbaiki setelah penutupan). Payroll diberi tahu."""
    from . import period_export
    with transaction.atomic():
        row = PeriodClose.objects.select_for_update().filter(pk=pk).first()
        if row is None: raise ValueError("Periode tidak ditemukan.")
        if not row.closed: raise ValueError("Berkas hanya bisa dibuat ulang untuk periode yang sedang tertutup.")
        data, counts = period_export.build(row.start_date, row.end_date, row.period)
        old = row.export_file.name
        row.export_file.save(f"periode-{row.start_date}-{row.end_date}.xlsx", ContentFile(data), save=False)
        row.export_at, row.export_counts = timezone.now(), counts
        row.save(update_fields=["export_file", "export_at", "export_counts"])
        if old and old != row.export_file.name:
            storage = row.export_file.storage; transaction.on_commit(lambda: storage.delete(old))
        _notify_payroll(f"Berkas periode gaji {row.period} ({fmt_range(row.start_date, row.end_date)}) diperbarui HRD — unduh ulang", "/payroll/periods/")
    return row


def _notify_payroll(title, link):
    from apps.core import notify
    from apps.core.models import Role
    notify.send((Role.PAYROLL,), "payroll", title, link)
