"""Putaran 34/47: kop surat Poli dibaca dari konfigurasi (apps.core.letterhead), bukan 'PT ........'. Sejak putaran 47 surat = Keterangan Istirahat/Cuti, Keterangan Cuti Hamil, Rujukan; penanda tangan = pemeriksa."""
import re
import zlib
import base64
from datetime import date, timedelta
from django.test import override_settings
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from apps.poli import services
from apps.poli.models import LetterCounter, SickLeaveLetter
from apps.poli.pdf import MONTHS, referral_pdf, sick_leave_pdf
from apps.poli.test_poli import PoliBase

KOP = dict(COMPANY_NAME="PT Maju Jaya Sentosa", COMPANY_ADDRESS="Jl. Raya Tegal No. 1, Tegal", POLI_NAME="Klinik Maju Jaya", POLI_DOCTOR_NAME="")
_ESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}


def pdf_lines(data):
    """[(y_pt, teks)] semua baris teks pada PDF buatan ReportLab (tanpa pustaka tambahan): decode ASCII85+Flate lalu baca operator Tm/Tj."""
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        raw = m.group(1).strip()
        if raw.endswith(b"~>"): raw = base64.a85decode(raw[:-2])
        try: raw = zlib.decompress(raw)
        except zlib.error: pass
        for y, txt in re.findall(rb"1 0 0 1 [\d.]+ ([\d.]+) Tm .*?\((.*)\) Tj", raw):
            txt = re.sub(rb"\\(\d{3}|.)", lambda k: bytes([int(k.group(1), 8)]) if k.group(1).isdigit() else _ESC.get(k.group(1), k.group(1)), txt)
            out.append((float(y), txt.decode("cp1252")))
    return out


def pdf_text(data): return "\n".join(t for _, t in pdf_lines(data))


