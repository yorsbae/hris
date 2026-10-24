"""Logika lamaran: membuat, verifikasi, pindah tahap, anonimkan, ekspor, email. Satu pintu agar jalur publik, backend HRD, dan perintah terjadwal konsisten."""
import json
import secrets
from datetime import timedelta
from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone
from apps.core.models import Notification, Role, User
from . import safe_upload
from .models import (ACTIVE_STAGES, FINAL_STAGES, STAGE_PUBLIC, Application, ApplicationEvent, Stage, default_retain_until, hash_email,
                     hash_ip, hash_token, new_ref)

T = {  # teks email pelamar (id, en)
    "verify_subject": ("Verifikasi lamaran Anda — {job}", "Verify your application — {job}"),
    "verify_body": ("Halo {name},\n\nTerima kasih sudah melamar sebagai {job} di {brand}.\nNomor lamaran Anda: {ref}\n\nBuka tautan ini untuk memverifikasi email dan memantau status lamaran:\n{url}\n\nTautan ini pribadi; jangan dibagikan.\nBila Anda tidak merasa melamar, abaikan email ini.\n",
                    "Hello {name},\n\nThank you for applying as {job} at {brand}.\nYour application number: {ref}\n\nOpen this link to verify your email and follow your application status:\n{url}\n\nThis link is private; please do not share it.\nIf you did not apply, ignore this email.\n"),
    "again_subject": ("Tautan status lamaran Anda", "Your application status link"),
    "again_body": ("Halo,\n\nBerikut tautan untuk memantau lamaran Anda ({ref}, {job}):\n{url}\n\nTautan ini pribadi; jangan dibagikan.\n",
                   "Hello,\n\nHere is the link to follow your application ({ref}, {job}):\n{url}\n\nThis link is private; please do not share it.\n"),
    "stage_subject": ("Pembaruan lamaran {ref}", "Update on your application {ref}"),
    "stage_body": ("Halo {name},\n\nStatus lamaran Anda ({job}) sekarang: {stage}.\n{message}\n\nLihat rinciannya: {url}\n",
                   "Hello {name},\n\nThe status of your application ({job}) is now: {stage}.\n{message}\n\nSee details: {url}\n"),
}
STAGE_EN = {"unverified": "Waiting for email verification", "new": "Received", "screening": "Application review", "test": "Assessment", "interview": "Interview",
            "offer": "Offer", "hired": "Hired", "rejected": "Not proceeding", "withdrawn": "Withdrawn"}


def _t(key, lang): return T[key][1 if lang == "en" else 0]


def base_url():
    return (settings.PUBLIC_BASE_URL or "").rstrip("/")


def status_url(app, raw_token, lang=None):
    pre = "/en" if (lang or app.lang) == "en" else ""
    return f"{base_url()}{pre}/karir/status/{raw_token}/"


def _mail(app, subject, body):
    """Gagal kirim TIDAK boleh menghilangkan lamaran: dicatat sebagai jejak, HRD dapat mengirim ulang."""
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [app.email], fail_silently=False)
        return True
    except Exception as e:  # noqa: BLE001 — SMTP bisa gagal dengan banyak cara
        ApplicationEvent.objects.create(application=app, kind="mail_failed", note=type(e).__name__)
        return False


def brand():
    from .models import SiteSetting
    return SiteSetting.get().brand_name


def event(app, kind, by=None, **kw): return ApplicationEvent.objects.create(application=app, kind=kind, by=by, **kw)


