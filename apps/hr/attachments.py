"""Bukti pengajuan (foto/berkas) — putaran 50. Opsional, ekstensi bebas, ukuran dibatasi hanya oleh konfigurasi (bukan daftar putih ekstensi).
Keamanan tanpa membatasi ekstensi: nama di disk acak, tidak pernah dieksekusi/di-serve statis; gambar dikenali dari ISI (magic bytes) dan
ditampilkan inline hanya untuk format raster yang aman (BUKAN SVG/HTML); selain itu selalu `attachment` + nosniff + CSP sandbox."""
from django.conf import settings
from django.utils.text import get_valid_filename
from django.http import FileResponse
from .models import AttendanceCheckAttachment, RequestAttachment

ATTACH_TYPES = ("izin", "cuti", "sakit", "izin_terlambat", "izin_pulang", "izin_khusus", "standby", "lembur")  # jenis yang lazim butuh bukti: grup Izin & Cuti (putaran 50) + Stand By & Lembur (putaran 51)


def max_bytes(): return int(getattr(settings, "REQUEST_ATTACH_MAX_MB", 50)) * 1024 * 1024
def max_files(): return int(getattr(settings, "REQUEST_ATTACH_MAX_FILES", 10))


def sniff_image(head):
    """MIME gambar raster yang aman ditampilkan inline, dari isi file; '' bila bukan salah satunya."""
    if head.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"): return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP": return "image/webp"
    return ""


def check(files):
    """Daftar pesan galat (kosong = lolos): jumlah, berkas kosong, ukuran."""
    errs = []
    if len(files) > max_files(): errs.append(f"Maksimal {max_files()} berkas per pengajuan.")
    for f in files:
        if f.size == 0: errs.append(f"Berkas \u201c{f.name[:60]}\u201d kosong.")
        elif f.size > max_bytes(): errs.append(f"Berkas \u201c{f.name[:60]}\u201d melebihi {max_bytes() // 1048576} MB.")
    return errs


def save(req, files, user):
    out = []
    for f in files:
        head = f.read(16); f.seek(0)
        name = get_valid_filename(f.name.replace("\\", "/").split("/")[-1])[:200] or "bukti"
        out.append(RequestAttachment.objects.create(request=req, file=f, original_name=name, content_type=sniff_image(head), size=f.size, uploaded_by=user))
    return out


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
    out = []
    for f in files:
        head = f.read(16); f.seek(0)
        name = get_valid_filename(f.name.replace("\\", "/").split("/")[-1])[:200] or "bukti"
        out.append(AttendanceCheckAttachment.objects.create(att=check, file=f, original_name=name, content_type=sniff_image(head), size=f.size, uploaded_by=user))
    return out


def serve(a, download=False):
    """Respons aman untuk satu lampiran (pengajuan atau validasi): gambar raster dikenali dari isi tampil inline, selain itu selalu diunduh."""
    inline = a.is_image and not download
    resp = FileResponse(a.file.open("rb"), as_attachment=not inline, filename=a.original_name, content_type=a.content_type if inline else "application/octet-stream")
    resp["X-Content-Type-Options"] = "nosniff"; resp["Content-Security-Policy"] = "sandbox; default-src 'none'"; resp["Cache-Control"] = "private, no-store"
    return resp
