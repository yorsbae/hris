"""Sinkronisasi HRIS (LAN) <-> Situs Publik (internet), tanpa membuka jalan masuk ke LAN.

  TURUN  HRIS -> publik : `publish_site` membuat bundle zip bertanda tangan (HMAC) berisi konten TERBIT + status lamaran; publik `import_site_bundle`.
  NAIK   publik -> HRIS : HRIS yang MENARIK lewat SSH (`pull_applications`), publik hanya menjawab `export_applications`. Tidak ada port/endpoint
         baru di internet, tidak ada koneksi masuk ke LAN.

Prinsip: (1) draf tidak pernah meninggalkan HRIS; (2) data pribadi pelamar di host publik diminimalkan setelah ditarik (bukti 'pulled');
(3) tiap host punya FIELD_ENCRYPTION_KEY sendiri (kunci HRIS tidak ada di host publik; data lintas host lewat SSH lalu dienkripsi ulang);
(4) bundle tidak pernah diekstrak ke disk apa adanya: tiap nama berkas divalidasi, tiap gambar diperiksa ulang (jenis + magic bytes + ukuran)."""
import base64
import hashlib
import hmac
import io
import json
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.fields.files import FieldFile
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from . import safe_upload
from .models import (Application, ApplicationEvent, Article, Faq, Job, JobStatus, Look, Motif, Product, ProductCategory, ProductColor, ProductImage,
                     SiteSetting, Page, StoreLocation, Stage)

CONTENT = [SiteSetting, Page, ProductCategory, Motif, Product, ProductColor, ProductImage, Look, Article, StoreLocation, Faq, Job]  # urutan = urutan impor
SKIP_FIELDS = {"updated_by", "department", "created_at", "updated_at"}   # penunjuk ke data HRIS (user/departemen) tidak ikut keluar
PUBLISHED = {"status": ["published"]}
MAX_BUNDLE_MB = 200
MEDIA_NAME = re.compile(r"^company/[a-z0-9_]+/[0-9a-f]{32}\.(jpg|jpeg|png|webp)$")
MEDIA_OK = {k: v for k, v in safe_upload.IMAGE_TYPES.items()}


class SyncError(Exception): pass


def _secret():
    s = getattr(settings, "SYNC_SHARED_SECRET", "")
    if len(s) < 32: raise SyncError("SYNC_SHARED_SECRET belum diisi / kurang dari 32 karakter (.env di kedua host harus sama).")
    return s.encode()


def _sign(data): return hmac.new(_secret(), data, hashlib.sha256).hexdigest()


def _plain(v):
    if isinstance(v, (datetime, date)): return v.isoformat()
    if isinstance(v, FieldFile): return v.name or ""
    return v


def _file_fields(model): return [f.name for f in model._meta.concrete_fields if isinstance(f, models.FileField)]


