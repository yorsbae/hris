"""Isi awal situs publik (contoh/demonstrasi): pengaturan, halaman Tentang Sarung, kategori (sarung saja), produk sarung contoh, Tepak Sarung, artikel, toko, FAQ, lowongan.
Aman dijalankan ulang (tidak menimpa isi yang sudah diubah HRD; hanya membuat bila kosong). Semua teks adalah CONTOH — ganti lewat menu Company Profile.
`--force` tidak ada: hapus/ubah lewat backend."""
from pathlib import Path
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify
from apps.company.models import Article, Faq, ProductImage, Job, Motif, Page, Product, ProductCategory, ProductColor, SiteSetting, StoreLocation, Tepak

OLD_ABOUT_ID = """## Apa itu sarung?
Sarung adalah kain tradisional Indonesia berbentuk tabung yang menjadi bagian dari budaya dan kehidupan sehari-hari. Selain sebagai pakaian, sarung adalah lambang identitas budaya, status sosial, dan warisan leluhur yang diwariskan turun-temurun.

## Tenun Sarung Kita
Kami memproduksi sarung tenun dengan menjaga mutu bahan, ketelitian motif, dan kenyamanan pemakaian.

- Bahan pilihan dan pewarnaan yang awet
- Motif klasik dan kontemporer
- Dikerjakan oleh pengrajin dan operator terlatih"""
OLD_ABOUT_EN = """## What is a sarong?
The sarong is a traditional Indonesian fabric that has become an integral part of Indonesian culture and daily life. This typically tubular fabric serves not only as clothing but also as a symbol of cultural identity, social status, and ancestral heritage, passed down from generation to generation.

## Woven Sarong, Our Pride
We make woven sarongs with careful attention to material quality, motif precision and wearing comfort.

- Selected materials and long-lasting dyes
- Classic and contemporary motifs
- Made by trained artisans and operators"""


ABOUT_ID = """## Apa itu sarung?
Sarung adalah kain tradisional Indonesia berbentuk tabung yang menjadi bagian dari budaya dan kehidupan sehari-hari. Selain sebagai pakaian, sarung adalah lambang identitas budaya, status sosial, dan warisan leluhur yang diwariskan turun-temurun.

## Profil perusahaan
Kami adalah produsen sarung tenun yang menjaga mutu bahan, ketelitian motif, dan kenyamanan pemakaian, dari pemilihan benang sampai pemeriksaan akhir.

- Bahan pilihan dan pewarnaan yang awet
- Motif klasik dan kontemporer
- Dikerjakan oleh pengrajin dan operator terlatih
- Pemeriksaan mutu pada setiap lembar

## Visi
Menjadi pilihan utama sarung tenun berkualitas yang membanggakan budaya Nusantara.

## Misi
- Menjaga mutu dan konsistensi produk
- Melestarikan motif dan teknik tenun
- Menciptakan lapangan kerja dan lingkungan kerja yang layak
- Melayani pelanggan dengan jujur dan tepat waktu"""
ABOUT_EN = """## What is a sarong?
The sarong is a traditional Indonesian fabric that has become an integral part of Indonesian culture and daily life. This typically tubular fabric serves not only as clothing but also as a symbol of cultural identity, social status, and ancestral heritage, passed down from generation to generation.

## Company profile
We are a woven sarong maker that cares about material quality, motif precision and wearing comfort, from yarn selection to the final inspection.

- Selected materials and long-lasting dyes
- Classic and contemporary motifs
- Made by trained artisans and operators
- Quality inspection on every piece

## Vision
To be the first choice for quality woven sarongs that make Nusantara culture proud.

## Mission
- Keep product quality consistent
- Preserve weaving motifs and techniques
- Provide jobs and a decent workplace
- Serve customers honestly and on time"""

