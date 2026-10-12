"""Formulir backend (HRD) dan formulir publik (kontak, lamaran)."""
import re
import time
import uuid
from datetime import date
from django import forms
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from . import safe_upload
from .models import (EDUCATION, Article, ContactMessage, Faq, Job, Page, Product, ProductCategory, ProductImage, SiteSetting, StoreLocation)

MAX_QUESTIONS = 8
HP = re.compile(r"^[0-9+()\-\s]{6,25}$")


class ImageMixin:
    """Gambar situs disajikan publik: hanya jpg/png/webp (bukan SVG), isi diperiksa, ≤ 3 MB."""
    image_fields = ()

    def clean(self):
        data = super().clean()
        for f in self.image_fields:
            up = self.files.get(self.add_prefix(f)) if self.files else None
            if up:
                try: safe_upload.validate_image(up)
                except ValidationError as e: self.add_error(f, e)
        return data


class SiteSettingForm(ImageMixin, forms.ModelForm):
    image_fields = ("logo",)
    class Meta:
        model = SiteSetting
        exclude = ("created_at", "updated_at", "updated_by")
        widgets = {k: forms.Textarea(attrs={"rows": 3}) for k in ("hero_text_id", "hero_text_en", "address", "careers_intro_id", "careers_intro_en")}

    def clean_whatsapp(self):
        v = re.sub(r"\D", "", self.cleaned_data.get("whatsapp", ""))
        if v and not 8 <= len(v) <= 16: raise ValidationError("Isi angka saja, mis. 62812xxxxxxx.")
        return v


class PageForm(forms.ModelForm):
    class Meta:
        model = Page
        exclude = ("created_at", "updated_at", "updated_by")
        widgets = {"body_id": forms.Textarea(attrs={"rows": 12}), "body_en": forms.Textarea(attrs={"rows": 12})}


class CategoryForm(forms.ModelForm):
    class Meta: model = ProductCategory; fields = ["name_id", "name_en", "order", "active"]


class ProductForm(ImageMixin, forms.ModelForm):
    image_fields = ("image",)
    class Meta:
        model = Product
        exclude = ("created_at", "updated_at", "updated_by", "slug")
        widgets = {"description_id": forms.Textarea(attrs={"rows": 6}), "description_en": forms.Textarea(attrs={"rows": 6})}

    def clean(self):
        d = super().clean()
        if d.get("show_price") and not d.get("price"): self.add_error("price", "Isi harga atau matikan 'Tampilkan harga'.")
        return d


class ProductImageForm(ImageMixin, forms.ModelForm):
    image_fields = ("image",)
    class Meta: model = ProductImage; fields = ["image", "caption_id", "caption_en", "order"]


class ArticleForm(ImageMixin, forms.ModelForm):
    image_fields = ("cover",)
    published_at = forms.DateTimeField(label="Tanggal terbit", required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
                                       input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"], help_text="Kosong = saat diterbitkan.")
    class Meta:
        model = Article
        exclude = ("created_at", "updated_at", "updated_by", "slug")
        widgets = {"body_id": forms.Textarea(attrs={"rows": 14}), "body_en": forms.Textarea(attrs={"rows": 14})}


class StoreForm(forms.ModelForm):
    class Meta:
        model = StoreLocation; exclude = ("created_at", "updated_at", "updated_by")
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}


class FaqForm(forms.ModelForm):
    class Meta:
        model = Faq; exclude = ("created_at", "updated_at", "updated_by")
        widgets = {"answer_id": forms.Textarea(attrs={"rows": 4}), "answer_en": forms.Textarea(attrs={"rows": 4})}


class JobForm(forms.ModelForm):
    questions_text = forms.CharField(label="Pertanyaan tambahan", required=False, widget=forms.Textarea(attrs={"rows": 4}),
                                     help_text=f"Satu pertanyaan per baris (maks {MAX_QUESTIONS}). Awali dengan * bila wajib dijawab. Jangan menanyakan agama, suku, status pernikahan, atau data yang tidak perlu.")

    class Meta:
        model = Job
        exclude = ("created_at", "updated_at", "updated_by", "slug", "questions")
        widgets = {"description": forms.Textarea(attrs={"rows": 7}), "requirements": forms.Textarea(attrs={"rows": 7}), "benefits": forms.Textarea(attrs={"rows": 4}),
                   "closes_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        if self.instance.pk:
            self.initial["questions_text"] = "\n".join(("*" if q.get("required") else "") + q["label"] for q in (self.instance.questions or []))

    def clean_questions_text(self):
        out = []
        for line in self.cleaned_data.get("questions_text", "").splitlines():
            line = line.strip()
            if not line: continue
            req = line.startswith("*"); label = line.lstrip("*").strip()
            if len(label) < 3 or len(label) > 200: raise ValidationError("Tiap pertanyaan 3–200 huruf.")
            out.append({"label": label, "required": req})
        if len(out) > MAX_QUESTIONS: raise ValidationError(f"Maksimal {MAX_QUESTIONS} pertanyaan.")
        return out

    def clean_openings(self):
        v = self.cleaned_data["openings"]
        if not 1 <= v <= 500: raise ValidationError("Isi 1–500.")
        return v

    def save(self, commit=True):
        self.instance.questions = self.cleaned_data["questions_text"]
        return super().save(commit)


# ------------------------------------------------------------------ Publik
def stamp(): return signing.TimestampSigner(salt="company.form").sign(str(int(time.time())))


def stamp_ok(value, min_seconds=None):
    """Formulir dianggap sah bila cap waktunya benar, tidak kedaluwarsa (2 jam), dan bukan terkirim terlalu cepat (bot)."""
    min_seconds = settings.CAREER_MIN_FILL_SECONDS if min_seconds is None else min_seconds
    try: t = int(signing.TimestampSigner(salt="company.form").unsign(value or "", max_age=7200))
    except signing.BadSignature: return False
    return time.time() - t >= min_seconds


class ContactForm(forms.Form):
    name = forms.CharField(label="Nama", max_length=120)
    email = forms.EmailField(label="Email")
    phone = forms.CharField(label="Telepon / WhatsApp (opsional)", max_length=25, required=False)
    subject = forms.CharField(label="Perihal", max_length=160, required=False)
    message = forms.CharField(label="Pesan", max_length=3000, widget=forms.Textarea(attrs={"rows": 6}), min_length=10)
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))   # honeypot: manusia tidak melihatnya
    ts = forms.CharField(widget=forms.HiddenInput, required=False)

    def clean_phone(self):
        v = self.cleaned_data.get("phone", "").strip()
        if v and not HP.match(v): raise ValidationError("Nomor telepon tidak valid.")
        return v

    def clean(self):
        d = super().clean()
        if d.get("website"): raise ValidationError("Permintaan ditolak.")
        if not stamp_ok(d.get("ts")): raise ValidationError("Formulir kedaluwarsa atau terkirim terlalu cepat. Muat ulang halaman lalu coba lagi.")
        return d