# ------------------------------------------------------------------ TURUN: HRIS -> publik
def build_bundle():
    """Kembalikan (bytes_zip, ringkasan). Hanya konten TERBIT; lowongan draf tidak ikut."""
    exported, media, data = {}, {}, {}
    for M in CONTENT:
        qs = M.objects.all()
        if M is Job: qs = qs.exclude(status=JobStatus.DRAFT)
        elif any(f.name == "status" for f in M._meta.fields): qs = qs.filter(status="published")
        if M in (ProductColor, ProductImage): qs = qs.filter(product_id__in=exported.get(Product, set()))
        rows, ids = [], set()
        for o in qs:
            r = {}
            for f in M._meta.concrete_fields:
                if f.name in SKIP_FIELDS: continue
                if f.is_relation:
                    v = getattr(o, f.attname)
                    if v is not None and f.related_model in exported and v not in exported[f.related_model]:
                        if not f.null: r = None; break
                        v = None
                    r[f.attname] = v
                else:
                    r[f.name] = _plain(getattr(o, f.name))
            if r is None: continue
            for ff in _file_fields(M):
                p = r.get(ff)
                if p:
                    if not MEDIA_NAME.match(p): r[ff] = ""; continue   # nama aneh = jangan terbitkan
                    try: media[p] = Path(settings.MEDIA_ROOT, p).read_bytes()
                    except OSError: r[ff] = ""
            rows.append(r); ids.add(o.pk)
        exported[M] = ids; data[M._meta.label] = rows
    m2m = {"company.Look.products": [[l.pk, sorted(l.products.filter(pk__in=exported[Product]).values_list("pk", flat=True))] for l in Look.objects.filter(pk__in=exported[Look])]}
    apps_status = [{"ref": a.ref, "stage": a.stage, "anonymized": bool(a.anonymized_at),
                    "events": [{"id": e.pk, "kind": e.kind, "from": e.from_stage, "to": e.to_stage, "msg": e.public_message, "at": e.created_at.isoformat()}
                               for e in a.events.all() if e.public_message or e.kind in ("stage", "anonymized")]}
                   for a in Application.objects.filter(anonymized_at__isnull=True).exclude(stage=Stage.UNVERIFIED).prefetch_related("events")]
    content = json.dumps({"content": data, "m2m": m2m, "applications": apps_status}, ensure_ascii=False, sort_keys=True).encode()
    files = {"content.json": content, **{f"media/{k}": v for k, v in media.items()}}
    manifest = json.dumps({"serial": timezone.now().strftime("%Y%m%d%H%M%S%f"), "files": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}, sort_keys=True).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("MANIFEST.json", manifest); z.writestr("SIGNATURE", _sign(manifest))
        for k, v in files.items(): z.writestr(k, v)
    return buf.getvalue(), {"rows": {k: len(v) for k, v in data.items()}, "media": len(media), "applications": len(apps_status)}


# ------------------------------------------------------------------ TURUN: sisi publik
def _read_verified(raw):
    if len(raw) > MAX_BUNDLE_MB * 1024 * 1024: raise SyncError("Bundle terlalu besar.")
    try: z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile: raise SyncError("Bundle rusak.")
    names = set(z.namelist())
    if not {"MANIFEST.json", "SIGNATURE", "content.json"} <= names: raise SyncError("Bundle tidak lengkap.")
    manifest = z.read("MANIFEST.json")
    if not hmac.compare_digest(_sign(manifest), z.read("SIGNATURE").decode().strip()): raise SyncError("Tanda tangan bundle SALAH: ditolak.")
    mf = json.loads(manifest)
    if set(mf["files"]) != names - {"MANIFEST.json", "SIGNATURE"}: raise SyncError("Isi bundle tidak cocok dengan manifest.")
    files = {}
    for n, h in mf["files"].items():
        if n != "content.json" and not (n.startswith("media/") and MEDIA_NAME.match(n[6:])): raise SyncError(f"Nama berkas tidak sah: {n[:60]}")
        b = z.read(n)
        if hashlib.sha256(b).hexdigest() != h: raise SyncError(f"Hash berkas tidak cocok: {n[:60]}")
        files[n] = b
    return mf["serial"], files


def _coerce(M, row):
    out = {}
    for f in M._meta.concrete_fields:
        key = f.attname if f.is_relation else f.name
        if key not in row: continue
        v = row[key]
        if v is not None and isinstance(f, models.DateTimeField): v = parse_datetime(v)
        elif v is not None and isinstance(f, models.DateField): v = parse_date(v)
        out[key] = v
    return out