class PoliLetterKopTests(PoliBase):
    def rec(self, emp=None, kind="berobat", **kw):
        emp = emp or self.e1
        exam = {"kehamilan": {"usia_minggu": 20, "hpl": "2027-01-10"}} if kind == "kehamilan" else {}
        return services.create_record(self.poli, emp, kind, "k", exam, None, "", (), **kw)[0]

    def letter(self, kind, rec=None, **kw):
        """kind: 'pulang' | 'istirahat' | 'izin_hamil' (lewat services.create_letter, seperti halaman)."""
        if kind == "izin_hamil":
            return services.create_letter(self.poli, rec or self.rec(self.e2, "kehamilan"), "izin_hamil", start_date=date.today(), months=kw.get("months", 3))[0]
        if kind == "pulang": return services.create_letter(self.poli, rec or self.rec(), "istirahat", go_home=True)[0]
        return services.create_letter(self.poli, rec or self.rec(), "istirahat", start_date=date.today(), days=kw.get("days", 3))[0]

    @override_settings(**KOP)
    def test_every_letter_kind_carries_configured_letterhead(self):
        for kind, title in (("pulang", "SURAT KETERANGAN ISTIRAHAT / CUTI"), ("istirahat", "SURAT KETERANGAN ISTIRAHAT / CUTI"), ("izin_hamil", "SURAT KETERANGAN CUTI HAMIL")):
            l = self.letter(kind, self.rec(self.e2, "kehamilan") if kind == "izin_hamil" else self.rec()); t = pdf_text(sick_leave_pdf(l))
            for need in ("PT Maju Jaya Sentosa", "Jl. Raya Tegal No. 1, Tegal", "Klinik Maju Jaya", title, f"No. {l.number}", l.record.employee.name, l.record.employee.nik):
                self.assertIn(need, t, (kind, need))
            self.assertNotIn("........", t.replace("( ........................ )", ""), kind)  # tidak ada kop placeholder

    @override_settings(COMPANY_NAME="PT Maju Jaya Sentosa", COMPANY_ADDRESS="", POLI_NAME="", POLI_DOCTOR_NAME="")
    def test_poli_name_defaults_to_company_and_empty_address_is_skipped(self):
        t = pdf_text(sick_leave_pdf(self.letter("pulang")))
        self.assertIn("POLIKLINIK PT Maju Jaya Sentosa", t)
        self.assertEqual([x for x in t.splitlines() if x.strip() == ""], [])  # tidak ada baris alamat kosong

    @override_settings(**{**KOP, "COMPANY_CITY": "Batang"})
    def test_dateline_has_city_and_long_date(self):
        l = self.letter("pulang"); d = timezone.localtime(l.issued_at).date()
        self.assertIn(f"Batang, {d.day} {MONTHS[d.month - 1]} {d.year}", pdf_text(sick_leave_pdf(l)))
        with override_settings(COMPANY_CITY=""): self.assertIn(f"{d.day} {MONTHS[d.month - 1]} {d.year}", pdf_text(sick_leave_pdf(l))); self.assertNotIn("Batang,", pdf_text(sick_leave_pdf(l)))

    @override_settings(**KOP)
    def test_doctor_written_on_the_record_signs(self):
        t = pdf_text(sick_leave_pdf(self.letter("istirahat", self.rec(doctor_name="dr. Anisa Rahayu"))))
        self.assertIn("Dokter Pemeriksa,", t); self.assertIn("( dr. Anisa Rahayu )", t); self.assertNotIn("Petugas Poliklinik,", t)

    @override_settings(**{**KOP, "POLI_DOCTOR_NAME": "dr. Perusahaan"})
    def test_env_doctor_is_only_a_form_default_not_the_signer(self):
        t = pdf_text(sick_leave_pdf(self.letter("pulang")))  # rekam medis tanpa dokter/pemeriksa → petugas pembuat, BUKAN dokter .env
        self.assertNotIn("dr. Perusahaan", t); self.assertIn("Petugas Poliklinik,", t)

    @override_settings(**KOP)
    def test_without_examiner_the_poli_user_who_made_the_record_signs(self):
        self.poli.first_name, self.poli.last_name = "Rina", "Petugas"; self.poli.save()
        t = pdf_text(sick_leave_pdf(self.letter("pulang")))
        self.assertIn("Petugas Poliklinik,", t); self.assertIn("( Rina Petugas )", t)

    @override_settings(**KOP)
    def test_rest_letter_text_for_going_home_and_for_rest_days(self):
        t = pdf_text(sick_leave_pdf(self.letter("pulang"))); self.assertIn("pulang pada hari itu juga", t); self.assertNotIn("Cuti Istirahat selama", t)
        t = pdf_text(sick_leave_pdf(self.letter("istirahat", days=3))); self.assertIn("Cuti Istirahat selama : 3 ( tiga ) hari", t)
        for f in ("Nama", "Umur", "Alamat", "Jabatan", "NIK"): self.assertIn(f, t)  # kolom formulir kertas

    @override_settings(**KOP)
    def test_age_printed_from_birth_date_and_dash_when_unknown(self):
        self.e1.birth_date = date.today().replace(year=date.today().year - 30) - timedelta(days=1); self.e1.save()
        self.assertIn("30 tahun", pdf_text(sick_leave_pdf(self.letter("pulang"))))
        self.assertIn("\n-\n", pdf_text(sick_leave_pdf(self.letter("izin_hamil"))))  # e2 tanpa tanggal lahir

    @override_settings(**KOP)
    def test_pregnancy_letter_has_no_diagnosis_but_has_hpl_and_three_month_approval(self):
        rec = services.create_record(self.poli, self.e2, "kehamilan", "k", {"kehamilan": {"usia_minggu": 20, "hpht": "2026-04-05", "hpl": "2027-01-10"}}, self.diag, "", ())[0]
        t = pdf_text(sick_leave_pdf(self.letter("izin_hamil", rec)))
        for need in ("HPHT", "05-04-2026", "HPL", "10-01-2027", "Menyetujui untuk diberikan cuti hamil selama : 3 ( tiga ) bulan", "Kabag Personalia,"): self.assertIn(need, t, need)
        self.assertNotIn("Diare", t); self.assertNotIn("A09", t)

    @override_settings(**{**KOP, "PERSONALIA_SIGNER_NAME": "Musthafa K Al Muhdhar", "PERSONALIA_SIGNER_TITLE": "Kabag Personalia"})
    def test_pregnancy_letter_has_two_signers_examiner_and_personalia(self):
        t = pdf_text(sick_leave_pdf(self.letter("izin_hamil", self.rec(self.e2, "kehamilan", doctor_name="dr. Muhammad Jaelani"))))
        self.assertIn("( dr. Muhammad Jaelani )", t); self.assertIn("( Musthafa K Al Muhdhar )", t)

    @override_settings(**{**KOP, "COMPANY_ADDRESS": "Jl. " + "Panjang " * 6 + "No. 1, Tegal"})
    def test_rest_letter_stays_in_the_upper_half_above_the_cut_line(self):
        half = A4[1] / 2
        for kind in ("pulang", "istirahat"):
            for y, txt in pdf_lines(sick_leave_pdf(self.letter(kind, self.rec(self.e1) if kind == "pulang" else self.rec(self.e2)))): self.assertGreater(y, half + 5, (kind, txt))

    @override_settings(**{**KOP, "COMPANY_ADDRESS": "Jl. " + "Panjang " * 6 + "No. 1, Tegal"})
    def test_pregnancy_letter_and_referral_are_full_page_and_inside_margins(self):
        for data in (sick_leave_pdf(self.letter("izin_hamil")), referral_pdf(services.create_referral(self.poli, self.rec(), "RSUD Kardinah Tegal", "ket", purpose="berobat"))):
            ys = [y for y, _ in pdf_lines(data)]; self.assertTrue(ys); self.assertGreater(min(ys), 15)  # tidak keluar dari kaki halaman

    @override_settings(**{**KOP, "PERSONALIA_SIGNER_NAME": "Musthafa K Al Muhdhar"})
    def test_signature_block_starts_below_the_last_body_line(self):
        for kind in ("pulang", "istirahat"):
            rec = self.rec(self.e1 if kind == "pulang" else self.e2, doctor_name="dr. Anisa Rahayu")
            lines = pdf_lines(sick_leave_pdf(self.letter(kind, rec)))
            label = next(y for y, t in lines if t == "Dokter Pemeriksa,")
            body = [y for y, t in lines if t not in ("Dokter Pemeriksa,", "( dr. Anisa Rahayu )")]
            self.assertLess(label, min(body) - 3, kind)  # label di bawah semua baris isi (≥ 3 pt), tidak menimpa

    @override_settings(**{**KOP, "COMPANY_ADDRESS": "Jl. Industri Raya Kawasan Terpadu Blok A-12 Nomor 345, Kelurahan Panggung, Kecamatan Tegal Timur, Kota Tegal, Jawa Tengah 52123"})
    def test_long_address_wraps_to_at_most_two_lines(self):
        t = pdf_text(sick_leave_pdf(self.letter("pulang")))
        parts = [x for x in t.splitlines() if x.startswith(("Jl. Industri", "Kelurahan", "Kecamatan", "Kota", "Jawa"))]
        self.assertTrue(1 <= len(parts) <= 2, parts); self.assertIn("Jl. Industri Raya", parts[0])

    @override_settings(**KOP)
    def test_legacy_letters_still_print_with_the_new_layout(self):
        r = self.rec()
        a = SickLeaveLetter.objects.create(record=r, kind="izin_pulang", number="SIP/OLD/1")
        b = SickLeaveLetter.objects.create(record=r, kind="izin_libur", number="SIL/OLD/1", start_date=date.today(), days=2)
        c = SickLeaveLetter.objects.create(record=self.rec(self.e2, "kehamilan"), kind="izin_hamil", number="SIH/OLD/1", start_date=date.today(), days=1, purpose="kontrol")
        self.assertIn("pulang pada hari itu juga", pdf_text(sick_leave_pdf(a))); self.assertIn("2 ( dua ) hari", pdf_text(sick_leave_pdf(b)))
        self.assertIn("SURAT KETERANGAN CUTI HAMIL", pdf_text(sick_leave_pdf(c)))

    @override_settings(**KOP)
    def test_referral_has_letterhead_complaint_bpjs_and_omits_diagnosis(self):
        rec = services.create_record(self.poli, self.e1, "berobat", "Mencret", {}, self.diag, "", ())[0]
        ref = services.create_referral(self.poli, rec, "RSUD Kardinah Tegal", "ket", purpose="berobat"); t = pdf_text(referral_pdf(ref))
        for need in ("PT Maju Jaya Sentosa", "Klinik Maju Jaya", "SURAT RUJUKAN", ref.number, "RSUD Kardinah Tegal", "Budi", "Mencret", "No. BPJS Kesehatan (K)", "No. BPJS Ketenagakerjaan (TK)",
                     "Tanggal kejadian / keluhan", "Rujukan spesialis"): self.assertIn(need, t, need)
        self.assertNotIn("Diare", t); self.assertNotIn("A09", t); self.assertNotIn("........", t.replace("( ........................ )", ""))

    @override_settings(**KOP)
    def test_served_pdf_uses_letterhead_end_to_end(self):
        self.login("poli"); r = self.rec()
        resp = self.client.post(f"/poli/records/{r.pk}/letter/new/", {"kind": "istirahat", "mode": "istirahat", "start_date": date.today().isoformat(), "days": "2"})
        pdf = self.client.get(resp["Location"]); self.assertEqual(pdf.status_code, 200)
        t = pdf_text(pdf.content); self.assertIn("PT Maju Jaya Sentosa", t); self.assertIn("Klinik Maju Jaya", t)
        self.assertIn(f"{date.today():%d-%m-%Y} s.d. {date.today() + timedelta(days=1):%d-%m-%Y}", t)
