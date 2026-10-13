"""Rumus gaji sebagai FUNGSI MURNI (tanpa database) — mudah diuji dan dibaca. Mesin yang mengambil data dan merangkai baris slip ada di engine.py.

Pembulatan (diturunkan dari slip contoh Okt-2025 dan dikunci tes):
  • pendapatan  → ke rupiah terdekat (ROUND_HALF_UP)       : 2.534.426,1 → 2.534.426 · 161.148,47 → 161.148 · 168.961,74 → 168.962
  • potongan %  → ke BAWAH ke kelipatan yang ditetapkan     : Jamsostek 76.032,78 → 76.030 (kelipatan 10) · BPJS 50.688,52 → 50.688 (kelipatan 1)"""
import re
from datetime import date, timedelta
from decimal import Decimal as D, ROUND_DOWN, ROUND_HALF_UP

ZERO = D(0)


def dec(v, default=ZERO):
    if v is None or v == "": return default
    return v if isinstance(v, D) else D(str(v))


def q0(x): return dec(x).quantize(D(1), rounding=ROUND_HALF_UP)
def q1(x): return dec(x).quantize(D("0.1"), rounding=ROUND_HALF_UP)
def q2(x): return dec(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def floor_to(x, step):
    """Bulatkan ke bawah ke kelipatan `step` (≥ 1). Nilai negatif tidak dipakai (potongan selalu ≥ 0)."""
    step = int(dec(step)) or 1
    return D((int(dec(x).to_integral_value(rounding=ROUND_DOWN)) // step) * step)


# ---------------------------------------------------------------- periode
_PERIOD = re.compile(r"^(\d{4})-(\d{2})$")


def valid_period(p):
    m = _PERIOD.match(p or "")
    return bool(m and 2000 <= int(m[1]) <= 2100 and 1 <= int(m[2]) <= 12)


def period_bounds(p):
    """'2026-10' → (2026-10-01, 2026-10-31)."""
    if not valid_period(p): raise ValueError("Periode harus berformat YYYY-MM.")
    y, m = int(p[:4]), int(p[5:])
    start = date(y, m, 1)
    end = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
    return start, end


def period_label(p):
    """'2025-10' → 'Oct-25' (seperti slip contoh)."""
    s, _ = period_bounds(p); return s.strftime("%b-%y")


def full_years(join, on):
    """Masa kerja penuh (tahun) pada tanggal `on`."""
    if join is None or on < join: return 0
    return on.year - join.year - ((on.month, on.day) < (join.month, join.day))


def paid_days(join, last, start, end, basis):
    """Hari dibayar. Sebulan penuh bekerja → `basis` (mis. 30) berapa pun hari kalendernya; masuk/keluar di tengah periode → hari kalender bekerja (maks `basis`)."""
    basis = dec(basis)
    first, final = max(start, join), (min(end, last) if last else end)
    if final < first: return ZERO
    if first == start and final == end: return basis
    return min(basis, D((final - first).days + 1))


# ---------------------------------------------------------------- upah & lembur
def hourly_rate(daily, basis, divisor):
    """Upah per jam = (upah harian × hari dasar) ÷ pembagi. Tidak dibulatkan (pembulatan di jumlah akhir)."""
    return dec(daily) * dec(basis) / dec(divisor)


def weighted_hours(day_minutes, first_mult, next_mult):
    """Jam lembur TERBOBOT: tiap hari, jam pertama × `first_mult` dan jam berikutnya × `next_mult` (Kepmenaker 102/2004: 1,5 dan 2). Menit dijumlah per hari lalu dibobot;
    hasil dibulatkan 2 desimal. `day_minutes` = menit lembur terbukti per hari (≤ 0 diabaikan)."""
    total = ZERO
    for m in day_minutes:
        if not m or m <= 0: continue
        h = D(int(m)) / D(60)
        total += min(h, D(1)) * dec(first_mult) + max(h - 1, ZERO) * dec(next_mult)
    return q2(total)


def fmt_qty(v, places=1):
    """Angka Indonesia: koma sebagai pemisah desimal (30,0 · 11,0 · 2,5)."""
    if v is None: return ""
    return f"{dec(v):.{places}f}".replace(".", ",")
