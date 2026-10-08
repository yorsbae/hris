from django import template
from apps.core.money import format_rupiah

register = template.Library()


@register.filter
def rupiah(value):
    """`{{ x|rupiah }}` → Rp 1.500.000 (tanpa ,00). Kosong/None → Rp 0. Nilai yang bukan angka tampil apa adanya (jangan sampai halaman 500)."""
    try: return format_rupiah(value)
    except (TypeError, ValueError, ArithmeticError): return value


@register.filter
def rupiah_plain(value):
    """Tanpa awalan 'Rp ' (untuk kolom yang judulnya sudah memuat (Rp))."""
    try: return format_rupiah(value, symbol=False)
    except (TypeError, ValueError, ArithmeticError): return value
