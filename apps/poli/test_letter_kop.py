"""Putaran 34: kop & penanda tangan surat Poli (izin pulang/libur/hamil + rujukan) dibaca dari konfigurasi (apps.core.letterhead), bukan 'PT ........'."""
import re
import zlib
import base64
from datetime import date, timedelta
from django.test import override_settings
from reportlab.lib.pagesizes import A4
from apps.poli import services
from apps.poli.models import LetterCounter, SickLeaveLetter
from apps.poli.pdf import referral_pdf, sick_leave_pdf
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
    def rec(self, emp=None, kind="berobat"):
        emp = emp or self.e1
        exam = {"kehamilan": {"usia_minggu": 20, "hpl": "2027-01-10"}} if kind == "kehamilan" else {}
        return services.create_record(self.poli, emp, kind, "k", exam, None, "", ())[0]

    def letter(self, kind, rec=None):
        rec = rec or self.rec(self.e2 if kind == "izin_hamil" else self.e1, "kehamilan" if kind == "izin_hamil" else "berobat")
        extra = {} if kind == "izin_pulang" else {"start_date": date.today(), "days": 3, "purpose": "kontrol" if kind == "izin_hamil" else ""}
        return SickLeaveLetter.objects.create(record=rec, kind=kind, number=LetterCounter.next_number(SickLeaveLetter.COUNTER[kind], date.today()), **extra)

    @override_settings(**KOP)
    def test_every_letter_kind_carries_configured_letterhead(self):
        for kind, title in (("izin_pulang", "SURAT IZIN PULANG"), ("izin_libur", "SURAT IZIN LIBUR / ISTIRAHAT"), ("izin_hamil", "SURAT IZIN HAMIL")):
            l = self.letter(kind); t = pdf_text(sick_leave_pdf(l))
            for need in ("PT Maju Jaya Sentosa", "Jl. Raya Tegal No. 1, Tegal", "Klinik Maju Jaya", title, f"No. {l.number}", l.record.employee.name, l.record.employee.nik):
                self.assertIn(need, t, (kind, need))
            self.assertNotIn("........", t.replace("( ........................ )", ""), kind)  # tidak ada kop placeholder

    @override_settings(COMPANY_NAME="PT Maju Jaya Sentosa", COMPANY_ADDRESS="", POLI_NAME="", POLI_DOCTOR_NAME="")
    def test_poli_name_defaults_to_company_and_empty_address_is_skipped(self):
        t = pdf_text(sick_leave_pdf(self.letter("izin_pulang")))
        self.assertIn("POLIKLINIK PT Maju Jaya Sentosa", t)
        self.assertEqual([x for x in t.splitlines() if x.strip() == ""], [])  # tidak ada baris alamat kosong

    @override_settings(**{**KOP, "POLI_DOCTOR_NAME": "dr. Anisa Rahayu"})
    def test_configured_doctor_signs(self):
        t = pdf_text(sick_leave_pdf(self.letter("izin_libur")))
        self.assertIn("Dokter Perusahaan,", t); self.assertIn("( dr. Anisa Rahayu )", t); self.assertNotIn("Petugas Poliklinik,", t)

    @override_settings(**KOP)
    def test_without_configured_doctor_the_poli_user_who_made_the_record_signs(self):
        self.poli.first_name, self.poli.last_name = "Rina", "Petugas"; self.poli.save()
        t = pdf_text(sick_leave_pdf(self.letter("izin_pulang")))
        self.assertIn("Petugas Poliklinik,", t); self.assertIn("( Rina Petugas )", t)

    @override_settings(**KOP)
    def test_pregnancy_letter_has_no_diagnosis_but_has_pregnancy_info(self):
        rec = services.create_record(self.poli, self.e2, "kehamilan", "k", {"kehamilan": {"usia_minggu": 20, "hpl": "2027-01-10"}}, self.diag, "", ())[0]
        t = pdf_text(sick_leave_pdf(self.letter("izin_hamil", rec)))
        self.assertIn("usia kehamilan 20 minggu", t); self.assertIn("HPL 10-01-2027", t); self.assertNotIn("Diare", t); self.assertNotIn("A09", t)

    @override_settings(**{**KOP, "COMPANY_ADDRESS": "Jl. " + "Panjang " * 6 + "No. 1, Tegal", "POLI_DOCTOR_NAME": "dr. Anisa Rahayu"})
    def test_all_text_stays_in_the_upper_half_above_the_cut_line(self):
        half = A4[1] / 2
        for kind in ("izin_pulang", "izin_libur", "izin_hamil"):
            for y, txt in pdf_lines(sick_leave_pdf(self.letter(kind))): self.assertGreater(y, half + 5, (kind, txt))
        ref = services.create_referral(self.poli, self.rec(), "RSUD Kardinah Tegal", "ket")
        for y, txt in pdf_lines(referral_pdf(ref)): self.assertGreater(y, half + 5, txt)

    @override_settings(**{**KOP, "POLI_DOCTOR_NAME": "dr. Anisa Rahayu"})
    def test_signature_block_starts_below_the_last_body_line(self):
        for kind in ("izin_pulang", "izin_libur", "izin_hamil"):
            lines = pdf_lines(sick_leave_pdf(self.letter(kind)))
            label = next(y for y, t in lines if t == "Dokter Perusahaan,")
            body = [y for y, t in lines if t not in ("Dokter Perusahaan,", "( dr. Anisa Rahayu )")]
            self.assertLess(label, min(body) - 3, kind)  # label di bawah semua baris isi (≥ 3 pt), tidak menimpa

    @override_settings(**{**KOP, "COMPANY_ADDRESS": "Jl. Industri Raya Kawasan Terpadu Blok A-12 Nomor 345, Kelurahan Panggung, Kecamatan Tegal Timur, Kota Tegal, Jawa Tengah 52123"})
    def test_long_address_wraps_to_at_most_two_lines(self):
        t = pdf_text(sick_leave_pdf(self.letter("izin_pulang")))
        parts = [x for x in t.splitlines() if x.startswith(("Jl. Industri", "Kelurahan", "Kecamatan", "Kota", "Jawa"))]
        self.assertTrue(1 <= len(parts) <= 2, parts); self.assertIn("Jl. Industri Raya", parts[0])

    @override_settings(**KOP)
    def test_referral_uses_letterhead_and_omits_diagnosis(self):
        rec = services.create_record(self.poli, self.e1, "berobat", "Mencret", {}, self.diag, "", ())[0]
        ref = services.create_referral(self.poli, rec, "RSUD Kardinah Tegal", "ket"); t = pdf_text(referral_pdf(ref))
        for need in ("PT Maju Jaya Sentosa", "Klinik Maju Jaya", "SURAT RUJUKAN", ref.number, "RSUD Kardinah Tegal", "Budi"): self.assertIn(need, t, need)
        self.assertNotIn("Diare", t); self.assertNotIn("Mencret", t); self.assertNotIn("........", t.replace("( ........................ )", ""))

    @override_settings(**KOP)
    def test_served_pdf_uses_letterhead_end_to_end(self):
        self.login("poli"); r = self.rec()
        resp = self.client.post(f"/poli/records/{r.pk}/letter/new/", {"kind": "izin_libur", "start_date": date.today().isoformat(), "days": "2"})
        pdf = self.client.get(resp["Location"]); self.assertEqual(pdf.status_code, 200)
        t = pdf_text(pdf.content); self.assertIn("PT Maju Jaya Sentosa", t); self.assertIn("Klinik Maju Jaya", t)
        self.assertIn(f"{date.today():%d-%m-%Y} s/d {date.today() + timedelta(days=1):%d-%m-%Y}", t)
