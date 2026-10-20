"""Tutup periode (tutup buku) bulanan + checklist akhir bulan. Satu sumber: dipakai halaman Periode, perhitungan absensi, dan tagihan Poli."""
import calendar
from datetime import date, timedelta
from django.db.models import Count, Q
from .models import PeriodClose


def key(d): return f"{d.year:04d}-{d.month:02d}"


def closed_keys():
    return set(PeriodClose.objects.filter(closed=True).values_list("period", flat=True))


def is_closed(d, closed=None):
    return key(d) in (closed_keys() if closed is None else closed)


def open_segments(start, end):
    """Pecah [start, end] menjadi rentang tanggal yang BULANNYA belum ditutup (rekap bulan tertutup tidak boleh berubah)."""
    closed = closed_keys()
    if not closed: return [(start, end)]
    out, cur = [], None
    d = start
    while d <= end:
        if key(d) in closed:
            if cur: out.append(cur); cur = None
            d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)   # lompat ke awal bulan berikutnya
            continue
        cur = (cur[0], d) if cur else (d, d); d += timedelta(days=1)
    if cur: out.append(cur)
    return out


def month_range(period):
    y, m = int(period[:4]), int(period[5:7])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def valid(period):
    try: y, m = int(period[:4]), int(period[5:7]); return period[4] == "-" and len(period) == 7 and 2000 < y < 2100 and 1 <= m <= 12
    except (ValueError, IndexError): return False


def checklist(period):
    """Butir yang harus beres sebelum menutup bulan. → list[dict(label, n, ok, url, blocking)]; blocking = menghalangi penutupan."""
    from .models import AttendanceCheck, AttendanceDaily as AD, AttendanceRaw, ChangeRequest
    a, b = month_range(period); today = date.today()
    rows = AD.objects.filter(date__gte=a, date__lte=b)
    items = [
        {"label": "Rekap absensi sudah diolah (ada data harian)", "n": 0 if rows.exists() else 1, "url": "/absensi/impor/", "blocking": True, "hint": "Impor scanlog lalu olah rekap."},
        {"label": "Hari 'Tanpa scan' belum jelas", "n": rows.filter(status="tanpa_scan").count(), "url": f"/absensi/?from={a}&to={b}&status=review", "blocking": True, "hint": "Minta validasi kehadiran atau koreksi."},
        {"label": "Absensi tidak lengkap / konflik (perlu dicek HRD)", "n": rows.filter(Q(incomplete=True) | ~Q(conflict="")).count(), "url": f"/absensi/?from={a}&to={b}&status=incomplete", "blocking": False, "hint": "Sebaiknya diperiksa dulu."},
        {"label": "Validasi kehadiran belum diverifikasi", "n": AttendanceCheck.objects.filter(date__gte=a, date__lte=b, status__in=("diminta", "dijawab", "dikembalikan")).count(), "url": "/validasi/", "blocking": True, "hint": "Selesaikan / verifikasi."},
        {"label": "Pengajuan masih berjalan (belum dilaksanakan)", "n": ChangeRequest.objects.filter(status__in=("submitted", "pending", "approved")).count(), "url": "/requests/", "blocking": False, "hint": "Cek pengajuan yang menunggu."},
    ]
    for it in items: it["ok"] = it["n"] == 0
    if b >= today: items.insert(0, {"label": "Bulan sudah berakhir", "n": 1, "ok": False, "url": "", "blocking": True, "hint": "Periode baru bisa ditutup setelah bulan berakhir."})
    return items
