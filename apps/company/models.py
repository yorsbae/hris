"""Company Profile (situs publik) + Lowongan & Lamaran (putaran 48, Tahap 11).
Isi situs = DATA yang dikelola HRD/Superadmin (bukan kode). Dua bahasa: kolom `*_id` (Indonesia, wajib) dan `*_en` (Inggris, opsional → jatuh ke Indonesia).
Konten teks BUKAN HTML: dirender oleh filter `rich` (escape + paragraf/daftar/judul sederhana), sehingga isi dari editor maupun pelamar tidak pernah menjadi HTML mentah.
Data pribadi pelamar/pengirim pesan (email, telepon, tgl lahir, surat lamaran, jawaban) terenkripsi kolom; berkas CV terenkripsi di penyimpanan privat."""
import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import timedelta
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from apps.core.crypto import EncryptedTextField

USER = settings.AUTH_USER_MODEL


def _name(kind, filename):
    """Nama berkas acak (tebakan nama/ekstensi asli tidak berguna); ekstensi sudah divalidasi form."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"company/{kind}/{uuid.uuid4().hex}.{ext}"


def upload_brand(instance, filename): return _name("brand", filename)
def upload_product(instance, filename): return _name("product", filename)
def upload_article(instance, filename): return _name("article", filename)
def upload_look(instance, filename): return _name("look", filename)
def upload_motif(instance, filename): return _name("motif", filename)


def unique_slug(model, text, instance=None, field="slug", fallback="item"):
    base = slugify(text)[:60] or fallback
    slug, n = base, 2
    qs = model.objects.all()
    if instance is not None and instance.pk: qs = qs.exclude(pk=instance.pk)
    while qs.filter(**{field: slug}).exists():
        slug = f"{base}-{n}"; n += 1
    return slug


class Status(models.TextChoices):
    DRAFT = "draft", "Draf"
    PUBLISHED = "published", "Terbit"


class Stamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta: abstract = True


# ------------------------------------------------------------------ Pengaturan situs (satu baris)
class SiteSetting(Stamped):
    """Identitas & kontak situs. Satu baris (pk=1); lewat `SiteSetting.get()`."""
    brand_name = models.CharField("Nama merek / perusahaan", max_length=120)
    tagline_id = models.CharField("Slogan (ID)", max_length=160, blank=True)
    tagline_en = models.CharField("Slogan (EN)", max_length=160, blank=True)
    hero_title_id = models.CharField("Judul beranda (ID)", max_length=160, blank=True)
    hero_title_en = models.CharField("Judul beranda (EN)", max_length=160, blank=True)
    hero_text_id = models.TextField("Teks beranda (ID)", blank=True)
    hero_text_en = models.TextField("Teks beranda (EN)", blank=True)
    logo = models.FileField("Logo", upload_to=upload_brand, blank=True)
    address = models.TextField("Alamat", blank=True)
    phone = models.CharField("Telepon", max_length=40, blank=True)
    whatsapp = models.CharField("WhatsApp (angka saja, mis. 62812…)", max_length=20, blank=True)
    email = models.EmailField("Email", blank=True)
    hours_id = models.CharField("Jam layanan (ID)", max_length=160, blank=True)
    hours_en = models.CharField("Jam layanan (EN)", max_length=160, blank=True)
    maps_url = models.URLField("Tautan peta", blank=True)
    instagram = models.URLField("Instagram", blank=True)
    facebook = models.URLField("Facebook", blank=True)
    tiktok = models.URLField("TikTok", blank=True)
    marketplace_url = models.URLField("Toko online / marketplace", blank=True)
    banner_active = models.BooleanField("Tampilkan pita pengumuman di atas situs", default=False)
    banner_id = models.CharField("Teks pita (ID)", max_length=240, blank=True)
    banner_en = models.CharField("Teks pita (EN)", max_length=240, blank=True)
    careers_intro_id = models.TextField("Pengantar halaman karir (ID)", blank=True)
    careers_intro_en = models.TextField("Pengantar halaman karir (EN)", blank=True)
    meta_description_id = models.CharField("Deskripsi untuk mesin pencari (ID)", max_length=300, blank=True)
    meta_description_en = models.CharField("Deskripsi untuk mesin pencari (EN)", max_length=300, blank=True)
    footer_note_id = models.CharField("Catatan kaki (ID)", max_length=240, blank=True)
    footer_note_en = models.CharField("Catatan kaki (EN)", max_length=240, blank=True)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"brand_name": settings.COMPANY_NAME})
        return obj

    def save(self, *a, **k):
        self.pk = 1
        super().save(*a, **k)

    def delete(self, *a, **k): raise PermissionError("Pengaturan situs tidak boleh dihapus")
    def __str__(self): return self.brand_name


# ------------------------------------------------------------------ Halaman (Tentang Kami, dst.)
class Page(Stamped):
    slug = models.SlugField("Alamat halaman", max_length=60, unique=True)
    title_id = models.CharField("Judul (ID)", max_length=160)
    title_en = models.CharField("Judul (EN)", max_length=160, blank=True)
    subtitle_id = models.CharField("Subjudul (ID)", max_length=240, blank=True)
    subtitle_en = models.CharField("Subjudul (EN)", max_length=240, blank=True)
    body_id = models.TextField("Isi (ID)", blank=True)
    body_en = models.TextField("Isi (EN)", blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    show_in_menu = models.BooleanField("Tampil di menu atas", default=False)
    order = models.PositiveIntegerField("Urutan", default=100)

    class Meta: ordering = ["order", "title_id"]
    def __str__(self): return self.title_id


# ------------------------------------------------------------------ Produk
class ProductCategory(Stamped):
    """Dua tingkat: KELOMPOK (parent kosong: Bawahan / Atasan / Aksesoris / …) dan SUBKATEGORI di bawahnya (Sarung, Celana Sarung, Baju Koko, Peci, …).
    Produk boleh ditaruh di subkategori atau langsung di kelompok; menyaring kelompok = semua subkategorinya."""
    slug = models.SlugField(max_length=60, unique=True, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children", verbose_name="Kelompok induk",
                               help_text="Kosong = ini sebuah kelompok (mis. Bawahan). Isi = subkategori di dalam kelompok itu.")
    name_id = models.CharField("Nama (ID)", max_length=100)
    name_en = models.CharField("Nama (EN)", max_length=100, blank=True)
    hint_id = models.CharField("Contoh isi (ID)", max_length=160, blank=True, help_text="Satu kalimat di bawah judul kelompok, mis. “Sarung, celana sarung, dan bawahan lainnya.”")
    hint_en = models.CharField("Contoh isi (EN)", max_length=160, blank=True)
    order = models.PositiveIntegerField("Urutan", default=100)
    active = models.BooleanField("Aktif", default=True)

    class Meta: ordering = ["order", "name_id"]; verbose_name_plural = "product categories"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.parent_id:
            if self.pk and self.parent_id == self.pk: raise ValidationError({"parent": "Kategori tidak bisa menjadi induk dirinya sendiri."})
            if self.parent.parent_id: raise ValidationError({"parent": "Hanya dua tingkat: pilih sebuah kelompok (bukan subkategori) sebagai induk."})
            if self.pk and self.children.exists(): raise ValidationError({"parent": "Kategori ini punya subkategori; pindahkan dulu subkategorinya."})

    def save(self, *a, **k):
        if not self.slug: self.slug = unique_slug(ProductCategory, self.name_id, self, fallback="kategori")
        super().save(*a, **k)

    @property
    def group(self): return self.parent if self.parent_id else self

    def tree_slugs(self):
        """Slug kategori ini + semua subkategorinya (untuk menyaring produk per kelompok)."""
        return [self.slug] + list(self.children.values_list("slug", flat=True))

    def __str__(self): return f"{self.parent.name_id} › {self.name_id}" if self.parent_id else self.name_id


class Motif(Stamped):
    """SERI / MOTIF sarung (Grandmaster, Primer, Viscose, Jacquard, …): tingkat di antara subkategori (mis. Sarung Tenun) dan produk.
    Jalur situs: Produk → Sarung Tenun → motif Grandmaster → produk Grandmaster SGE → pilihan warna. Menu Produk menampilkan daftar motif di kiri dan produk motif yang disorot di kanan."""
    slug = models.SlugField(max_length=60, unique=True, blank=True)
    category = models.ForeignKey(ProductCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name="motifs", verbose_name="Subkategori",
                                 help_text="Mis. Sarung Tenun. Dipakai untuk rute “Produk › Sarung Tenun › Motif”.")
    name_id = models.CharField("Nama motif (ID)", max_length=100)
    name_en = models.CharField("Nama motif (EN)", max_length=100, blank=True)
    hint_id = models.CharField("Keterangan singkat (ID)", max_length=200, blank=True)
    hint_en = models.CharField("Keterangan singkat (EN)", max_length=200, blank=True)
    cover = models.FileField("Foto sampul motif", upload_to=upload_motif, blank=True)
    order = models.PositiveIntegerField("Urutan", default=100)
    active = models.BooleanField("Tampil di menu & situs", default=True)

    class Meta: ordering = ["order", "name_id"]; verbose_name_plural = "motifs"

    def save(self, *a, **k):
        if not self.slug: self.slug = unique_slug(Motif, self.name_id, self, fallback="motif")
        super().save(*a, **k)

    def __str__(self): return self.name_id


class Product(Stamped):
    category = models.ForeignKey(ProductCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    series = models.ForeignKey(Motif, null=True, blank=True, on_delete=models.SET_NULL, related_name="products", verbose_name="Motif / seri (menu Produk)",
                               help_text="Mis. Grandmaster. Produk tampil di bawah motif ini pada menu Produk dan halaman motif.")
    slug = models.SlugField(max_length=80, unique=True, blank=True)
    name = models.CharField("Nama produk", max_length=140)
    summary_id = models.CharField("Ringkasan (ID)", max_length=240, blank=True)
    summary_en = models.CharField("Ringkasan (EN)", max_length=240, blank=True)
    description_id = models.TextField("Deskripsi (ID)", blank=True)
    description_en = models.TextField("Deskripsi (EN)", blank=True)
    material = models.CharField("Bahan", max_length=120, blank=True)
    size = models.CharField("Ukuran", max_length=80, blank=True)
    motif = models.CharField("Motif / seri", max_length=120, blank=True)
    colors = models.CharField("Warna", max_length=160, blank=True)
    price = models.DecimalField("Harga (Rp)", max_digits=12, decimal_places=0, null=True, blank=True)
    show_price = models.BooleanField("Tampilkan harga di situs", default=False)
    image = models.FileField("Foto utama", upload_to=upload_product, blank=True)
    model_image = models.FileField("Foto model (memakai produk ini)", upload_to=upload_product, blank=True, help_text="Foto orang yang memakai produk; tampil berdampingan dengan detail produk.")
    model_caption_id = models.CharField("Keterangan foto model (ID)", max_length=200, blank=True, help_text="Mis. “Dipadukan dengan baju koko putih dan peci hitam.”")
    model_caption_en = models.CharField("Keterangan foto model (EN)", max_length=200, blank=True)
    featured = models.BooleanField("Unggulan (tampil di beranda)", default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    order = models.PositiveIntegerField("Urutan", default=100)

    class Meta: ordering = ["order", "name"]; indexes = [models.Index(fields=["status", "order"])]
    def save(self, *a, **k):
        if not self.slug: self.slug = unique_slug(Product, self.name, self, fallback="produk")
        super().save(*a, **k)
    def __str__(self): return self.name


HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ProductColor(models.Model):
    """Pilihan warna satu produk. Di halaman produk tiap warna = satu tombol bulat; diklik → foto berganti ke foto warna itu
    (tanpa foto → ilustrasi motif berwarna sesuai kode warna). Berfungsi tanpa JavaScript."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="color_options")
    name_id = models.CharField("Nama warna (ID)", max_length=60)
    name_en = models.CharField("Nama warna (EN)", max_length=60, blank=True)
    hex = models.CharField("Kode warna", max_length=7, default="#8b1e2d", help_text="Format #RRGGBB, mis. #8b1e2d. Dipakai untuk bulatan warna.")
    image = models.FileField("Foto warna ini", upload_to=upload_product, blank=True, help_text="Foto produk dalam warna ini (JPG/PNG/WebP ≤ 3 MB). Kosong = ilustrasi otomatis.")
    order = models.PositiveIntegerField("Urutan", default=100)

    class Meta: ordering = ["order", "id"]

    def clean(self):
        from django.core.exceptions import ValidationError
        if not HEX_RE.match(self.hex or ""): raise ValidationError({"hex": "Kode warna harus berbentuk #RRGGBB, mis. #8b1e2d."})

    # sifat tiruan agar ilustrasi motif (tag `motif`) menghasilkan gambar khas warna ini
    @property
    def name(self): return f"{self.product.name} {self.name_id}"
    @property
    def motif(self): return self.product.motif
    @property
    def colors(self): return self.name_id
    @property
    def category(self): return self.product.category

    def __str__(self): return f"{self.product.name} — {self.name_id}"


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.FileField(upload_to=upload_product)
    caption_id = models.CharField("Keterangan (ID)", max_length=160, blank=True)
    caption_en = models.CharField("Keterangan (EN)", max_length=160, blank=True)
    order = models.PositiveIntegerField(default=100)

    class Meta: ordering = ["order", "id"]


