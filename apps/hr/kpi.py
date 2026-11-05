"""KPI karyawan (menu KPI). Saat ini fokus ke KPI KEDISIPLINAN yang datanya sudah ada di sistem: rekap absensi (AttendanceDaily) + Surat Peringatan (hrd.WarningLetter).
Skor = 100 dikurangi poin per pelanggaran (aturan di RULES, ubah di satu tempat), dibatasi 0-100. Semua angka bisa ditelusuri ke rekap absensi bulanan; tidak ada data baru yang disimpan."""
from datetime import date, timedelta
from django.db.models import Count, Q
from . import attendance_calc as calc
from .models import AttendanceDaily as AD, PerformanceReview

RULES = {"alfa": 10, "terlambat": 2, "pulang_cepat": 2, "izin": 1,          # poin dikurangi per hari/kali
         "sp": {1: 10, 2: 20, 3: 30}}                                        # per tingkat SP aktif (diambil yang tertinggi)
GRADES = [(90, "A", "Sangat baik", "approved"), (75, "B", "Baik", "approved"), (60, "C", "Perlu pembinaan", "pending"), (0, "D", "Kurang", "rejected")]
W_DISC, W_REVIEW = 70, 30   # bobot skor akhir bila ada penilaian atasan (jumlah 100); tanpa penilaian: 100% kedisiplinan
ALFA_SP = 3          # alfa dalam sebulan >= angka ini -> disarankan SP
LATE_WARN = 8        # terlambat >= angka ini dalam sebulan -> disarankan teguran
ALFA_FREQ_3M = 4     # alfa dalam 3 bulan >= angka ini -> ditandai "sering alfa"


def grade(score):
    for lo, g, label, pill in GRADES:
        if score >= lo: return g, label, pill
    return "D", "Kurang", "rejected"


def active_sp(employee_ids, ref):
    """{employee_id: tingkat SP aktif tertinggi pada tanggal ref}."""
    from apps.hrd.models import WarningLetter
    out = {}
    for eid, lv in WarningLetter.objects.filter(employee_id__in=employee_ids, revoked_at__isnull=True, issue_date__lte=ref, valid_until__gte=ref).values_list("employee_id", "level"):
        out[eid] = max(lv, out.get(eid, 0))
    return out


def _three_month_start(a):
    y, m = a.year, a.month - 2
    if m < 1: y, m = y - 1, m + 12
    return date(y, m, 1)


def suggest(r):
    """Saran tindakan sederhana (bukan keputusan otomatis; HRD yang memutuskan). Saran SP mengisi r['saran_level'] untuk tombol Buat SP."""
    sp, alfa = r["sp"], r["alfa"]
    if alfa >= ALFA_SP:
        if sp < 3: r["saran_level"] = sp + 1; return f"SP {sp + 1}"
        return "Tinjau SP 3 / PHK"
    if r["alfa3"] >= ALFA_FREQ_3M: return "Panggil & bina (sering alfa)"
    if alfa >= 1: return "Teguran lisan"
    if r["terlambat"] >= LATE_WARN: return "Teguran (sering terlambat)"
    return ""


