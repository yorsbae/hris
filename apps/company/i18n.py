"""Teks antarmuka situs publik (menu, tombol, pesan). Konten (produk, artikel, dll.) memakai kolom *_id/*_en di database, bukan berkas ini."""
UI = {
    "home": {"id": "Beranda", "en": "Home"}, "products": {"id": "Produk", "en": "Products"}, "about": {"id": "Tentang Kami", "en": "About Us"},
    "stores": {"id": "Lokasi Toko", "en": "Store Locations"}, "articles": {"id": "Artikel", "en": "Articles"}, "contact": {"id": "Kontak", "en": "Contact"},
    "faq": {"id": "FAQ", "en": "FAQ"}, "archive": {"id": "Arsip", "en": "Archive"}, "careers": {"id": "Karir", "en": "Careers"}, "search": {"id": "Cari", "en": "Search"},
    "search_ph": {"id": "Cari produk atau artikel…", "en": "Search products or articles…"}, "all": {"id": "Semua", "en": "All"},
    "featured": {"id": "Produk Unggulan", "en": "Featured Products"}, "latest": {"id": "Artikel Terbaru", "en": "Latest Articles"},
    "see_all": {"id": "Lihat semua", "en": "See all"}, "read_more": {"id": "Baca selengkapnya", "en": "Read more"}, "back": {"id": "Kembali", "en": "Back"},
    "price": {"id": "Harga", "en": "Price"}, "price_ask": {"id": "Hubungi kami untuk harga", "en": "Contact us for pricing"}, "material": {"id": "Bahan", "en": "Material"},
    "size": {"id": "Ukuran", "en": "Size"}, "motif": {"id": "Motif", "en": "Motif"}, "colors": {"id": "Warna", "en": "Colors"},
    "order_wa": {"id": "Tanya / pesan via WhatsApp", "en": "Ask / order via WhatsApp"}, "no_results": {"id": "Belum ada data untuk ditampilkan.", "en": "Nothing to show yet."},
    "address": {"id": "Alamat", "en": "Address"}, "phone": {"id": "Telepon", "en": "Phone"}, "hours": {"id": "Jam buka", "en": "Opening hours"}, "map": {"id": "Lihat peta", "en": "View map"},
    "send": {"id": "Kirim", "en": "Send"}, "sent_title": {"id": "Pesan terkirim", "en": "Message sent"},
    "sent_text": {"id": "Terima kasih. Tim kami akan membalas lewat email atau telepon.", "en": "Thank you. Our team will reply by email or phone."},
    "name": {"id": "Nama", "en": "Name"}, "email": {"id": "Email", "en": "Email"}, "subject": {"id": "Perihal", "en": "Subject"}, "message": {"id": "Pesan", "en": "Message"},
    "open_jobs": {"id": "Lowongan Dibuka", "en": "Open Positions"}, "no_jobs": {"id": "Saat ini belum ada lowongan. Silakan cek lagi nanti.", "en": "No open positions right now. Please check back later."},
    "apply": {"id": "Lamar Posisi Ini", "en": "Apply for This Position"}, "closes": {"id": "Ditutup", "en": "Closes"}, "location": {"id": "Lokasi", "en": "Location"},
    "type": {"id": "Jenis", "en": "Type"}, "duties": {"id": "Tugas & tanggung jawab", "en": "Responsibilities"}, "reqs": {"id": "Persyaratan", "en": "Requirements"},
    "benefits": {"id": "Yang kami tawarkan", "en": "What we offer"}, "step1": {"id": "Data diri", "en": "About you"}, "step2": {"id": "Pendidikan & pengalaman", "en": "Education & experience"},
    "step3": {"id": "Dokumen & persetujuan", "en": "Documents & consent"}, "submit_app": {"id": "Kirim lamaran", "en": "Submit application"},
    "app_done": {"id": "Lamaran terkirim", "en": "Application submitted"},
    "app_done_text": {"id": "Kami mengirim email ke alamat yang Anda isi. Buka tautan di email itu untuk memverifikasi dan memantau status lamaran. Bila belum ada dalam beberapa menit, cek folder spam.",
                      "en": "We sent an email to the address you entered. Open the link in that email to verify and follow your application status. If it does not arrive in a few minutes, check your spam folder."},
    "track": {"id": "Status Lamaran", "en": "Application Status"}, "forgot_link": {"id": "Lupa tautan status?", "en": "Lost your status link?"},
    "forgot_text": {"id": "Masukkan email yang Anda pakai melamar. Bila ada lamaran, kami kirim tautan statusnya.", "en": "Enter the email you applied with. If an application exists, we will send you its status link."},
    "forgot_done": {"id": "Bila email itu pernah dipakai melamar, tautan status sudah kami kirim. Cek email Anda.", "en": "If that email was used to apply, we have sent the status link. Please check your email."},
    "withdraw": {"id": "Batalkan lamaran", "en": "Withdraw application"}, "delete_data": {"id": "Hapus data saya", "en": "Delete my data"},
    "download_data": {"id": "Unduh data saya", "en": "Download my data"}, "privacy": {"id": "Kebijakan privasi", "en": "Privacy policy"},
    "skip": {"id": "Lewati ke isi", "en": "Skip to content"}, "lang_switch": {"id": "English", "en": "Bahasa Indonesia"}, "menu": {"id": "Menu", "en": "Menu"},
    "archive_text": {"id": "Arsip artikel per bulan.", "en": "Article archive by month."}, "posted": {"id": "Terbit", "en": "Published"}, "by": {"id": "oleh", "en": "by"},
    "preview": {"id": "PRATINJAU — draf ikut tampil dan hanya terlihat oleh Anda.", "en": "PREVIEW — drafts are shown and only visible to you."},
    "not_found": {"id": "Halaman tidak ditemukan", "en": "Page not found"}, "too_many": {"id": "Terlalu banyak permintaan. Coba lagi nanti.", "en": "Too many requests. Please try again later."},
    "stage_hist": {"id": "Riwayat", "en": "History"}, "ref": {"id": "Nomor lamaran", "en": "Application number"}, "position": {"id": "Posisi", "en": "Position"},
    "results_for": {"id": "Hasil untuk", "en": "Results for"}, "rights": {"id": "Seluruh hak cipta dilindungi.", "en": "All rights reserved."},
}

CONSENT_ID = ("Data yang Anda isi (nama, kontak, tanggal lahir, riwayat, dan CV) hanya dipakai untuk seleksi lowongan ini oleh tim HRD, disimpan terenkripsi, "
              "tidak dijual atau dibagikan ke pihak lain, dan dihapus/dianonimkan setelah masa simpan berakhir. Anda dapat mengunduh, membatalkan, atau menghapus data kapan saja lewat tautan status.")
CONSENT_EN = ("The data you enter (name, contact, birth date, history and CV) is used only by our HR team to assess this vacancy. It is stored encrypted, never sold or shared, "
              "and deleted/anonymised after the retention period. You can download, withdraw or delete it at any time via your status link.")