def apply_bundle(raw, last_serial=""):
    """Terapkan bundle (atomik). Mengembalikan (serial, ringkasan, daftar_ref_terkonfirmasi)."""
    serial, files = _read_verified(raw)
    if last_serial and serial <= last_serial: raise SyncError("Bundle lebih lama/sama dengan yang sudah diterapkan (anti-replay).")
    for n, b in files.items():                                   # periksa SEMUA gambar sebelum menyentuh apa pun
        if n.startswith("media/"):
            try: safe_upload.check_content(n, b, MEDIA_OK, 3 * 1024 * 1024)
            except ValidationError as e: raise SyncError(f"Gambar ditolak ({n[:50]}): {e.messages[0]}")
    payload = json.loads(files["content.json"])
    root = Path(settings.MEDIA_ROOT)
    for n, b in files.items():
        if n.startswith("media/"):
            dest = root / n[6:]; dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".tmp"); tmp.write_bytes(b); tmp.replace(dest)
    n_del = 0
    with transaction.atomic():
        for M in CONTENT:
            rows = payload["content"].get(M._meta.label, [])
            keep = set()
            for r in rows:
                data = _coerce(M, r); pk = data.pop("id", None) if "id" in data else None
                if M is SiteSetting: pk = 1
                obj = M.objects.filter(pk=pk).first() or M(pk=pk)
                for k, v in data.items(): setattr(obj, k, v)
                obj.save()
                keep.add(pk)
            stale = M.objects.exclude(pk__in=keep)
            if M is Job: stale.update(status=JobStatus.DRAFT)            # PROTECT oleh lamaran; cukup disembunyikan
            elif M is not SiteSetting: n_del += stale.delete()[0]
        for lid, pids in payload["m2m"].get("company.Look.products", []):
            l = Look.objects.filter(pk=lid).first()
            if l: l.products.set(pids)
        confirmed = _apply_statuses(payload.get("applications", []))
    return serial, {"deleted": n_del, "applications": len(confirmed)}, confirmed


def _apply_statuses(items):
    """Status dari HRIS (otoritas) + minimisasi data pribadi untuk lamaran yang sudah ditarik. Lamaran dibatalkan pelamar tidak ditimpa."""
    from . import careers
    done = []
    for it in items:
        a = Application.objects.filter(ref=it["ref"]).first()
        if not a: continue
        done.append(a.ref)
        if a.anonymized_at: continue
        if it["anonymized"]: careers.anonymize(a, reason="dihapus di HRIS"); continue
        if a.stage != Stage.WITHDRAWN and a.stage != it["stage"] and it["stage"] in Stage.values and it["stage"] != Stage.UNVERIFIED:
            a.stage = it["stage"]; a.save(update_fields=["stage", "updated_at"])
        have = set(a.events.filter(kind="hris").values_list("note", flat=True))
        for e in it["events"]:
            tag = f"hris#{e['id']}"
            if tag in have or e["kind"] != "stage": continue
            ev = ApplicationEvent.objects.create(application=a, kind="hris", from_stage=e["from"], to_stage=e["to"], public_message=e["msg"][:500], note=tag)
            ApplicationEvent.objects.filter(pk=ev.pk).update(created_at=parse_datetime(e["at"]))
        minimize(a)
    return done


def minimize(a):
    """Setelah HRIS menarik: buang yang tidak lagi perlu di internet (CV, telepon, tgl lahir, surat, jawaban). Tetap: nama, email (kirim ulang tautan), token, status."""
    if a.events.filter(kind="pulled").exists(): return False
    safe_upload.delete_private(a.cv_path)
    Application.objects.filter(pk=a.pk).update(phone="", birth_date="", cover_letter="", answers="", cv_path="", cv_name="", cv_sha256="", cv_size=0, city="", institution="",
                                               last_position="", expected_salary=None)
    ApplicationEvent.objects.create(application=a, kind="pulled", note="data pribadi diminimalkan setelah ditarik HRIS")
    return True


# ------------------------------------------------------------------ NAIK: publik -> HRIS
APP_FIELDS = ["ref", "lang", "stage", "full_name", "city", "education", "institution", "experience_years", "last_position", "expected_salary", "scan_status",
              "consent_version", "talent_pool", "idem_key", "ip_hash", "email_hash", "token_hash", "retain_until", "email_hash"]
APP_SECRET = ["email", "phone", "birth_date", "cover_letter", "answers", "token_enc"]


