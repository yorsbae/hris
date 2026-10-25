"""Data standar Payroll yang di sistem dibuat OLEH MIGRASI (bukan oleh seed): komponen slip. `manage.py flush` menghapusnya dan migrasi tidak jalan lagi,
sehingga run gaji tidak bisa membentuk slip. `ensure_components()` membuatnya ulang — idempoten, tidak menimpa suntingan (get_or_create per kode)."""
import importlib


# Komponen tambahan setelah migrasi 0002 (putaran 60): (kode, label, bagian, jenis, urut, boleh negatif)
EXTRA = [("thr", "THR", "lain", "manual", 64, False), ("pot_pph21", "PPh 21", "potongan", "auto", 79, False)]


def ensure_components():
    """→ jumlah komponen yang DIBUAT (0 bila semua sudah ada). Daftar diambil dari migrasi 0002 agar tidak ada dua sumber."""
    from .models import PayComponent
    mig = importlib.import_module("apps.payroll.migrations.0002_seed_components"); n = 0
    for code, label, section, kind, order, signed in list(mig.COMPONENTS) + EXTRA:
        _, created = PayComponent.objects.get_or_create(code=code, defaults={"label": label, "section": section, "kind": kind, "order": order, "signed": signed}); n += created
    return n
