"""Format & penguraian RUPIAH (putaran 21, paket P2; asumsi A43).

Aturan: titik sebagai pemisah ribuan, TANPA ",00" (rupiah penuh tampil `Rp 1.500.000`). Desimal baru tampil bila memang ada (`Rp 1.500.000,50`).
Hanya untuk nilai rupiah — saldo cuti, suhu, stok, persen, dst. tidak memakai helper ini.
Satu sumber untuk: filter template (`{% load money %}`), field form (`RupiahField`), dan format angka XLSX (`XLSX_RUPIAH_FORMAT`)."""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import forms
from django.core.exceptions import ValidationError

XLSX_RUPIAH_FORMAT = '"Rp" #,##0'  # Excel menampilkan pemisah sesuai locale komputer; nilai sel tetap angka (bisa dijumlah)
_INT = re.compile(r"^(\d{1,3}(\.\d{3})+|\d+)$")


def _dec(value):
    if value is None or value == "": return Decimal(0)
    if isinstance(value, bool): raise TypeError("bool bukan rupiah")
    if isinstance(value, Decimal): return value
    if isinstance(value, int): return Decimal(value)
    if isinstance(value, float): return Decimal(str(value))
    return parse_rupiah(str(value), allow_negative=True, allow_decimal=True)


def _group(n: int) -> str: return f"{n:,}".replace(",", ".")


def format_rupiah(value, symbol=True) -> str:
    """1500000 → 'Rp 1.500.000'; None/'' → 'Rp 0'; negatif → '-Rp 1.500.000'; desimal hanya bila ada (maks 2 digit, dibulatkan)."""
    d = _dec(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    neg, d = d < 0, abs(d)
    whole, frac = int(d), int((d - int(d)) * 100)
    txt = _group(whole) + (f",{frac:02d}".rstrip("0") if frac else "")
    return ("-" if neg and d else "") + ("Rp " if symbol else "") + txt


def parse_rupiah(text: str, allow_negative=False, allow_decimal=False):
    """'1.500.000' / 'Rp 1.500.000' / 'Rp. 1500000' / '1.500.000,00' → int. Salah ketik (mis. '1.5', '1,500,000') → ValueError.
    Pecahan bukan nol ditolak kecuali allow_decimal (lalu Decimal)."""
    s = re.sub(r"\s+", "", str(text or ""))
    s = re.sub(r"^(-?)(?:rp\.?)", r"\1", s, flags=re.I)
    neg = s.startswith("-")
    if neg:
        if not allow_negative: raise ValueError("Nilai negatif tidak diperbolehkan.")
        s = s[1:]
    frac = ""
    if "," in s:
        s, frac = s.rsplit(",", 1)
        if not frac.isdigit() or len(frac) > 2: raise ValueError("Pecahan setelah koma harus 1–2 digit.")
    if not s or not _INT.match(s): raise ValueError("Format nominal tidak dikenali. Contoh: 1.500.000")
    whole = int(s.replace(".", ""))
    if frac and int(frac) != 0:
        if not allow_decimal: raise ValueError("Nominal harus rupiah penuh (tanpa sen).")
        out = Decimal(f"{whole}.{frac}")
        return -out if neg else out
    return -whole if neg else whole


class RupiahField(forms.IntegerField):
    """Input rupiah penuh: menerima `1500000`, `1.500.000`, `Rp 1.500.000`, `1.500.000,00`; menampilkan ulang `1.500.000` (tanpa ',00')."""
    default_error_messages = {"invalid": "Isi nominal rupiah yang valid, mis. 1.500.000."}

    def __init__(self, *a, **k):
        k.setdefault("widget", forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "off", "data-rupiah": "1", "placeholder": "mis. 1.500.000"}))
        super().__init__(*a, **k)

    def to_python(self, value):
        if value in self.empty_values: return None
        if isinstance(value, str):
            try: return parse_rupiah(value)
            except ValueError as e: raise ValidationError(str(e) if "Format" not in str(e) else self.error_messages["invalid"], code="invalid")
        return super().to_python(value)

    def prepare_value(self, value):
        return format_rupiah(value, symbol=False) if isinstance(value, int) and not isinstance(value, bool) else value

    def widget_attrs(self, widget): return {}  # IntegerField menambah min/max/step (khusus NumberInput); TextInput tidak memerlukannya
