"""Alur kerja per modul: langkah, status saat ini, dan siapa yang bertindak berikutnya.

Satu sumber untuk semua halaman detail. Setiap status = (langkah selesai, peran yang bertindak, kalimat, jenis).
jenis: "open" = ada tindakan berikutnya; "done" = alur selesai; "off" = berhenti (ditolak/dibatalkan).
Status dan urutan langkah di sini mengikuti model dan tampilan yang sudah ada; tidak ada status baru."""

ROLE_LABEL = {"hrd": "HRD", "dept_admin": "Admin Departemen", "poli": "Poli", "payroll": "Payroll", "superadmin": "Superadmin"}

FLOWS = {
    "request": (["Diajukan", "Keputusan HRD", "Dilaksanakan"], {
        "draft": (0, "dept_admin", "kirim pengajuan ke HRD agar diputuskan", "open"),
        "submitted": (1, "hrd", "setujui atau tolak pengajuan", "open"),
        "pending": (1, "hrd", "setujui atau tolak pengajuan", "open"),
        "approved": (2, "hrd", "laksanakan agar jadwal, saldo cuti, atau rekap absensi ikut berubah", "open"),
        "executed": (3, None, "Pengajuan sudah dilaksanakan.", "done"),
        "rejected": (1, None, "Pengajuan ditolak HRD. Buat pengajuan baru bila masih perlu.", "off"),
        "cancelled": (0, None, "Pengajuan dibatalkan.", "off"),
    }),
    "order": (["Diajukan", "Jawaban HRD"], {
        "menunggu": (1, "hrd", "jawab dengan Di-ACC atau Ditolak", "open"),
        "acc": (2, None, "Order sudah di-ACC HRD.", "done"),
        "ditolak": (1, None, "Order ditolak HRD.", "off"),
        "dibatalkan": (1, None, "Order dibatalkan pemohon.", "off"),
    }),
    "bill": (["Diterima", "Diverifikasi", "Disetujui bayar", "Dibayar"], {
        "diterima": (1, "poli", "verifikasi tagihan", "open"),
        "diverifikasi": (2, "poli", "setujui pembayaran, atau tolak tagihan", "open"),
        "disetujui": (3, "poli", "catat pembayaran setelah dibayar", "open"),
        "dibayar": (4, None, "Tagihan sudah dibayar.", "done"),
        "ditolak": (1, None, "Tagihan ditolak.", "off"),
    }),
    "visit": (["Dicatat", "Ditutup"], {
        "berjalan": (1, "poli", "tutup visit dengan hasil, atau batalkan bila salah catat", "open"),
        "selesai": (2, None, "Visit sudah selesai.", "done"),
        "batal": (1, None, "Visit dibatalkan.", "off"),
    }),
    "referral": (["Diajukan", "Dirujuk", "Selesai"], {
        "diajukan": (1, "poli", "rujuk pasien, lalu cetak surat rujukan", "open"),
        "dirujuk": (2, "poli", "catat hasil rujukan", "open"),
        "selesai": (3, None, "Rujukan sudah selesai.", "done"),
        "batal": (1, None, "Rujukan dibatalkan.", "off"),
    }),
    "purchase": (["Dipesan", "Barang diterima"], {
        "ordered": (1, "poli", "terima barang saat datang, atau batalkan pesanan bila tidak jadi", "open"),
        "partial": (1, "poli", "terima sisa barang, atau tutup pesanan bila sisanya tidak akan datang", "open"),
        "received": (2, None, "Semua barang pesanan sudah diterima.", "done"),
        "closed": (2, None, "Pesanan ditutup, sisa yang belum datang tidak ditunggu lagi.", "done"),
        "cancelled": (1, None, "Pesanan dibatalkan.", "off"),
    }),
    "applicant": (["Diterima", "Seleksi berkas", "Tes", "Wawancara", "Penawaran", "Diterima bekerja"], {
        "unverified": (0, None, "Menunggu pelamar memverifikasi email lewat tautan yang dikirim.", "wait"),
        "new": (1, "superadmin", "periksa lamaran, lalu pindahkan ke tahap berikutnya", "open"),
        "screening": (2, "superadmin", "selesaikan seleksi berkas, lalu pindahkan tahap", "open"),
        "test": (3, "superadmin", "catat hasil tes, lalu pindahkan tahap", "open"),
        "interview": (4, "superadmin", "catat hasil wawancara, lalu pindahkan tahap", "open"),
        "offer": (5, "superadmin", "tunggu jawaban pelamar atas penawaran, lalu tetapkan hasilnya", "open"),
        "hired": (6, None, "Pelamar diterima bekerja.", "done"),
        "rejected": (1, None, "Pelamar tidak dilanjutkan.", "off"),
        "withdrawn": (1, None, "Pelamar membatalkan lamaran.", "off"),
    }),
}


def describe(kind, status, role=""):
    """Kembalikan {steps: [(label, kelas)], tone, text} untuk satu halaman, atau None bila status tidak dikenal."""
    spec = FLOWS.get(kind)
    if not spec or status not in spec[1]:
        return None
    labels, states = spec
    done, actor, what, mode = states[status]
    steps = []
    for i, label in enumerate(labels, 1):
        if mode == "done" or i <= done:
            cls = "done"
        elif i == done + 1:
            cls = "off" if mode == "off" else "now"
        else:
            cls = ""
        steps.append((label, cls))
    if mode == "open":
        mine = role == actor or role == "superadmin"
        who = "Giliran Anda" if mine else "Menunggu " + ROLE_LABEL.get(actor, actor)
        text, tone = f"{who}: {what}.", "now" if mine else "wait"
    elif mode == "done":
        text, tone = what, "done"
    else:
        text, tone = what, "wait"
    return {"steps": steps, "tone": tone, "text": text}
