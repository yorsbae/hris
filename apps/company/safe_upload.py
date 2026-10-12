"""Validasi unggahan, pemindai antivirus (ClamAV, opsional), dan penyimpanan privat terenkripsi.
Prinsip (VISION Tahap 11): ekstensi + magic bytes + ukuran + nama acak; PDF berskrip ditolak; berkas CV tidak pernah disajikan langsung —
disimpan terenkripsi di PRIVATE_MEDIA_ROOT dan hanya diunduh lewat view HRD (attachment, nosniff)."""
import hashlib
import io
import socket
import struct
import uuid
import zipfile
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from apps.core.crypto import _fernet

CV_TYPES = {"pdf": b"%PDF-", "jpg": b"\xff\xd8\xff", "jpeg": b"\xff\xd8\xff", "png": b"\x89PNG\r\n\x1a\n", "doc": b"\xd0\xcf\x11\xe0", "docx": b"PK\x03\x04"}
IMAGE_TYPES = {"jpg": b"\xff\xd8\xff", "jpeg": b"\xff\xd8\xff", "png": b"\x89PNG\r\n\x1a\n", "webp": b"RIFF"}
PDF_BAD = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/OpenAction", b"/AA")


def _ext(name): return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def check_content(name, data, allowed, max_bytes):
    """Kembalikan ekstensi bila lolos; ValidationError (pesan ramah pengguna) bila tidak."""
    ext = _ext(name)
    if ext not in allowed: raise ValidationError("Jenis berkas tidak diizinkan. Gunakan: " + ", ".join(sorted({e.upper() for e in allowed})) + ".")
    if not data: raise ValidationError("Berkas kosong.")
    if len(data) > max_bytes: raise ValidationError(f"Berkas terlalu besar (maksimal {max_bytes // (1024 * 1024)} MB).")
    if not data.startswith(allowed[ext]): raise ValidationError("Isi berkas tidak sesuai dengan jenisnya.")
    if ext == "webp" and data[8:12] != b"WEBP": raise ValidationError("Isi berkas tidak sesuai dengan jenisnya.")
    if ext == "pdf" and any(t in data for t in PDF_BAD): raise ValidationError("PDF berisi skrip/lampiran aktif dan ditolak. Simpan ulang sebagai PDF biasa.")
    if ext == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if not any(n.startswith("word/") for n in names) or any(n.lower().endswith("vbaproject.bin") for n in names): raise ValueError
        except (zipfile.BadZipFile, ValueError):
            raise ValidationError("Berkas DOCX tidak valid atau berisi makro.")
    return ext


def validate_cv(upload):
    data = upload.read(); upload.seek(0)
    return data, check_content(upload.name, data, CV_TYPES, settings.CAREER_MAX_UPLOAD_MB * 1024 * 1024)


def validate_image(upload, max_mb=3):
    """Gambar situs (produk/artikel/logo) yang disajikan PUBLIK: hanya jpg/png/webp, bukan SVG (SVG dapat memuat skrip)."""
    data = upload.read(); upload.seek(0)
    return data, check_content(upload.name, data, IMAGE_TYPES, max_mb * 1024 * 1024)


# ------------------------------------------------------------------ Antivirus (clamd INSTREAM)
class ScanUnavailable(Exception): pass


def _clamd_connect():
    addr = settings.CLAMAV_ADDRESS
    if addr.startswith("unix:"):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(15); s.connect(addr[5:]); return s
    host, _, port = addr.replace("tcp:", "").partition(":")
    return socket.create_connection((host or "127.0.0.1", int(port or 3310)), timeout=15)


def scan(data):
    """'clean' | 'unscanned' (tanpa pemindai) ; ValidationError bila terdeteksi; ScanUnavailable bila pemindai dikonfigurasi tapi tidak menjawab."""
    if not settings.CLAMAV_ADDRESS: return "unscanned"
    try:
        s = _clamd_connect()
        with s:
            s.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), 8192):
                chunk = data[i:i + 8192]; s.sendall(struct.pack("!I", len(chunk)) + chunk)
            s.sendall(struct.pack("!I", 0))
            resp = b""
            while not resp.endswith(b"\0") and len(resp) < 4096:
                part = s.recv(1024)
                if not part: break
                resp += part
    except OSError as e:
        raise ScanUnavailable(str(e))
    text = resp.rstrip(b"\0").decode("utf-8", "replace")
    if text.endswith("OK"): return "clean"
    if "FOUND" in text: raise ValidationError("Berkas ditolak oleh pemindai keamanan. Periksa berkas Anda atau gunakan berkas lain.")
    raise ScanUnavailable(text)


# ------------------------------------------------------------------ Penyimpanan privat terenkripsi
def _root(): return Path(settings.PRIVATE_MEDIA_ROOT)


def store_private(data, ext):
    """Simpan terenkripsi; kembalikan (path relatif, sha256 isi asli)."""
    rel = f"career/{uuid.uuid4().hex}.bin"
    dest = _root() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_fernet().encrypt(data))
    try: dest.chmod(0o600)
    except OSError: pass
    return rel, hashlib.sha256(data).hexdigest()


def read_private(rel):
    p = (_root() / rel).resolve()
    if not str(p).startswith(str(_root().resolve())): raise FileNotFoundError(rel)   # tidak keluar dari folder privat
    return _fernet().decrypt(p.read_bytes())


def delete_private(rel):
    if not rel: return
    p = (_root() / rel).resolve()
    if str(p).startswith(str(_root().resolve())) and p.exists(): p.unlink()