PRODUCTS = (  # slug, nama, subkategori, motif, warna, bahan, ukuran, harga (None = hubungi kami), unggulan, ringkasan id/en
    ("sarung-wadimor-klasik", "Sarung Wadimor Klasik", "Sarung Tenun", "Kotak klasik", "Merah marun, emas", "Katun mercerized", "115 x 220 cm", 185000, True,
     "Kotak klasik yang halus, nyaman untuk keseharian dan ibadah.", "Classic checks, smooth and comfortable for daily wear and worship."),
    ("sarung-wadimor-songket", "Sarung Wadimor Songket", "Sarung Tenun", "Songket", "Hijau, emas", "Viscose & benang metalik", "110 x 210 cm", 325000, True,
     "Sentuhan benang emas berpola berlian untuk acara istimewa.", "Gold-thread diamond pattern for special occasions."),
    ("sarung-wadimor-mini", "Sarung Wadimor Mini", "Sarung Anak", "Garis", "Biru, putih", "Katun lembut", "80 x 160 cm", 95000, True,
     "Ukuran anak, lembut di kulit dan mudah dicuci.", "Kids' size, soft on skin and easy to wash."),
    ("sarung-tumpal-premium", "Sarung Tumpal Premium", "Sarung Tenun", "Tumpal", "Cokelat, emas", "Katun premium", "115 x 220 cm", 245000, True,
     "Kepala sarung bermotif tumpal dengan tenunan rapat dan awet.", "Tumpal-patterned head panel with a dense, durable weave."),
    ("sarung-poleng-hitam", "Sarung Poleng Hitam", "Sarung Tenun", "Kotak poleng", "Hitam, putih, cokelat", "Katun", "115 x 220 cm", 150000, True,
     "Poleng kotak tegas, tampil sederhana namun berkarakter.", "Bold poleng checks, simple yet full of character."),
    ("sarung-ibadah-halus", "Sarung Ibadah Halus", "Sarung Ibadah", "Garis halus", "Biru tua, emas", "Katun halus", "115 x 220 cm", 175000, True,
     "Jatuh nyaman, tidak mudah kusut, cocok untuk salat dan pengajian.", "Falls comfortably and resists creasing, ideal for prayer and gatherings."),
    ("sarung-jacquard-eksklusif", "Sarung Jacquard Eksklusif", "Sarung Tenun", "Songket jacquard", "Ungu, emas", "Viscose jacquard", "110 x 210 cm", None, False,
     "Seri eksklusif untuk hadiah dan koleksi. Harga: hubungi kami.", "Exclusive series for gifts and collectors. Price: contact us."),
    ("sarung-anak-ceria", "Sarung Anak Ceria", "Sarung Anak", "Kotak", "Merah, kuning", "Katun", "80 x 160 cm", 89000, False,
     "Warna ceria untuk anak, aman dan nyaman dipakai.", "Cheerful colours for kids, safe and comfortable."),
)
GROUPS = (  # slug, nama id/en, contoh isi id/en, urutan — katalog HANYA sarung: satu kelompok
    ("sarung", "Sarung", "Sarong", "Sarung tenun, printing, ibadah, dan anak.", "Woven, printed, worship and kids' sarongs.", 10),
)
SUBS = (  # kelompok, nama id/en, urutan
    ("sarung", "Sarung Tenun", "Woven Sarong", 10), ("sarung", "Sarung Printing", "Printed Sarong", 15), ("sarung", "Sarung Ibadah", "Worship Sarong", 20), ("sarung", "Sarung Anak", "Kids' Sarong", 30),
)
TEPAK = (  # slug, nama, varian, ringkasan id/en, keterangan, berkas foto (apps/company/seed_assets/tepak), urutan
    ("tepak-wadimor-citra", "Tepak Wadimor Citra", "Citra", "Ornamen emas berlatar biru, tampil mewah untuk hadiah.", "Gold ornaments on a blue field, a luxurious gift box.", "Kotak keras · isi 1 sarung", "citra.jpg", 10),
    ("tepak-wadimor-penang", "Tepak Wadimor Penang", "Penang", "Lengkung kubah emas di atas dasar hitam yang elegan.", "A golden dome arch on an elegant black base.", "Kotak keras · isi 1 sarung", "penang.jpg", 20),
    ("tepak-wadimor-horizon", "Tepak Wadimor Horizon", "Horizon", "Biru langit berpola siluet dengan tumpal emas di tepi.", "Sky blue with a silhouette pattern and gold tumpal edge.", "Kotak keras · isi 1 sarung", "horizon.jpg", 30),
    ("tepak-wadimor-premium", "Tepak Wadimor Premium", "Premium", "Kemasan kaleng perak yang ringkas, awet, dan mudah disimpan.", "A compact silver tin that is durable and easy to store.", "Kaleng · isi 1 sarung", "premium.jpg", 40),
    ("tepak-wadimor-bukit-tinggi", "Tepak Wadimor Bukit Tinggi", "Bukit Tinggi", "Bergambar menara jam di atas latar emas bermotif, oleh-oleh yang berkesan.", "A clock tower on a patterned gold field, a memorable souvenir.", "Kotak keras · isi 1 sarung", "bukit-tinggi.jpg", 50),
    ("tepak-wadimor-hawai", "Tepak Wadimor Hawai", "Hawai", "Sulur emas klasik di atas dasar kuning keemasan.", "Classic gold scrolls on a golden-yellow base.", "Kotak keras · isi 1 sarung", "hawai.jpg", 60),
    ("tepak-wadimor-merah-garis", "Tepak Wadimor Merah Garis", "Merah Garis", "Kisi merah berpadu lajur garis berwarna yang tegas.", "A red lattice with bold coloured stripes.", "Kotak keras · isi 1 sarung", "merah-garis.jpg", 70),
)
STORES = (  # nama, kota, alamat, telepon, jam id/en, tautan peta
    ("Toko Pusat", "Batang", "Jl. Sultan Agung No. 21, Batang, Jawa Tengah 51211", "(0285) 555 0101", "Senin–Sabtu 08.00–16.00", "Mon–Sat 08:00–16:00", "https://www.google.com/maps/search/?api=1&query=Batang+Jawa+Tengah"),
    ("Gerai Tegal", "Tegal", "Jl. Ahmad Yani No. 88, Kota Tegal, Jawa Tengah 52111", "(0283) 555 0102", "Setiap hari 09.00–20.00", "Daily 09:00–20:00", "https://www.google.com/maps/search/?api=1&query=Tegal+Jawa+Tengah"),
    ("Gerai Pekalongan", "Pekalongan", "Jl. Hayam Wuruk No. 14, Kota Pekalongan, Jawa Tengah 51117", "(0285) 555 0103", "Senin–Sabtu 09.00–19.00", "Mon–Sat 09:00–19:00", "https://www.google.com/maps/search/?api=1&query=Pekalongan+Jawa+Tengah"),
    ("Showroom Semarang", "Semarang", "Jl. Pemuda No. 150, Kota Semarang, Jawa Tengah 50132", "(024) 555 0104", "Setiap hari 10.00–21.00", "Daily 10:00–21:00", "https://www.google.com/maps/search/?api=1&query=Semarang+Jawa+Tengah"),
)
ARTICLES = (  # slug, judul id/en, ringkasan id/en, isi id/en, hari lalu
    ("sejarah-singkat-sarung-nusantara", "Sejarah Singkat Sarung Nusantara", "A Short History of the Nusantara Sarong", "Dari kain sehari-hari hingga busana kebanggaan.", "From everyday cloth to proud attire.",
     "## Dari kain sehari-hari\nSarung dipakai luas di berbagai daerah Nusantara untuk beragam keperluan: bekerja, beribadah, hingga upacara adat.\n\n## Beragam motif\nSetiap daerah punya corak dan teknik tenun sendiri, sehingga sarung menjadi penanda identitas yang kaya.\n\n## Kini dan nanti\nSarung terus berkembang: motif klasik dijaga, sementara warna dan desain baru memperluas pemakainya.",
     "## From everyday cloth\nThe sarong is worn widely across the archipelago for work, worship and ceremonies.\n\n## Many motifs\nEvery region has its own patterns and weaving techniques, making the sarong a rich marker of identity.\n\n## Today and tomorrow\nClassic motifs are kept while new colours and designs bring in new wearers.", 3),
    ("cara-merawat-sarung-tenun", "Cara Merawat Sarung Tenun agar Awet", "How to Care for a Woven Sarong", "Tips sederhana mencuci, menjemur, dan menyimpan sarung.", "Simple tips for washing, drying and storing your sarong.",
     "## Mencuci\n- Gunakan air dingin dan sabun lembut\n- Hindari pemutih dan sikat keras\n- Cuci terpisah dari pakaian berwarna lain di pencucian pertama\n\n## Menjemur\nJemur di tempat teduh dengan angin cukup agar warna tidak cepat pudar.\n\n## Menyimpan\nLipat rapi dan simpan di tempat kering. Hindari menggantung terlalu lama agar serat tidak melar.",
     "## Washing\n- Use cold water and mild soap\n- Avoid bleach and hard brushes\n- Wash separately from other coloured clothes the first time\n\n## Drying\nDry in the shade with good airflow so colours do not fade quickly.\n\n## Storing\nFold neatly and keep in a dry place. Avoid hanging for long so the fibres do not stretch.", 40),
    ("mengenal-motif-songket", "Mengenal Motif Songket dan Maknanya", "Getting to Know Songket Motifs", "Benang emas, pola berlian, dan cerita di baliknya.", "Gold threads, diamond patterns and the stories behind them.",
     "## Apa itu songket?\nSongket adalah kain tenun dengan benang tambahan (sering berwarna emas atau perak) yang diselipkan untuk membentuk motif timbul.\n\n## Pola yang sering ditemui\n- Berlian dan belah ketupat\n- Bunga dan tumbuhan merambat\n- Garis dan tumpal pada kepala kain\n\nMakna motif berbeda di tiap daerah; tanyakan kepada pengrajin atau tim kami bila ingin tahu lebih jauh.",
     "## What is songket?\nSongket is a woven cloth with supplementary threads (often gold or silver) inserted to form raised motifs.\n\n## Common patterns\n- Diamonds and lozenges\n- Flowers and creepers\n- Lines and tumpal on the head panel\n\nMeanings differ by region; ask an artisan or our team to learn more.", 75),
    ("memilih-sarung-ibadah-dan-acara", "Memilih Sarung untuk Ibadah dan Acara Istimewa", "Choosing a Sarong for Worship and Special Occasions", "Panduan bahan, motif, dan ukuran yang tepat.", "A guide to the right material, motif and size.",
     "## Bahan\nPilih katun halus untuk harian dan ibadah; viscose atau benang metalik untuk acara istimewa.\n\n## Motif\nMotif sederhana (garis atau kotak) cocok untuk ibadah, sedangkan songket cocok untuk pesta dan pernikahan.\n\n## Ukuran\nUkuran dewasa umumnya sekitar 115 x 220 cm. Pastikan panjang cukup nyaman untuk Anda.",
     "## Material\nPick fine cotton for daily wear and worship; viscose or metallic thread for special occasions.\n\n## Motif\nSimple motifs (stripes or checks) suit worship, while songket suits parties and weddings.\n\n## Size\nAdult sizes are usually around 115 x 220 cm. Make sure the length is comfortable for you.", 120),
)
FAQS = (
    ("Bagaimana cara merawat sarung?", "How do I care for my sarong?", "Cuci dengan air dingin, hindari pemutih, jemur di tempat teduh.", "Wash in cold water, avoid bleach, dry in the shade.", 1),
    ("Apakah menerima pesanan grosir?", "Do you accept wholesale orders?", "Ya. Hubungi kami lewat halaman Kontak atau WhatsApp.", "Yes. Contact us via the Contact page or WhatsApp.", 2),
    ("Apakah bisa membeli langsung di toko?", "Can I buy directly at a store?", "Bisa. Lihat halaman Lokasi Toko untuk alamat, jam buka, dan petunjuk arah.", "Yes. See the Store Locations page for addresses, opening hours and directions.", 3),
    ("Berapa lama pengiriman?", "How long does shipping take?", "Pesanan diproses 1–2 hari kerja. Lama pengiriman bergantung tujuan dan ekspedisi.", "Orders are processed in 1–2 working days. Delivery time depends on the destination and courier.", 4),
    ("Apakah warna sarung mudah luntur?", "Do the colours fade easily?", "Kami memakai pewarnaan yang dikunci, namun cuci pertama sebaiknya terpisah dan tanpa pemutih.", "We use fixed dyes, but the first wash should be separate and without bleach.", 5),
    ("Bagaimana cara melamar kerja?", "How do I apply for a job?", "Buka halaman Karir, pilih lowongan, isi formulir, lalu verifikasi email Anda. Status lamaran bisa dipantau lewat tautan di email.", "Open the Careers page, pick a position, fill in the form, then verify your email. You can follow your status through the link in the email.", 6),
)
JOBS = (  # judul, divisi, lokasi, jenis, jumlah, ringkasan, tugas, syarat, benefit
    ("Staf Quality Control", "Produksi", "Batang", "kontrak", 2, "Memeriksa mutu kain sebelum dikemas.", "- Memeriksa cacat tenun dan warna\n- Mencatat hasil pemeriksaan\n- Berkoordinasi dengan operator", "- Pendidikan minimal SMA/SMK\n- Teliti dan jujur\n- Bersedia kerja shift", "- Gaji sesuai UMK\n- BPJS Kesehatan & Ketenagakerjaan\n- Pelatihan"),
    ("Staf Gudang & Logistik", "Gudang", "Batang", "kontrak", 3, "Mengelola stok dan pengiriman barang jadi.", "- Menerima dan menata barang\n- Menyiapkan pesanan\n- Mencatat stok masuk/keluar", "- Pendidikan minimal SMA/SMK\n- Sehat dan cekatan\n- Bisa menggunakan Excel dasar (nilai tambah)", "- Gaji sesuai UMK\n- BPJS\n- Uang makan"),
    ("Staf Desain Motif", "Desain", "Batang", "magang", 2, "Menyusun dan merapikan motif tenun untuk koleksi baru.", "- Menggambar dan menyusun motif\n- Menyiapkan acuan warna\n- Berkoordinasi dengan tim tenun", "- Pendidikan minimal SMK/D3 desain\n- Menguasai aplikasi desain dasar\n- Kreatif dan terbuka pada masukan", "- Uang saku\n- Bimbingan langsung\n- Sertifikat magang"),
    ("Teknisi Mesin Tenun", "Perawatan", "Batang", "kontrak", 2, "Merawat dan memperbaiki mesin tenun agar produksi lancar.", "- Pemeriksaan dan perawatan berkala\n- Memperbaiki kerusakan mesin\n- Mencatat riwayat perawatan", "- Pendidikan minimal SMK mesin/listrik\n- Memahami dasar mekanik\n- Bersedia kerja shift", "- Gaji sesuai UMK\n- BPJS\n- Tunjangan keahlian"),
    ("Admin Penjualan", "Penjualan", "Tegal", "tetap", 1, "Melayani pesanan dan pelanggan toko.", "- Menjawab pertanyaan pelanggan\n- Membuat faktur dan rekap penjualan\n- Koordinasi dengan gudang", "- Pendidikan minimal D3/S1\n- Komunikatif dan rapi\n- Mahir Excel/Spreadsheet", "- Gaji kompetitif\n- BPJS\n- Insentif penjualan"),
)


