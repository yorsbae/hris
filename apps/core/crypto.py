"""Enkripsi kolom sensitif (Fernet/AES-128-CBC + HMAC). Kunci di .env: FIELD_ENCRYPTION_KEY (boleh beberapa, dipisah koma;
kunci pertama dipakai menulis, semua kunci dipakai membaca → rotasi kunci tanpa downtime).

Catatan penting:
- Nilai kosong tidak dienkripsi. Nilai lama yang masih plaintext tetap terbaca (kompatibilitas migrasi); jalankan
  `python manage.py encrypt_sensitive` untuk mengenkripsi semuanya.
- Enkripsi non-deterministik: kolom ini TIDAK bisa dicari/di-filter lewat SQL. Pencarian memakai NIK induk & nama.
"""
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

PREFIX = "enc1:"


def _fernet():
    keys = [k.strip() for k in (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").split(",") if k.strip()]
    if not keys:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY belum diisi (.env). Buat: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
    return MultiFernet([Fernet(k.encode()) for k in keys])


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value):
    if value in (None, "") or is_encrypted(value): return value
    return PREFIX + _fernet().encrypt(str(value).encode()).decode()


def decrypt(value):
    if not is_encrypted(value): return value  # kosong / plaintext lama
    try: return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken: raise ImproperlyConfigured("Data terenkripsi tidak dapat dibuka: FIELD_ENCRYPTION_KEY salah/berubah.")


class EncryptedTextField(models.TextField):
    """Dienkripsi saat ditulis ke DB, didekripsi saat dibaca. `max_length` hanya validasi form (panjang teks asli)."""
    def from_db_value(self, value, expression, connection): return decrypt(value)
    def get_prep_value(self, value): return encrypt(super().get_prep_value(value))