# ------------------------------------------------------------------ Lookbook: foto model memakai satu setelan + daftar produk yang dipakai
class Look(Stamped):
    """Satu “setelan” (mis. sarung + baju koko + peci) difoto pada model; di sebelahnya daftar produk yang dipakai (gaya halaman “Shop your style”)."""
    slug = models.SlugField(max_length=80, unique=True, blank=True)
    title_id = models.CharField("Judul (ID)", max_length=140)
    title_en = models.CharField("Judul (EN)", max_length=140, blank=True)
    story_id = models.TextField("Cerita singkat (ID)", blank=True)
    story_en = models.TextField("Cerita singkat (EN)", blank=True)
    occasion_id = models.CharField("Untuk acara (ID)", max_length=80, blank=True, help_text="Mis. Salat Jumat, Lebaran, Pernikahan")
    occasion_en = models.CharField("Untuk acara (EN)", max_length=80, blank=True)
    photo = models.FileField("Foto model", upload_to=upload_look, blank=True)
    products = models.ManyToManyField(Product, blank=True, related_name="looks", verbose_name="Produk yang dipakai")
    featured = models.BooleanField("Tampil di beranda", default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    order = models.PositiveIntegerField("Urutan", default=100)

    class Meta: ordering = ["order", "-id"]; indexes = [models.Index(fields=["status", "order"])]
    def save(self, *a, **k):
        if not self.slug: self.slug = unique_slug(Look, self.title_id, self, fallback="lookbook")
        super().save(*a, **k)
    def __str__(self): return self.title_id


# ------------------------------------------------------------------ Artikel (+ arsip per bulan)
class Article(Stamped):
    slug = models.SlugField(max_length=90, unique=True, blank=True)
    title_id = models.CharField("Judul (ID)", max_length=200)
    title_en = models.CharField("Judul (EN)", max_length=200, blank=True)
    excerpt_id = models.CharField("Ringkasan (ID)", max_length=300, blank=True)
    excerpt_en = models.CharField("Ringkasan (EN)", max_length=300, blank=True)
    body_id = models.TextField("Isi (ID)", blank=True)
    body_en = models.TextField("Isi (EN)", blank=True)
    cover = models.FileField("Gambar sampul", upload_to=upload_article, blank=True)
    author = models.CharField("Penulis", max_length=100, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    published_at = models.DateTimeField("Tanggal terbit", null=True, blank=True)

    class Meta: ordering = ["-published_at", "-id"]; indexes = [models.Index(fields=["status", "-published_at"])]
    def save(self, *a, **k):
        if not self.slug: self.slug = unique_slug(Article, self.title_id, self, fallback="artikel")
        if self.status == Status.PUBLISHED and not self.published_at: self.published_at = timezone.now()
        super().save(*a, **k)
    def __str__(self): return self.title_id


# ------------------------------------------------------------------ Lokasi toko, FAQ
class StoreLocation(Stamped):
    name = models.CharField("Nama toko", max_length=120)
    city = models.CharField("Kota", max_length=80)
    address = models.TextField("Alamat")
    phone = models.CharField("Telepon", max_length=40, blank=True)
    hours_id = models.CharField("Jam buka (ID)", max_length=160, blank=True)
    hours_en = models.CharField("Jam buka (EN)", max_length=160, blank=True)
    maps_url = models.URLField("Tautan peta", blank=True)
    order = models.PositiveIntegerField("Urutan", default=100)
    active = models.BooleanField("Tampil di situs", default=True)

    class Meta: ordering = ["order", "city", "name"]
    def __str__(self): return f"{self.name} — {self.city}"


class Faq(Stamped):
    question_id = models.CharField("Pertanyaan (ID)", max_length=240)
    question_en = models.CharField("Pertanyaan (EN)", max_length=240, blank=True)
    answer_id = models.TextField("Jawaban (ID)")
    answer_en = models.TextField("Jawaban (EN)", blank=True)
    order = models.PositiveIntegerField("Urutan", default=100)
    active = models.BooleanField("Tampil di situs", default=True)

    class Meta: ordering = ["order", "id"]
    def __str__(self): return self.question_id


# ------------------------------------------------------------------ Pesan dari formulir Kontak
class MessageStatus(models.TextChoices):
    NEW = "new", "Baru"
    READ = "read", "Dibaca"
    DONE = "done", "Selesai"
    SPAM = "spam", "Spam"


class ContactMessage(models.Model):
    name = models.CharField(max_length=120)
    email = EncryptedTextField()
    phone = EncryptedTextField(blank=True)
    subject = models.CharField(max_length=160, blank=True)
    message = EncryptedTextField()
    lang = models.CharField(max_length=2, default="id")
    ip_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=6, choices=MessageStatus.choices, default=MessageStatus.NEW)
    note = models.CharField("Catatan internal", max_length=300, blank=True)
    handled_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    handled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta: ordering = ["-created_at"]; indexes = [models.Index(fields=["status", "-created_at"])]


# ------------------------------------------------------------------ Lowongan
class JobStatus(models.TextChoices):
    DRAFT = "draft", "Draf"
    OPEN = "open", "Dibuka"
    CLOSED = "closed", "Ditutup"


class EmploymentType(models.TextChoices):
    PERMANENT = "tetap", "Karyawan tetap"
    CONTRACT = "kontrak", "Kontrak"
    INTERN = "magang", "Magang"
    DAILY = "harian", "Harian"


class Job(Stamped):
    slug = models.SlugField(max_length=90, unique=True, blank=True)
    title = models.CharField("Posisi", max_length=140)
    department_name = models.CharField("Bagian / departemen", max_length=100, blank=True)
    department = models.ForeignKey("hr.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                   verbose_name="Departemen di HRIS (untuk karyawan baru)")
    location = models.CharField("Lokasi kerja", max_length=120, blank=True)
    employment_type = models.CharField("Jenis", max_length=10, choices=EmploymentType.choices, default=EmploymentType.CONTRACT)
    openings = models.PositiveIntegerField("Jumlah dibutuhkan", default=1)
    summary = models.CharField("Ringkasan (1 kalimat)", max_length=240, blank=True)
    description = models.TextField("Tugas & tanggung jawab", blank=True)
    requirements = models.TextField("Persyaratan", blank=True)
    benefits = models.TextField("Yang kami tawarkan", blank=True)
    questions = models.JSONField("Pertanyaan tambahan", default=list, blank=True)  # [{"label": "...", "required": bool}]
    closes_on = models.DateField("Batas akhir melamar", null=True, blank=True)
    status = models.CharField(max_length=10, choices=JobStatus.choices, default=JobStatus.DRAFT)

    class Meta: ordering = ["-created_at"]; indexes = [models.Index(fields=["status", "-created_at"])]

    def save(self, *a, **k):
        if not self.slug: self.slug = unique_slug(Job, self.title, self, fallback="lowongan")
        super().save(*a, **k)

    @property
    def is_open(self):
        return self.status == JobStatus.OPEN and (not self.closes_on or self.closes_on >= timezone.localdate())

    def __str__(self): return self.title


# ------------------------------------------------------------------ Lamaran
class Stage(models.TextChoices):
    UNVERIFIED = "unverified", "Menunggu verifikasi email"
    NEW = "new", "Diterima"
    SCREENING = "screening", "Seleksi berkas"
    TEST = "test", "Tes"
    INTERVIEW = "interview", "Wawancara"
    OFFER = "offer", "Penawaran"
    HIRED = "hired", "Diterima bekerja"
    REJECTED = "rejected", "Tidak dilanjutkan"
    WITHDRAWN = "withdrawn", "Dibatalkan pelamar"


ACTIVE_STAGES = (Stage.NEW, Stage.SCREENING, Stage.TEST, Stage.INTERVIEW, Stage.OFFER)
FINAL_STAGES = (Stage.HIRED, Stage.REJECTED, Stage.WITHDRAWN)
# Kalimat untuk pelamar: apa artinya tahap ini dan apa yang terjadi berikutnya (halaman lacak + email).
STAGE_PUBLIC = {
    Stage.UNVERIFIED: "Kami menunggu Anda membuka tautan verifikasi di email. Setelah itu lamaran Anda masuk ke tim HRD.",
    Stage.NEW: "Lamaran Anda sudah kami terima. Tim HRD akan memeriksa berkas Anda.",
    Stage.SCREENING: "Berkas Anda sedang diseleksi. Bila cocok, kami hubungi untuk tahap berikutnya.",
    Stage.TEST: "Anda lolos seleksi berkas dan masuk ke tahap tes. Jadwal dan caranya kami kabarkan lewat email/telepon.",
    Stage.INTERVIEW: "Anda masuk tahap wawancara. Tim kami akan menghubungi Anda untuk menentukan jadwal.",
    Stage.OFFER: "Kami sedang menyiapkan atau sudah menyampaikan penawaran kerja. Silakan cek email dan telepon Anda.",
    Stage.HIRED: "Selamat, Anda diterima. Tim HRD akan menghubungi Anda untuk langkah administrasi.",
    Stage.REJECTED: "Terima kasih sudah melamar. Untuk saat ini kami belum dapat melanjutkan lamaran Anda.",
    Stage.WITHDRAWN: "Lamaran ini sudah Anda batalkan.",
}
EDUCATION = [("sd", "SD"), ("smp", "SMP"), ("sma", "SMA / SMK / sederajat"), ("d3", "Diploma (D1–D3)"), ("s1", "Sarjana (S1/D4)"), ("s2", "Pascasarjana (S2/S3)")]


def hash_token(raw): return hashlib.sha256(raw.encode()).hexdigest()
def hash_email(email): return hmac.new(settings.SECRET_KEY.encode(), (email or "").strip().lower().encode(), hashlib.sha256).hexdigest()
def hash_ip(ip): return hmac.new(settings.SECRET_KEY.encode(), ("ip:" + (ip or "")).encode(), hashlib.sha256).hexdigest()


def new_ref():
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # tanpa 0/O/1/I/L agar tidak salah baca
    return f"LMR-{timezone.localdate():%y%m}-" + "".join(secrets.choice(alphabet) for _ in range(5))


class Application(models.Model):
    ref = models.CharField(max_length=20, unique=True)
    lang = models.CharField(max_length=2, default="id")        # bahasa email ke pelamar
    job = models.ForeignKey(Job, on_delete=models.PROTECT, related_name="applications")
    stage = models.CharField(max_length=12, choices=Stage.choices, default=Stage.UNVERIFIED)
    full_name = models.CharField(max_length=120)
    email = EncryptedTextField()
    email_hash = models.CharField(max_length=64, db_index=True)
    phone = EncryptedTextField(blank=True)
    birth_date = EncryptedTextField(blank=True)               # ISO yyyy-mm-dd, terenkripsi
    city = models.CharField(max_length=80, blank=True)
    education = models.CharField(max_length=6, choices=EDUCATION, blank=True)
    institution = models.CharField(max_length=140, blank=True)
    experience_years = models.PositiveSmallIntegerField(default=0)
    last_position = models.CharField(max_length=140, blank=True)
    expected_salary = models.PositiveIntegerField(null=True, blank=True)
    cover_letter = EncryptedTextField(blank=True)
    answers = EncryptedTextField(blank=True)                  # JSON [{"q": label, "a": jawaban}]
    cv_path = models.CharField(max_length=200, blank=True)    # relatif terhadap PRIVATE_MEDIA_ROOT; isi berkas terenkripsi
    cv_name = models.CharField(max_length=120, blank=True)
    cv_size = models.PositiveIntegerField(default=0)
    cv_sha256 = models.CharField(max_length=64, blank=True)
    scan_status = models.CharField(max_length=10, default="unscanned")  # clean | unscanned
    consent_at = models.DateTimeField()
    consent_version = models.CharField(max_length=20, default="v1")
    talent_pool = models.BooleanField("Boleh disimpan untuk lowongan lain", default=False)
    token_hash = models.CharField(max_length=64, db_index=True)   # pencarian cepat dari tautan
    token_enc = EncryptedTextField(blank=True)                      # token mentah terenkripsi: tautan di email selanjutnya tetap sama (tidak ada tautan lama yang mati)
    idem_key = models.CharField(max_length=40, unique=True)
    ip_hash = models.CharField(max_length=64, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    retain_until = models.DateField()
    anonymized_at = models.DateTimeField(null=True, blank=True)
    internal_note = models.CharField("Catatan HRD", max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["job", "stage", "-created_at"]), models.Index(fields=["stage", "-created_at"])]

    def delete(self, *a, **k): raise PermissionError("Lamaran tidak dihapus; gunakan anonimkan (data pribadi dibersihkan, jejak tetap)")

    def answers_list(self):
        try: return json.loads(self.answers) if self.answers else []
        except ValueError: return []

    @property
    def public_text(self): return STAGE_PUBLIC.get(self.stage, "")
    def __str__(self): return self.ref


class ApplicationEvent(models.Model):
    """Jejak lamaran, append-only (seperti AuditLog)."""
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=20)  # submitted, verified, stage, note, download, anonymized, exported, mail_failed
    from_stage = models.CharField(max_length=12, blank=True)
    to_stage = models.CharField(max_length=12, blank=True)
    public_message = models.CharField(max_length=500, blank=True)   # tampil ke pelamar
    note = models.CharField(max_length=500, blank=True)             # hanya internal
    by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta: ordering = ["created_at", "id"]

    def save(self, *a, **k):
        if self.pk: raise PermissionError("Jejak lamaran tidak boleh diubah")
        super().save(*a, **k)

    def delete(self, *a, **k): raise PermissionError("Jejak lamaran tidak boleh dihapus")


def default_retain_until(talent_pool=False):
    months = settings.CAREER_RETENTION_MONTHS * (2 if talent_pool else 1)
    return timezone.localdate() + timedelta(days=30 * months)