def build(qs_daily, a, b, today=None, label=""):
    """qs_daily: AttendanceDaily bulan [a,b] yang sudah di-scope. Mengembalikan baris per karyawan lengkap dengan skor, grade, SP, saran; urut skor terendah dulu."""
    today = today or date.today(); ref = min(b, today) if a <= today else b
    rows = calc.summary(qs_daily)
    ids = [r["employee_id"] for r in rows]
    sp = active_sp(ids, ref)
    alfa3 = dict(AD.objects.filter(employee_id__in=ids, date__gte=_three_month_start(a), date__lte=b, status="alfa").values("employee_id").annotate(n=Count("id")).values_list("employee_id", "n"))
    rev = {x.employee_id: x for x in PerformanceReview.objects.filter(employee_id__in=ids, period=f"{a:%Y-%m}")}
    out = []
    for r in rows:
        r["sp"] = sp.get(r["employee_id"], 0); r["alfa3"] = alfa3.get(r["employee_id"], 0); r["saran_level"] = 0
        r["review"] = rev[r["employee_id"]].score if r["employee_id"] in rev else None
        if not r["hari"]: r["score"] = None; r["grade"], r["glabel"], r["pill"] = "-", "Belum ada data", "nonaktif"
        else:
            pts = r["alfa"] * RULES["alfa"] + r["terlambat"] * RULES["terlambat"] + r["pulang_cepat"] * RULES["pulang_cepat"] + r["izin"] * RULES["izin"] + RULES["sp"].get(r["sp"], 0)
            r["disc"] = max(0, 100 - pts)
            r["score"] = round((W_DISC * r["disc"] + W_REVIEW * r["review"]) / 100) if r["review"] is not None else r["disc"]
            r["grade"], r["glabel"], r["pill"] = grade(r["score"])
        r["pct_hadir"] = round(100 * r["hadir"] / r["hari"]) if r["hari"] else None
        r["saran"] = suggest(r)
        r["violation"] = f"Alfa {r['alfa']} hari pada {label}"[:200] if r["saran_level"] else ""
        r["sp_desc"] = f"Rekap absensi {label}: alfa {r['alfa']} hari, terlambat {r['terlambat']} kali; alfa 3 bulan terakhir {r['alfa3']} hari. Dibuat dari menu KPI."
        out.append(r)
    out.sort(key=lambda r: (r["score"] is None, r["score"] if r["score"] is not None else 0, r["employee__name"]))
    return out


def totals(rows):
    sc = [r["score"] for r in rows if r["score"] is not None]
    return {"n": len(rows), "avg": round(sum(sc) / len(sc)) if sc else None, "alfa": sum(r["alfa"] for r in rows), "terlambat": sum(r["terlambat"] for r in rows),
            "sp": sum(1 for r in rows if r["sp"]), "tindak": sum(1 for r in rows if r["saran"]),
            "grades": {g: sum(1 for r in rows if r["grade"] == g) for g in "ABCD"}}


def by_department(rows):
    d = {}
    for r in rows:
        x = d.setdefault(r["department__name"] or "-", {"name": r["department__name"] or "-", "n": 0, "scores": [], "hadir": 0, "hari": 0, "alfa": 0, "terlambat": 0, "sp": 0})
        x["n"] += 1; x["hadir"] += r["hadir"]; x["hari"] += r["hari"]; x["alfa"] += r["alfa"]; x["terlambat"] += r["terlambat"]; x["sp"] += 1 if r["sp"] else 0
        if r["score"] is not None: x["scores"].append(r["score"])
    out = []
    for x in d.values():
        x["avg"] = round(sum(x["scores"]) / len(x["scores"])) if x["scores"] else None
        x["pct_hadir"] = round(100 * x["hadir"] / x["hari"]) if x["hari"] else None
        x["grade"], x["glabel"], x["pill"] = grade(x["avg"]) if x["avg"] is not None else ("-", "", "nonaktif")
        out.append(x)
    return sorted(out, key=lambda x: (x["avg"] is None, x["avg"] or 0))


def add_months(d, n):
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, 1)


def insight(trend, sp_level):
    """Bantuan keputusan perpanjang kontrak/pengangkatan dari tren skor (bukan keputusan). trend: list skor bulanan (None = tanpa data), terbaru terakhir."""
    sc = [x for x in trend[-3:] if x is not None]
    if len(sc) < 2: return ("nonaktif", "Data belum cukup (butuh minimal 2 bulan).")
    avg = round(sum(sc) / len(sc))
    if sp_level >= 2 or avg < 60: return ("rejected", f"Rata-rata {avg}: perlu evaluasi sebelum perpanjang kontrak atau pengangkatan.")
    if avg >= 85 and not sp_level: return ("approved", f"Rata-rata {avg}: kedisiplinan baik, layak dipertimbangkan untuk perpanjang/pengangkatan.")
    return ("pending", f"Rata-rata {avg}: cukup, pantau dan bina bila ada penurunan.")