def create_application(job, data, cv_upload, ip, lang="id"):
    """Buat lamaran (idempoten via data['idem']). Mengembalikan (application|None, dibuat_baru). Duplikat (email+lowongan sama) TIDAK membuat baris baru:
    pelamar menerima email tautan status lagi; hasil ke layar sama persis (tidak membocorkan apakah email itu pernah melamar)."""
    from .models import Application as A
    idem = data["idem"]
    existing = A.objects.filter(idem_key=idem).first()
    if existing: return existing, False
    eh = hash_email(data["email"])
    dup = A.objects.filter(job=job, email_hash=eh, anonymized_at__isnull=True).exclude(stage__in=(Stage.WITHDRAWN, Stage.REJECTED)).first()
    if dup:
        _mail(dup, _t("again_subject", lang), _t("again_body", lang).format(ref=dup.ref, job=job.title, url=status_url(dup, dup.token_enc, lang)))
        return dup, False
    blob, ext = safe_upload.validate_cv(cv_upload)
    status = safe_upload.scan(blob)
    path, sha = safe_upload.store_private(blob, ext)
    raw = secrets.token_urlsafe(24)
    now = timezone.now()
    answers = json.dumps([{"q": q, "a": a} for q, a in data.get("answers", [])], ensure_ascii=False) if data.get("answers") else ""
    verify = settings.CAREER_REQUIRE_EMAIL_VERIFY
    for _ in range(5):
        try:
            with transaction.atomic():
                app = Application.objects.create(
                    ref=new_ref(), lang=lang, job=job, stage=Stage.UNVERIFIED if verify else Stage.NEW, full_name=data["full_name"], email=data["email"], email_hash=eh,
                    phone=data.get("phone", ""), birth_date=data["birth_date"].isoformat() if data.get("birth_date") else "", city=data.get("city", ""),
                    education=data.get("education", ""), institution=data.get("institution", ""), experience_years=data.get("experience_years") or 0,
                    last_position=data.get("last_position", ""), expected_salary=data.get("expected_salary"), cover_letter=data.get("cover_letter", ""),
                    answers=answers, cv_path=path, cv_name=f"cv.{ext}", cv_size=len(blob), cv_sha256=sha, scan_status=status, consent_at=now,
                    talent_pool=bool(data.get("talent_pool")), token_hash=hash_token(raw), token_enc=raw, idem_key=idem, ip_hash=hash_ip(ip),
                    verified_at=None if verify else now, retain_until=default_retain_until(bool(data.get("talent_pool"))))
                event(app, "submitted", to_stage=app.stage)
            break
        except IntegrityError:
            if Application.objects.filter(idem_key=idem).exists(): safe_upload.delete_private(path); return Application.objects.get(idem_key=idem), False
            continue   # tabrakan ref acak: ulangi
    else:
        safe_upload.delete_private(path); raise RuntimeError("Tidak dapat membuat nomor lamaran")
    _mail(app, _t("verify_subject", lang).format(job=job.title), _t("verify_body", lang).format(name=app.full_name, job=job.title, brand=brand(), ref=app.ref, url=status_url(app, raw, lang)))
    if not verify: notify_hrd(app)
    return app, True


def notify_hrd(app):
    users = User.objects.filter(is_active=True, role=Role.SUPERADMIN)
    Notification.objects.bulk_create([Notification(user=u, kind="info", title=f"Lamaran baru: {app.job.title} ({app.ref})"[:200], link=f"/recruitment/applicants/{app.pk}/") for u in users])


def find_by_token(raw):
    if not raw or len(raw) > 100: return None
    return Application.objects.select_related("job").filter(token_hash=hash_token(raw), anonymized_at__isnull=True).first()


def verify(app):
    """Membuka tautan = email terbukti milik pelamar. Idempoten."""
    if app.stage != Stage.UNVERIFIED: return False
    app.stage = Stage.NEW; app.verified_at = timezone.now(); app.save(update_fields=["stage", "verified_at", "updated_at"])
    event(app, "verified", from_stage=Stage.UNVERIFIED, to_stage=Stage.NEW)
    notify_hrd(app)
    return True


def resend_link(email, lang="id"):
    """Pelamar lupa tautan: kirim tautan baru untuk SEMUA lamaran aktif email itu. Tidak mengembalikan apa pun (agar tidak membocorkan keberadaan)."""
    eh = hash_email(email)
    for app in Application.objects.select_related("job").filter(email_hash=eh, anonymized_at__isnull=True).exclude(stage=Stage.WITHDRAWN)[:5]:
        _mail(app, _t("again_subject", lang), _t("again_body", lang).format(ref=app.ref, job=app.job.title, url=status_url(app, app.token_enc, lang)))