class ApplyForm(forms.Form):
    full_name = forms.CharField(label="Nama lengkap", max_length=120)
    email = forms.EmailField(label="Email", help_text="Tautan verifikasi dan status lamaran dikirim ke sini.")
    phone = forms.CharField(label="Nomor HP / WhatsApp", max_length=25)
    birth_date = forms.DateField(label="Tanggal lahir", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    city = forms.CharField(label="Kota domisili", max_length=80)
    education = forms.ChoiceField(label="Pendidikan terakhir", choices=[("", "— pilih —")] + EDUCATION)
    institution = forms.CharField(label="Sekolah / kampus", max_length=140, required=False)
    experience_years = forms.IntegerField(label="Pengalaman kerja (tahun)", min_value=0, max_value=60, initial=0)
    last_position = forms.CharField(label="Posisi terakhir (bila ada)", max_length=140, required=False)
    expected_salary = forms.IntegerField(label="Harapan gaji per bulan, Rp (opsional)", min_value=0, max_value=1_000_000_000, required=False)
    cover_letter = forms.CharField(label="Ceritakan singkat mengapa Anda cocok (opsional)", max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 4}))
    cv = forms.FileField(label="CV (PDF, DOC/DOCX, atau foto JPG/PNG)", help_text="Maksimal %d MB.")
    consent = forms.BooleanField(label="Saya setuju data saya diproses untuk keperluan seleksi dan disimpan sesuai kebijakan privasi", error_messages={"required": "Centang persetujuan untuk melanjutkan."})
    talent_pool = forms.BooleanField(label="Boleh simpan data saya lebih lama untuk lowongan lain (opsional)", required=False)
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))
    ts = forms.CharField(widget=forms.HiddenInput, required=False)
    idem = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(self, *a, job=None, **k):
        super().__init__(*a, **k)
        self.job = job
        self.fields["cv"].help_text = self.fields["cv"].help_text % settings.CAREER_MAX_UPLOAD_MB
        self.qfields = []
        for i, q in enumerate((job.questions if job else []) or []):
            name = f"q_{i}"
            self.fields[name] = forms.CharField(label=q["label"], required=bool(q.get("required")), max_length=500, widget=forms.Textarea(attrs={"rows": 2}))
            self.qfields.append((name, q["label"]))
        if not self.is_bound: self.initial["idem"] = uuid.uuid4().hex; self.initial["ts"] = stamp()

    def clean_phone(self):
        v = self.cleaned_data["phone"].strip()
        if not HP.match(v): raise ValidationError("Nomor HP tidak valid.")
        return v

    def clean_birth_date(self):
        d = self.cleaned_data["birth_date"]; age = (date.today() - d).days // 365
        if not 15 <= age <= 70: raise ValidationError("Periksa tanggal lahir Anda.")
        return d

    def clean_idem(self):
        v = self.cleaned_data.get("idem", "")
        if not re.fullmatch(r"[0-9a-f]{32}", v): raise ValidationError("Formulir tidak valid. Muat ulang halaman.")
        return v

    def clean_cv(self):
        up = self.cleaned_data["cv"]
        try: safe_upload.validate_cv(up)
        except ValidationError as e: raise ValidationError(e.messages)
        return up

    def clean(self):
        d = super().clean()
        if d.get("website"): raise ValidationError("Permintaan ditolak.")
        if not stamp_ok(d.get("ts")): raise ValidationError("Formulir kedaluwarsa atau terkirim terlalu cepat. Muat ulang halaman lalu kirim lagi.")
        return d

    def answers(self): return [(label, self.cleaned_data.get(name, "")) for name, label in self.qfields if self.cleaned_data.get(name, "")]


class LookupForm(forms.Form):
    email = forms.EmailField(label="Email yang Anda pakai melamar")
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))


# ------------------------------------------------------------------ Backend lamaran
class MoveForm(forms.Form):
    to = forms.ChoiceField(label="Pindahkan ke")
    public_message = forms.CharField(label="Pesan untuk pelamar (opsional)", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                     help_text="Dikirim lewat email dan tampil di halaman status pelamar. Jangan menulis data sensitif.")
    note = forms.CharField(label="Catatan internal (opsional)", max_length=500, required=False)

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        from .models import Stage
        self.fields["to"].choices = [(s.value, s.label) for s in Stage if s.value not in ("unverified", "withdrawn")]