MOTIFS = (  # nama motif/seri sarung tenun (urutan = urutan di menu Produk), keterangan id/en
    ("Horizon", "Seri Horizon: motif tenun etnik berlatar warna pekat.", "Horizon series: ethnic woven motifs on a deep ground."),
    ("Grandmaster", "Seri unggulan: tenunan rapat, warna pekat, tampil berwibawa.", "Flagship series: dense weave, rich colours, a dignified look."),
    ("Primer", "Sarung harian bermutu dengan harga bersahabat.", "Good everyday sarongs at a friendly price."),
    ("Viscose", "Serat viscose yang jatuh lembut dan adem.", "Viscose fibre that drapes softly and stays cool."),
    ("Dobby Special", "Tenunan dobby dengan pola timbul halus.", "Dobby weave with a subtle raised pattern."),
    ("Jacquard", "Pola jacquard rumit pada seluruh badan kain.", "Intricate jacquard patterns across the whole cloth."),
    ("Mercerized", "Benang mercerized: mengilap, kuat, dan awet warna.", "Mercerized yarn: lustrous, strong and colour-fast."),
    ("Balimoon", "", ""), ("Bali", "", ""), ("Songket", "Benang metalik bermotif songket untuk acara istimewa.", "Metallic songket threads for special occasions."),
    ("Kembang", "", ""), ("Tumpal Kembang", "Kepala tumpal berpadu motif bunga.", "Tumpal head panel with floral motifs."), ("Gerimis 777", "", ""), ("Pen Mode Sutra", "", ""),
    ("Timbul", "", ""), ("Sulam", "", ""), ("Dobby Excellent Oriental", "", ""), ("Batik", "", ""), ("Viscose (GMC & BMC)", "", ""), ("Junior", "", ""),
    ("Sarung Celana & Subaiyah", "", ""), ("Best Seller", "Pilihan pelanggan paling laris.", "Our customers' all-time favourites."),
)
PRINTING = {"Balimoon", "Bali", "Kembang", "Tumpal Kembang", "Gerimis 777", "Timbul", "Sulam"}   # jenis Sarung Printing; selebihnya Sarung Tenun — pengelompokan ini ASUMSI awal, ubah di menu Motif / seri
PLACEHOLDERS = ("Primer", "Viscose", "Dobby Special", "Jacquard", "Mercerized", "Songket", "Dobby Excellent Oriental", "Pen Mode Sutra", "Balimoon", "Bali", "Kembang", "Tumpal Kembang", "Gerimis 777", "Timbul", "Sulam")
SERIES_OF = {"sarung-wadimor-klasik": "Primer", "sarung-wadimor-songket": "Songket", "sarung-tumpal-premium": "Tumpal Kembang", "sarung-jacquard-eksklusif": "Jacquard"}
C = {  # palet warna contoh: nama id, nama en, kode warna
    "marun": ("Marun", "Maroon", "#7a1f2b"), "navy": ("Biru Navy", "Navy", "#1c2f55"), "hijau": ("Hijau Botol", "Bottle Green", "#1f5a3d"), "hitam": ("Hitam", "Black", "#1b1b1f"),
    "cokelat": ("Cokelat", "Brown", "#6b4423"), "abu": ("Abu-abu", "Grey", "#6c7077"), "ungu": ("Ungu", "Purple", "#5b2f7a"), "emas": ("Emas", "Gold", "#c9a227"),
    "teal": ("Hijau Tosca", "Teal", "#1d6f72"), "krem": ("Krem", "Cream", "#d8c79b"),
}
GRANDMASTER = (  # slug, nama, motif teks, bahan, harga, ringkasan id/en, warna
    ("grandmaster", "Grandmaster", "Kotak grandmaster", "Katun mercerized premium", 265000, "Seri induk Grandmaster: tenunan padat dengan kilau halus.", "The original Grandmaster: dense weave with a soft sheen.", ("marun", "navy", "hijau", "hitam", "cokelat")),
    ("grandmaster-sge", "Grandmaster SGE", "Kotak SGE", "Katun mercerized", 275000, "Varian SGE dengan garis emas tipis di sepanjang kain.", "SGE variant with fine gold lines along the cloth.", ("navy", "marun", "hijau", "abu")),
    ("grandmaster-sjl", "Grandmaster SJL", "Kotak SJL", "Katun mercerized", 275000, "Varian SJL dengan lajur tegas, tampil rapi untuk salat Jumat.", "SJL variant with bold stripes, neat for Friday prayers.", ("hitam", "navy", "cokelat", "ungu")),
    ("grandmaster-sei", "Grandmaster SEI", "Kotak SEI", "Katun mercerized", 285000, "Varian SEI berkesan elegan dengan kombinasi dua warna.", "Elegant SEI variant with a two-tone combination.", ("hijau", "teal", "marun", "hitam", "abu")),
    ("grandmaster-jacquard-batik", "Grandmaster Jacquard Batik", "Jacquard batik", "Viscose jacquard", 345000, "Jacquard bermotif batik pada seluruh badan kain.", "Batik-pattern jacquard across the whole cloth.", ("cokelat", "navy", "ungu", "emas")),
)
class Command(BaseCommand):
    help = "Isi awal situs publik dengan konten contoh (tidak menimpa data yang sudah ada)."

    def handle(self, *a, **o):
        import datetime
        s = SiteSetting.get()
        if s.brand_name.strip() in ("", "PT X", "PT Nama Perusahaan", "PT SUKORINTEX"): s.brand_name = "Wadimor"   # nama bawaan .env / demo lama → Wadimor; nama yang sudah diganti HRD tidak disentuh
        if s.tagline_id in ("", "Tenun Sarung Kita"): s.tagline_id, s.tagline_en = "Tentu Sarung Kita!", "Surely Our Sarong!"   # slogan pada logo; slogan buatan HRD tidak disentuh
        if s.email == "info@sukorintex.example": s.email = "info@wadimor.example"
        if not s.hero_title_id:
            s.hero_title_id = "Warisan Budaya dalam Setiap Helai"; s.hero_title_en = "Cultural Heritage in Every Thread"
            s.hero_text_id = "Sarung tenun berkualitas untuk keseharian, ibadah, dan acara istimewa."; s.hero_text_en = "Quality woven sarongs for everyday wear, worship and special occasions."
            s.careers_intro_id = "Bergabunglah bersama kami. Lamaran cukup diisi satu kali; kami kabari perkembangannya lewat email."; s.careers_intro_en = "Join us. Fill in the application once; we will keep you posted by email."
            s.meta_description_id = "Wadimor: sarung tenun untuk keseharian, ibadah, dan acara istimewa. Lihat produk, tepak sarung, lokasi toko, artikel, dan lowongan kerja."; s.meta_description_en = "Wadimor: woven sarongs for daily wear, worship and special occasions. Browse products, sarong boxes, stores, articles and careers."
        if s.meta_description_id == "Wadimor: sarung tenun, baju koko, peci, dan aksesoris. Lihat produk, lookbook, lokasi toko, artikel, dan lowongan kerja.": s.meta_description_id, s.meta_description_en = "Wadimor: sarung tenun untuk keseharian, ibadah, dan acara istimewa. Lihat produk, tepak sarung, lokasi toko, artikel, dan lowongan kerja.", "Wadimor: woven sarongs for daily wear, worship and special occasions. Browse products, sarong boxes, stores, articles and careers."   # deskripsi contoh lama (masih menyebut baju koko/lookbook) → sarung saja; deskripsi buatan HRD tidak disentuh
        # kontak demo: hanya mengisi kolom yang masih kosong
        for k, v in dict(address="Jl. Industri Tekstil No. 5, Kawasan Industri, Batang, Jawa Tengah 51211", phone="(0285) 555 0100", whatsapp="628120000000", email="info@wadimor.example",
                         hours_id="Senin–Sabtu 08.00–16.00", hours_en="Mon–Sat 08:00–16:00", maps_url="https://www.google.com/maps/search/?api=1&query=Batang+Jawa+Tengah",
                         instagram="https://www.instagram.com/", facebook="https://www.facebook.com/", tiktok="https://www.tiktok.com/", marketplace_url="https://shopee.co.id/",
                         footer_note_id="", footer_note_en="").items():
            if not getattr(s, k): setattr(s, k, v)
        s.save()
        pg, _ = Page.objects.get_or_create(slug="tentang-kami", defaults=dict(title_id="Tentang Sarung", title_en="About the Sarong", subtitle_id="Mengenal lebih dalam tentang warisan budaya Indonesia",
                                           subtitle_en="Getting to know Indonesia's cultural heritage", body_id=ABOUT_ID, body_en=ABOUT_EN, status="published"))
        if pg.body_id == OLD_ABOUT_ID and pg.body_en == OLD_ABOUT_EN:   # belum pernah disunting HRD → perkaya
            pg.body_id, pg.body_en = ABOUT_ID, ABOUT_EN; pg.save()
        # kategori dua tingkat: kelompok "Sarung" → subkategori (Tenun/Printing/Ibadah/Anak). Kategori demo lama tanpa induk dipindah ke "Sarung"; kelompok "Bawahan" lama sudah diganti nama oleh migrasi 0004.
        groups, cats = {}, {}
        for gslug, gid, gen, hid, hen, order in GROUPS:
            g = ProductCategory.objects.filter(name_id=gid, parent__isnull=True).first() or ProductCategory.objects.create(name_id=gid, name_en=gen, hint_id=hid, hint_en=hen, order=order)
            if not g.hint_id: g.hint_id, g.hint_en = hid, hen; g.save()
            groups[gslug] = g
        for gslug, nid, nen, order in SUBS:
            c = ProductCategory.objects.filter(name_id=nid).first()
            if c is None: c = ProductCategory.objects.create(name_id=nid, name_en=nen, order=order, parent=groups[gslug])
            elif c.parent_id is None and not c.children.exists(): c.parent = groups[gslug]; c.order = order; c.save()
            cats[nid] = c
        for slug, name, cat, motif, colors, material, size, price, feat, sid, sen in PRODUCTS:
            p = Product.objects.filter(slug=slug).first() or Product.objects.create(slug=slug, name=name, category=cats[cat], status="published", featured=feat, order=[x[0] for x in PRODUCTS].index(slug))
            ch = False
            for k, v in dict(motif=motif, colors=colors, material=material, size=size).items():
                if not getattr(p, k): setattr(p, k, v); ch = True
            if p.summary_id in ("", "Contoh produk — ganti lewat menu Produk."): p.summary_id, p.summary_en, ch = sid, sen, True
            if not p.description_id: p.description_id = f"{sid}\n\n- Bahan: {material}\n- Ukuran: {size}\n- Warna: {colors}"; p.description_en = f"{sen}\n\n- Material: {material}\n- Size: {size}\n- Colours: {colors}"; ch = True
            if p.price is None and price is not None: p.price, p.show_price, ch = price, True, True
            if p.category_id is None: p.category, ch = cats[cat], True
            if ch: p.save()
        # motif/seri sarung tenun (menu Produk) + produk Grandmaster berpilihan warna; hanya dibuat bila belum ada, tidak menimpa suntingan HRD
        tenun, printing = cats["Sarung Tenun"], cats["Sarung Printing"]
        series = {}
        for i, (nm, hid, hen) in enumerate(MOTIFS):
            m = Motif.objects.filter(name_id=nm).first() or Motif.objects.create(name_id=nm, name_en=nm, hint_id=hid, hint_en=hen, category=printing if nm in PRINTING else tenun, order=(i + 1) * 10)
            if m.category_id is None: m.category = printing if nm in PRINTING else tenun; m.save(update_fields=["category"])   # subkategori pernah dihapus (SET_NULL) → pasang kembali agar menu & produk contoh tidak patah
            series[nm] = m
        for slug, nm in SERIES_OF.items():
            p = Product.objects.filter(slug=slug, series__isnull=True).first()
            if p: p.series = series[nm]; p.save(update_fields=["series"])
        old = timezone.make_aware(datetime.datetime(2025, 10, 8, 9, 0))
        for n, (slug, name, mt, material, price, sid, sen, cols) in enumerate(GRANDMASTER):
            p = Product.objects.filter(slug=slug).first()
            if p is None:
                size = "115 x 220 cm"; names = ", ".join(C[k][0] for k in cols)
                p = Product.objects.create(slug=slug, name=name, category=tenun, series=series["Grandmaster"], motif=mt, colors=names, material=material, size=size, price=price, show_price=True, status="published",
                                           order=100 + n, summary_id=sid, summary_en=sen, description_id=f"{sid}\n\n- Bahan: {material}\n- Ukuran: {size}\n- Warna: {names}", description_en=f"{sen}\n\n- Material: {material}\n- Size: {size}\n- Colours: {', '.join(C[k][1] for k in cols)}")
                Product.objects.filter(pk=p.pk).update(created_at=old)   # tanggal contoh seperti di menu referensi
            if not p.color_options.exists():
                for j, k in enumerate(cols): ProductColor.objects.create(product=p, name_id=C[k][0], name_en=C[k][1], hex=C[k][2], order=(j + 1) * 10)
        for i, nm in enumerate(PLACEHOLDERS):   # satu produk contoh untuk motif yang belum punya produk, agar menu Produk tidak berisi motif kosong; kunci = slug (nama boleh diubah HRD tanpa membuat duplikat)
            m = series[nm]; name = f"Sarung Wadimor {nm}"; slug = slugify(name); kind = m.category.name_id.lower()
            if m.products.exists() or Product.objects.filter(slug=slug).exists(): continue
            Product.objects.create(slug=slug, name=name, category=m.category, series=m, motif=nm, status="published", order=200 + i,
                                   summary_id=f"Seri {nm} dari Wadimor, {kind} untuk keseharian dan acara istimewa.", summary_en=f"The {nm} series by Wadimor, {kind.replace('sarung', 'sarong')} for daily wear and special occasions.")
        base = Path(__file__).resolve().parents[2] / "seed_assets"
        def attach(obj, field, rel):   # foto contoh → penyimpanan media (hanya bila kosong; HRD bebas menggantinya)
            if getattr(obj, field): return
            try: getattr(obj, field).save(Path(rel).name, ContentFile((base / rel).read_bytes()), save=True)
            except OSError: self.stderr.write(f"Foto contoh {rel} tidak bisa disimpan; memakai ilustrasi.")
        # produk "Pulau Madura · Horizon" (kemasan tepak Horizon menautkan ke sini) + produk untuk tepak lain agar setiap kartu tepak punya tujuan
        mad = Product.objects.filter(slug="sarung-wadimor-horizon-madura").first() or Product.objects.create(
            slug="sarung-wadimor-horizon-madura", name="Sarung Wadimor Horizon Pulau Madura", category=tenun, series=series["Horizon"], motif="Pulau Madura", colors="Marun", status="published", featured=True, order=5,
            summary_id="Tenun motif etnik Pulau Madura berlatar marun, dengan tumpal dan sorot di bagian belakang.", summary_en="Ethnic Pulau Madura woven motifs on a maroon ground, with a tumpal and highlight panel at the back.",
            description_id="Seri Horizon motif Pulau Madura: tumpal berpola rapi di bagian bawah dan sorot di bagian belakang kain.\n\n- Warna: Marun\n- Kemasan: tepak Horizon", description_en="Horizon series, Pulau Madura motif: a neatly patterned tumpal at the hem and a highlight panel at the back.\n\n- Colour: Maroon\n- Packaging: Horizon box")
        attach(mad, "image", "product/horizon-madura.jpg")
        if not mad.images.exists():
            gi = ProductImage(product=mad, caption_id="Tumpal / sorot belakang", caption_en="Tumpal / back panel", order=10)
            try: gi.image.save("horizon-madura-belakang.jpg", ContentFile((base / "product/horizon-madura-belakang.jpg").read_bytes()), save=True)
            except OSError: pass
        if not mad.color_options.exists(): ProductColor.objects.create(product=mad, name_id="Marun", name_en="Maroon", hex="#7a1f2b", order=10)
        prod_of = {"tepak-wadimor-horizon": mad}
        for i, (slug, name, variant, *_r) in enumerate(TEPAK):
            if slug in prod_of: continue
            pn = f"Sarung Wadimor {variant}"; ps = slugify(pn)
            prod_of[slug] = Product.objects.filter(slug=ps).first() or Product.objects.create(slug=ps, name=pn, category=tenun, status="published", order=300 + i, motif=variant,
                summary_id=f"Sarung tenun Wadimor dalam kemasan tepak {variant}.", summary_en=f"Wadimor woven sarong in the {variant} box.")
        # Tepak Sarung: dibuat bila belum ada (kunci = slug), menaut ke produk; foto contoh disalin ke media agar bisa diganti HRD
        for slug, name, variant, sid, sen, spec, fname, order in TEPAK:
            t = Tepak.objects.filter(slug=slug).first() or Tepak.objects.create(slug=slug, name=name, variant=variant, summary_id=sid, summary_en=sen, spec=spec, order=order, status="published")
            if t.product_id is None: t.product = prod_of[slug]; t.save(update_fields=["product"])
            attach(t, "image", f"tepak/{fname}")
        now = timezone.now()
        for slug, tid, ten, eid, een, bid, ben, days in ARTICLES:
            a = Article.objects.filter(slug=slug).first()
            if not a:
                Article.objects.create(slug=slug, title_id=tid, title_en=ten, excerpt_id=eid, excerpt_en=een, body_id=bid.replace("\\n", "\n"), body_en=ben.replace("\\n", "\n"),
                                       status="published", published_at=now - datetime.timedelta(days=days), author="Redaksi")
            elif a.body_id == "Artikel contoh. Ganti lewat menu Artikel.":
                a.body_id, a.body_en = bid.replace("\\n", "\n"), ben.replace("\\n", "\n"); a.save()
        vid = Article.objects.filter(slug="komitmen-seorang-bintang").first() or Article.objects.create(
            slug="komitmen-seorang-bintang", kind="video", title_id="Komitmen Seorang Bintang", title_en="The Commitment of a Star", status="published", author="Wadimor",
            published_at=now - datetime.timedelta(days=3), excerpt_id="Komitmen, disiplin, dan standar tinggi yang dijaga dengan konsisten.", excerpt_en="Commitment, discipline and high standards, kept consistently.",
            body_id="Dalam setiap langkah seorang bintang, terdapat komitmen, disiplin, dan standar tinggi yang dijaga dengan konsisten. Nilai inilah yang tercermin dalam setiap helai sarung Wadimor.", body_en="In every step of a star there is commitment, discipline and high standards, kept consistently. These are the values woven into every Wadimor sarong.")
        attach(vid, "cover", "video/bintang.jpg")
        existing = list(StoreLocation.objects.all()[:2])   # toko: hanya diisi bila kosong / masih satu placeholder lama (nama bisa diganti HRD → jangan duplikat)
        if not existing or (len(existing) == 1 and existing[0].address.startswith("Alamat contoh")):
            for i, (name, city, addr, phone, hid, hen, maps) in enumerate(STORES):
                if i == 0 and existing:
                    st = existing[0]; st.name, st.city, st.address, st.phone, st.hours_id, st.hours_en, st.maps_url = name, city, addr, phone, hid, hen, maps; st.save()
                else: StoreLocation.objects.create(name=name, city=city, address=addr, phone=phone, hours_id=hid, hours_en=hen, maps_url=maps, order=i)
        for qi, qe, ai, ae, order in FAQS:
            if not Faq.objects.filter(question_id=qi).exists(): Faq.objects.create(question_id=qi, question_en=qe, answer_id=ai, answer_en=ae, order=order)
        if not Job.objects.exists():
            Job.objects.create(title="Operator Mesin Tenun", department_name="Produksi", location="Batang", employment_type="kontrak", openings=5, summary="Mengoperasikan mesin tenun dan menjaga mutu kain.",
                               description="- Mengoperasikan mesin tenun\n- Memeriksa mutu kain\n- Menjaga kebersihan area kerja", requirements="- Pendidikan minimal SMA/SMK\n- Bersedia kerja shift\n- Teliti dan disiplin",
                               benefits="- Gaji sesuai UMK\n- BPJS Kesehatan & Ketenagakerjaan", questions=[{"label": "Pernah bekerja di pabrik tekstil?", "required": False}], status="open")
        few_jobs = Job.objects.count() < len(JOBS) + 1   # tambahan demo hanya saat lowongan masih sedikit (hindari duplikat setelah HRD mengganti judul)
        for title, dept, loc, typ, n, summ, desc, req, ben in JOBS:
            if few_jobs and not Job.objects.filter(title=title).exists():
                Job.objects.create(title=title, department_name=dept, location=loc, employment_type=typ, openings=n, summary=summ, description=desc.replace("\\n", "\n"), requirements=req.replace("\\n", "\n"),
                                   benefits=ben.replace("\\n", "\n"), closes_on=datetime.date.today() + datetime.timedelta(days=45), status="open")
        self.stdout.write("Konten contoh situs publik siap. Lihat /situs/ (pratinjau) atau jalankan SITE_MODE=public.")