def export_applications(since=None):
    """Dijalankan di host PUBLIK (lewat SSH oleh HRIS). Satu JSON per baris. Hanya yang sudah verifikasi email (kecuali bila verifikasi dimatikan)."""
    qs = Application.objects.exclude(stage=Stage.UNVERIFIED).select_related("job").prefetch_related("events").order_by("updated_at")
    if since: qs = qs.filter(updated_at__gt=since)
    for a in qs:
        d = {"job_id": a.job_id, "updated_at": a.updated_at.isoformat(), "anonymized": bool(a.anonymized_at), "created_at": a.created_at.isoformat(),
             "verified_at": a.verified_at.isoformat() if a.verified_at else None, "consent_at": a.consent_at.isoformat()}
        for f in set(APP_FIELDS): d[f] = _plain(getattr(a, f))
        pending = not a.events.filter(kind="pulled").exists()
        if pending and not a.anonymized_at:
            for f in APP_SECRET: d[f] = getattr(a, f)               # didekripsi ORM; dikirim lewat SSH, dienkripsi ulang dengan kunci HRIS
            if a.cv_path:
                try: d["cv_b64"] = base64.b64encode(safe_upload.read_private(a.cv_path)).decode(); d["cv_name"] = a.cv_name
                except (OSError, Exception): d["cv_b64"] = ""
        d["events"] = [{"id": e.pk, "kind": e.kind, "from": e.from_stage, "to": e.to_stage, "msg": e.public_message, "note": e.note, "at": e.created_at.isoformat()}
                       for e in a.events.all() if e.kind in ("submitted", "verified", "stage", "anonymized")]
        yield d


def import_applications(lines):
    """Dijalankan di HRIS. Idempoten (upsert per `ref`). Mengembalikan (baru, diperbarui, dilewati, waktu_terbaru)."""
    from . import careers
    new = upd = skip = 0; latest = None
    for ln in lines:
        if not ln.strip(): continue
        d = json.loads(ln); latest = max(latest or d["updated_at"], d["updated_at"])
        job = Job.objects.filter(pk=d["job_id"]).first()
        if not job: skip += 1; continue
        a = Application.objects.filter(ref=d["ref"]).first()
        if a is None:
            if d["anonymized"] or "email" not in d: skip += 1; continue
            cv_path = cv_sha = ""; cv_size = 0
            if d.get("cv_b64"):
                blob = base64.b64decode(d["cv_b64"]); ext = d["cv_name"].rsplit(".", 1)[-1].lower()
                try: safe_upload.check_content(d["cv_name"], blob, safe_upload.CV_TYPES, settings.CAREER_MAX_UPLOAD_MB * 1024 * 1024)
                except ValidationError: skip += 1; continue          # HRIS memeriksa ulang: host publik tidak dipercaya penuh
                cv_path, cv_sha = safe_upload.store_private(blob, ext); cv_size = len(blob)
            kw = {f: (parse_date(d[f]) if f == "retain_until" else d[f]) for f in set(APP_FIELDS) if f in d}
            a = Application.objects.create(job=job, cv_path=cv_path, cv_name=d.get("cv_name", ""), cv_size=cv_size, cv_sha256=cv_sha,
                                           consent_at=parse_datetime(d["consent_at"]), verified_at=parse_datetime(d["verified_at"]) if d["verified_at"] else None,
                                           **{f: d[f] for f in APP_SECRET}, **kw)
            Application.objects.filter(pk=a.pk).update(created_at=parse_datetime(d["created_at"]))
            new += 1; careers.notify_hrd(a)
        else:
            upd += 1
            if a.anonymized_at: continue
            if d["anonymized"]: careers.anonymize(a, reason="pelamar menghapus data di situs publik"); continue
            if d["stage"] == Stage.WITHDRAWN and a.stage not in (Stage.HIRED, Stage.WITHDRAWN): a.stage = Stage.WITHDRAWN; a.save(update_fields=["stage", "updated_at"])
            elif a.stage == Stage.UNVERIFIED and d["stage"] != Stage.UNVERIFIED: a.stage = d["stage"]; a.save(update_fields=["stage", "updated_at"])
        have = set(a.events.values_list("note", flat=True))
        for e in d["events"]:
            tag = f"pub#{e['id']}"
            if tag in have: continue
            ev = ApplicationEvent.objects.create(application=a, kind=e["kind"], from_stage=e["from"], to_stage=e["to"], public_message=e["msg"], note=(tag + " " + e["note"]).strip()[:500])
            ApplicationEvent.objects.filter(pk=ev.pk).update(created_at=parse_datetime(e["at"]))
    return new, upd, skip, latest
