"""Bukti pengajuan (foto/berkas) — putaran 50. Opsional, ekstensi bebas, ukuran dibatasi hanya oleh konfigurasi (bukan daftar putih ekstensi).
Keamanan tanpa membatasi ekstensi: nama di disk acak, tidak pernah dieksekusi/di-serve statis; gambar dikenali dari ISI (magic bytes) dan
ditampilkan inline hanya untuk format raster yang aman (BUKAN SVG/HTML); selain itu selalu `attachment` + nosniff + CSP sandbox."""
import shutil
from pathlib import Path
from django.conf import settings
from django.utils.text import get_valid_filename
from django.http import FileResponse
from .models import AttendanceCheckAttachment, ChangeRequest, RequestAttachment

ATTACH_TYPES = tuple(ChangeRequest.TYPES)  # SEMUA jenis pengajuan boleh melampirkan bukti (opsional): surat dokter, SK/surat mutasi, tangkapan layar kesepakatan tukar, daftar hadir lembur. Dulu hanya Izin & Cuti + Stand By/Lembur.


class AttachmentError(ValueError):
    """Berkas gagal disimpan (disk penuh/tidak bisa ditulis). Turunan ValueError agar ditangkap sebagai galat pengguna, bukan 500."""


def max_bytes(): return int(getattr(settings, "REQUEST_ATTACH_MAX_MB", 50)) * 1024 * 1024
def max_files(): return int(getattr(settings, "REQUEST_ATTACH_MAX_FILES", 10))
def max_total_bytes(): return int(getattr(settings, "REQUEST_ATTACH_MAX_TOTAL_MB", 100)) * 1024 * 1024  # jumlah semua berkas dalam satu pengajuan


def free_bytes():
    """Ruang disk bebas di folder media; None bila tidak dapat diperiksa (pemeriksaan dilewati, bukan menolak)."""
    path = Path(settings.MEDIA_ROOT)
    try: return shutil.disk_usage(path if path.exists() else settings.BASE_DIR).free
    except OSError: return None


def sniff_image(head):
    """MIME gambar raster yang aman ditampilkan inline, dari isi file; '' bila bukan salah satunya."""
    if head.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"): return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP": return "image/webp"
    return ""


def check(files, existing=()):
    """Daftar pesan galat (kosong = lolos): jumlah, berkas kosong, ukuran per berkas & total, dan sisa ruang disk.
    `existing` = lampiran yang sudah tersimpan pada pengajuan itu (ikut dihitung untuk batas jumlah & total, tidak diperiksa ulang)."""
    errs = []
    if len(files) + len(existing) > max_files(): errs.append(f"Maksimal {max_files()} berkas per pengajuan.")
    for f in files:
        if f.size == 0: errs.append(f"Berkas \u201c{f.name[:60]}\u201d kosong.")
        elif f.size > max_bytes(): errs.append(f"Berkas \u201c{f.name[:60]}\u201d melebihi {max_bytes() // 1048576} MB.")
    new = sum(f.size for f in files)
    if new + sum(e.size for e in existing) > max_total_bytes(): errs.append(f"Total ukuran berkas melebihi {max_total_bytes() // 1048576} MB per pengajuan.")
    free = free_bytes()
    if files and free is not None and free - new < int(getattr(settings, "HEALTH_MIN_FREE_MB", 500)) * 1024 * 1024:
        errs.append("Penyimpanan server hampir penuh, bukti belum dapat disimpan. Hubungi admin sistem (pengajuan tanpa bukti tetap bisa dikirim).")
    return errs


def _discard(rows):
    """Buang berkas yang sudah sempat tertulis di disk (barisnya ikut batal bersama transaksi pemanggil)."""
    for a in rows:
        try: a.file.storage.delete(a.file.name)
        except Exception: pass


def _write_all(files, make):
    """Tulis semua berkas atau tidak sama sekali: galat I/O (disk penuh, izin tulis) → berkas yang sudah tertulis dibuang, AttachmentError."""
    out = []
    try:
        for f in files:
            head = f.read(16); f.seek(0)
            name = get_valid_filename(f.name.replace("\\", "/").split("/")[-1])[:200] or "bukti"
            out.append(make(f, name, sniff_image(head)))
    except OSError as e:
        _discard(out)
        raise AttachmentError("Berkas gagal disimpan (penyimpanan server penuh atau tidak dapat ditulis). Tidak ada yang tersimpan; hubungi admin sistem.") from e
    return out


def save(req, files, user):
    return _write_all(files, lambda f, name, ct: RequestAttachment.objects.create(request=req, file=f, original_name=name, content_type=ct, size=f.size, uploaded_by=user))


def save_batch(reqs, files, user):
    """Stand By & Lembur (satu regu = banyak pengajuan, satu bukti): berkas disimpan SEKALI di disk, tiap pengajuan mendapat baris lampiran
    yang menunjuk berkas yang sama (berkas tidak pernah dihapus, jadi aman dipakai bersama)."""
    if not files or not reqs: return []
    first = save(reqs[0], files, user)
    for r in reqs[1:]:
        for a in first:
            RequestAttachment.objects.create(request=r, file=a.file.name, original_name=a.original_name, content_type=a.content_type, size=a.size, uploaded_by=user)
    return first


def save_check(check, files, user):
    """Bukti jawaban Validasi Kehadiran (mis. foto surat dokter / daftar hadir). Aturan sama dengan bukti pengajuan."""
    return _write_all(files, lambda f, name, ct: AttendanceCheckAttachment.objects.create(att=check, file=f, original_name=name, content_type=ct, size=f.size, uploaded_by=user))


def serve(a, download=False):
    """Respons aman untuk satu lampiran (pengajuan atau validasi): gambar raster dikenali dari isi tampil inline, selain itu selalu diunduh."""
    inline = a.is_image and not download
    resp = FileResponse(a.file.open("rb"), as_attachment=not inline, filename=a.original_name, content_type=a.content_type if inline else "application/octet-stream")
    resp["X-Content-Type-Options"] = "nosniff"; resp["Content-Security-Policy"] = "sandbox; default-src 'none'"; resp["Cache-Control"] = "private, no-store"
    return resp
