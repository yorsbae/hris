"""Isi awal situs publik (contoh/demonstrasi): pengaturan, halaman Tentang Sarung, kategori, produk contoh, artikel, toko, FAQ, lowongan.
Aman dijalankan ulang (tidak menimpa isi yang sudah diubah HRD; hanya membuat bila kosong). Semua teks adalah CONTOH — ganti lewat menu Company Profile.
`--force` tidak ada: hapus/ubah lewat backend."""
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.company.models import Article, Faq, Job, Page, Product, ProductCategory, SiteSetting, StoreLocation

ABOUT_ID = """## Apa itu sarung?
Sarung adalah kain tradisional Indonesia berbentuk tabung yang menjadi bagian dari budaya dan kehidupan sehari-hari. Selain sebagai pakaian, sarung adalah lambang identitas budaya, status sosial, dan warisan leluhur yang diwariskan turun-temurun.

## Tenun Sarung Kita
Kami memproduksi sarung tenun dengan menjaga mutu bahan, ketelitian motif, dan kenyamanan pemakaian.

- Bahan pilihan dan pewarnaan yang awet
- Motif klasik dan kontemporer
- Dikerjakan oleh pengrajin dan operator terlatih"""
ABOUT_EN = """## What is a sarong?
The sarong is a traditional Indonesian fabric that has become an integral part of Indonesian culture and daily life. This typically tubular fabric serves not only as clothing but also as a symbol of cultural identity, social status, and ancestral heritage, passed down from generation to generation.

## Woven Sarong, Our Pride
We make woven sarongs with careful attention to material quality, motif precision and wearing comfort.

- Selected materials and long-lasting dyes
- Classic and contemporary motifs
- Made by trained artisans and operators"""


class Command(BaseCommand):
    help = "Isi awal situs publik dengan konten contoh (tidak menimpa data yang sudah ada)."

    def handle(self, *a, **o):
        s = SiteSetting.get()
        if not s.tagline_id:
            s.tagline_id = "Tenun Sarung Kita"; s.tagline_en = "Our Woven Sarong"
            s.hero_title_id = "Warisan Budaya dalam Setiap Helai"; s.hero_title_en = "Cultural Heritage in Every Thread"
            s.hero_text_id = "Sarung tenun berkualitas untuk keseharian, ibadah, dan acara istimewa."; s.hero_text_en = "Quality woven sarongs for everyday wear, worship and special occasions."
            s.banner_active = True; s.banner_id = "Website Dalam Pengembangan — beberapa fitur mungkin belum tersedia atau berubah sewaktu-waktu."
            s.banner_en = "Website Under Development — some features may be unavailable or change at any time."
            s.careers_intro_id = "Bergabunglah bersama kami. Lamaran cukup diisi satu kali; kami kabari perkembangannya lewat email."; s.careers_intro_en = "Join us. Fill in the application once; we will keep you posted by email."
            s.meta_description_id = "Produsen sarung tenun: produk, lokasi toko, artikel, dan lowongan kerja."; s.meta_description_en = "Woven sarong maker: products, store locations, articles and careers."
            s.save()
        Page.objects.get_or_create(slug="tentang-kami", defaults=dict(title_id="Tentang Sarung", title_en="About the Sarong", subtitle_id="Mengenal lebih dalam tentang warisan budaya Indonesia",
                                   subtitle_en="Getting to know Indonesia's cultural heritage", body_id=ABOUT_ID, body_en=ABOUT_EN, status="published"))
        cat, _ = ProductCategory.objects.get_or_create(name_id="Sarung Tenun", defaults={"name_en": "Woven Sarong"})
        cat2, _ = ProductCategory.objects.get_or_create(name_id="Sarung Anak", defaults={"name_en": "Kids' Sarong"})
        if not Product.objects.exists():
            for i, (n, c, m) in enumerate((("Sarung Wadimor Klasik", cat, "Kotak"), ("Sarung Wadimor Songket", cat, "Songket"), ("Sarung Wadimor Mini", cat2, "Polos"))):
                Product.objects.create(name=n, category=c, motif=m, summary_id="Contoh produk — ganti lewat menu Produk.", summary_en="Sample product — edit it in the Products menu.", status="published", featured=True, order=i)
        if not Article.objects.exists():
            Article.objects.create(title_id="Sejarah Singkat Sarung Nusantara", title_en="A Short History of the Nusantara Sarong", excerpt_id="Dari kain sehari-hari hingga busana kebanggaan.",
                                   excerpt_en="From everyday cloth to proud attire.", body_id="Artikel contoh. Ganti lewat menu Artikel.", body_en="Sample article. Edit it in the Articles menu.", status="published", published_at=timezone.now(), author="Redaksi")
        if not StoreLocation.objects.exists(): StoreLocation.objects.create(name="Toko Pusat", city="Batang", address="Alamat contoh — ganti lewat menu Lokasi Toko.", hours_id="Senin–Sabtu 08.00–16.00", hours_en="Mon–Sat 08:00–16:00")
        if not Faq.objects.exists():
            Faq.objects.create(question_id="Bagaimana cara merawat sarung?", question_en="How do I care for my sarong?", answer_id="Cuci dengan air dingin, hindari pemutih, jemur di tempat teduh.", answer_en="Wash in cold water, avoid bleach, dry in the shade.", order=1)
            Faq.objects.create(question_id="Apakah menerima pesanan grosir?", question_en="Do you accept wholesale orders?", answer_id="Ya. Hubungi kami lewat halaman Kontak atau WhatsApp.", answer_en="Yes. Contact us via the Contact page or WhatsApp.", order=2)
        if not Job.objects.exists():
            Job.objects.create(title="Operator Mesin Tenun", department_name="Produksi", location="Batang", employment_type="kontrak", openings=5, summary="Mengoperasikan mesin tenun dan menjaga mutu kain.",
                               description="- Mengoperasikan mesin tenun\n- Memeriksa mutu kain\n- Menjaga kebersihan area kerja", requirements="- Pendidikan minimal SMA/SMK\n- Bersedia kerja shift\n- Teliti dan disiplin",
                               benefits="- Gaji sesuai UMK\n- BPJS Kesehatan & Ketenagakerjaan", questions=[{"label": "Pernah bekerja di pabrik tekstil?", "required": False}], status="open")
        self.stdout.write("Konten contoh situs publik siap. Lihat /situs/ (pratinjau) atau jalankan SITE_MODE=public.")