def move(app, to, by, public_message="", note="", notify=True):
    """Pindah tahap oleh HRD. Tahap awal 'unverified' hanya lewat verifikasi pelamar. Mengembalikan True bila berubah."""
    if to not in Stage.values or to in (Stage.UNVERIFIED, Stage.WITHDRAWN): raise ValueError("Tahap tidak valid")
    if app.anonymized_at or app.stage == Stage.UNVERIFIED or app.stage == Stage.WITHDRAWN or app.stage == to: return False
    old = app.stage
    app.stage = to; app.save(update_fields=["stage", "updated_at"])
    event(app, "stage", by=by, from_stage=old, to_stage=to, public_message=public_message[:500], note=note[:500])
    if notify and app.email:
        label = STAGE_EN[to] if app.lang == "en" else Stage(to).label
        msg = public_message or (STAGE_PUBLIC[Stage(to)] if app.lang != "en" else "")
        _mail(app, _t("stage_subject", app.lang).format(ref=app.ref), _t("stage_body", app.lang).format(name=app.full_name, job=app.job.title, stage=label, message=msg, url=status_url(app, app.token_enc)))
    return True


def withdraw(app):
    if app.stage in (Stage.HIRED, Stage.WITHDRAWN) or app.anonymized_at: return False
    old = app.stage; app.stage = Stage.WITHDRAWN; app.save(update_fields=["stage", "updated_at"])
    event(app, "stage", from_stage=old, to_stage=Stage.WITHDRAWN, note="oleh pelamar")
    return True


def anonymize(app, by=None, reason=""):
    """Hapus data pribadi + berkas CV; baris dan jejak tahap tetap (statistik). Tidak bisa dibatalkan."""
    if app.anonymized_at: return False
    safe_upload.delete_private(app.cv_path)
    app.full_name = "(dianonimkan)"; app.email = ""; app.phone = ""; app.birth_date = ""; app.city = ""; app.institution = ""; app.last_position = ""
    app.cover_letter = ""; app.answers = ""; app.cv_path = ""; app.cv_name = ""; app.cv_sha256 = ""; app.expected_salary = None; app.internal_note = ""
    app.token_hash = hash_token(secrets.token_urlsafe(24)); app.token_enc = ""; app.ip_hash = ""; app.talent_pool = False
    if app.stage not in FINAL_STAGES: app.stage = Stage.WITHDRAWN
    app.anonymized_at = timezone.now()
    app.save()
    event(app, "anonymized", by=by, note=reason[:200])
    return True


def export_data(app):
    """Salinan data milik pelamar (hak akses data, UU PDP)."""
    return {"ref": app.ref, "job": app.job.title, "stage": app.get_stage_display(), "submitted_at": app.created_at.isoformat(), "name": app.full_name, "email": app.email,
            "phone": app.phone, "birth_date": app.birth_date, "city": app.city, "education": app.get_education_display(), "institution": app.institution,
            "experience_years": app.experience_years, "last_position": app.last_position, "expected_salary": app.expected_salary, "cover_letter": app.cover_letter,
            "answers": app.answers_list(), "cv_file": app.cv_name, "consent_at": app.consent_at.isoformat(), "talent_pool": app.talent_pool, "retain_until": app.retain_until.isoformat()}


def purge_expired(today=None):
    """Perintah harian: anonimkan lamaran lewat masa simpan (kecuali yang diterima bekerja), hapus yang tak pernah diverifikasi. Mengembalikan (anonim, dihapus_tanpa_verifikasi)."""
    today = today or timezone.localdate()
    n = 0
    for app in Application.objects.filter(anonymized_at__isnull=True, retain_until__lt=today).exclude(stage=Stage.HIRED):
        if anonymize(app, reason="masa simpan berakhir"): n += 1
    cutoff = timezone.now() - timedelta(days=settings.CAREER_UNVERIFIED_DAYS)
    m = 0
    for app in Application.objects.filter(stage=Stage.UNVERIFIED, created_at__lt=cutoff, anonymized_at__isnull=True):
        if anonymize(app, reason="tidak diverifikasi"): m += 1
    return n, m
