from datetime import date, timedelta
from decimal import Decimal
import csv
from django import forms
from django.forms import formset_factory
from django.utils import timezone
from apps.core.money import RupiahField
from apps.hr.models import Employee
from .models import Diagnosis, MedicalRecord, Medicine, Partner, PartnerBill, PatientAllergy, PatientVisit, Referral, SickLeaveLetter
from . import services

TA = lambda rows=3: forms.Textarea(attrs={"rows": rows})
INJURY_TYPES = [("luka_ringan", "Luka ringan / lecet"), ("luka_robek", "Luka robek"), ("memar", "Memar / benturan"), ("patah", "Patah / retak tulang"), ("terkilir", "Terkilir / keseleo"),
                ("luka_bakar", "Luka bakar"), ("mata", "Cedera mata"), ("lainnya", "Lainnya")]
EXAM_CONCLUSIONS = [("fit", "Sehat / fit bekerja"), ("fit_catatan", "Fit dengan catatan"), ("tidak_fit", "Tidak fit bekerja sementara")]


class RecordForm(forms.Form):
    nik = forms.CharField(label="NIK karyawan", max_length=20, help_text="Ketik NIK atau nama; pilih dari saran. Hanya karyawan aktif.",
                          widget=forms.TextInput(attrs={"data-lookup": "employee", "data-q-scope": "all", "placeholder": "Ketik NIK atau nama…"}))
    kind = forms.ChoiceField(label="Jenis kunjungan", choices=MedicalRecord.KINDS)
    # Pemeriksa (putaran 35): SATU isian di bagian akhir form. Ketik nama dokter/petugas medis (tanpa NIK, mis. dokter bukan karyawan) ATAU ketik NIK/nama petugas
    # departemen Poli lalu pilih dari saran (NIK tersimpan di kolom tersembunyi examiner_nik). Penyimpanan tidak berubah: examiner (karyawan Poli) / doctor_name (teks).
    examiner_nik = forms.CharField(required=False, max_length=20, widget=forms.HiddenInput())
    doctor_name = forms.CharField(label="Pemeriksa (dokter / petugas medis)", required=False, max_length=100,
                                  help_text="Dokter atau petugas medis: cukup ketik namanya (tidak perlu NIK). Petugas departemen Poli boleh dicari lewat NIK/nama lalu dipilih dari saran. Boleh kosong.",
                                  widget=forms.TextInput(attrs={"data-lookup": "employee", "data-lk-free": "1", "data-lk-fill": "name", "data-target-field": "nik", "placeholder": "Nama dokter / petugas, atau NIK petugas Poli…", "autocomplete": "off"}))
    complaint = forms.CharField(label="Keluhan", required=False, widget=TA(), max_length=4000)
    # tanda vital (opsional; rentang dibatasi agar salah ketik tertangkap)
    tensi = forms.RegexField(label="Tensi (mmHg)", required=False, regex=r"^\d{2,3}/\d{2,3}$", max_length=7, error_messages={"invalid": "Format 120/80"})
    suhu = forms.DecimalField(label="Suhu (°C)", required=False, min_value=Decimal("30"), max_value=Decimal("45"), max_digits=4, decimal_places=1)
    nadi = forms.IntegerField(label="Nadi (x/menit)", required=False, min_value=20, max_value=250)
    bb = forms.DecimalField(label="Berat badan (kg)", required=False, min_value=Decimal("1"), max_value=Decimal("500"), max_digits=5, decimal_places=1)
    tb = forms.IntegerField(label="Tinggi badan (cm)", required=False, min_value=30, max_value=250)
    # khusus kecelakaan kerja
    incident_place = forms.CharField(label="Lokasi kejadian", required=False, max_length=150)
    incident_story = forms.CharField(label="Kronologi", required=False, widget=TA(), max_length=2000)
    injury_part = forms.CharField(label="Bagian tubuh yang cedera", required=False, max_length=100)
    injury_type = forms.ChoiceField(label="Jenis cedera", required=False, choices=[("", "—")] + INJURY_TYPES)
    lost_days = forms.IntegerField(label="Perkiraan hari kehilangan kerja", required=False, min_value=0, max_value=365)
    # khusus kehamilan
    preg_hpht = forms.DateField(label="HPHT (hari pertama haid terakhir)", required=False, widget=forms.DateInput(attrs={"type": "date"}), help_text="HPL dan usia kehamilan dihitung otomatis dari HPHT bila dikosongkan.")
    preg_hpl = forms.DateField(label="HPL (hari perkiraan lahir)", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    preg_weeks = forms.IntegerField(label="Usia kehamilan (minggu)", required=False, min_value=1, max_value=45)
    preg_g = forms.IntegerField(label="G (gravida)", required=False, min_value=1, max_value=20)
    preg_p = forms.IntegerField(label="P (para)", required=False, min_value=0, max_value=20)
    preg_a = forms.IntegerField(label="A (abortus)", required=False, min_value=0, max_value=20)
    preg_tfu = forms.DecimalField(label="TFU (cm)", required=False, min_value=Decimal("0"), max_value=Decimal("60"), max_digits=4, decimal_places=1)
    preg_djj = forms.IntegerField(label="DJJ (x/menit)", required=False, min_value=60, max_value=220)
    preg_letak = forms.CharField(label="Letak / presentasi janin", required=False, max_length=60)
    # khusus pemeriksaan (MCU / cek kesehatan)
    exam_conclusion = forms.ChoiceField(label="Kesimpulan pemeriksaan", required=False, choices=[("", "—")] + EXAM_CONCLUSIONS)
    exam_result = forms.CharField(label="Hasil pemeriksaan", required=False, widget=TA(), max_length=2000)
    # alergi (putaran 47): bagian dari data pasien, bukan hanya rekam medis ini. Alergi yang sudah tercatat otomatis tampil di setiap kunjungan berikutnya (ringkasan pasien, form, detail).
    allergy_substance = forms.CharField(label="Alergen baru", required=False, max_length=100,
                                        help_text="Isi bila pasien punya alergi yang BELUM tercatat (obat, makanan, dll.). Tersimpan pada data pasien dan otomatis tampil di kunjungan berikutnya.",
                                        widget=forms.TextInput(attrs={"placeholder": "mis. Amoxicillin, udang", "autocomplete": "off"}))
    allergy_reaction = forms.CharField(label="Reaksi", required=False, max_length=200, widget=forms.TextInput(attrs={"placeholder": "mis. gatal, bengkak, sesak", "autocomplete": "off"}))
    allergy_severity = forms.ChoiceField(label="Tingkat", required=False, choices=PatientAllergy.SEVERITY, initial="sedang")
    diagnosis_code = forms.CharField(label="Kode diagnosa", required=False, max_length=10, help_text="Ketik kode atau nama (mis. A09); obat yang ditautkan di Master diagnosa terisi otomatis di resep.",
                                     widget=forms.TextInput(attrs={"data-lookup": "diagnosis", "placeholder": "Ketik kode atau nama diagnosa…"}))
    treatment = forms.CharField(label="Tindakan / anjuran", required=False, widget=TA(), max_length=4000)

    def clean_nik(self):
        self.employee = services.active_employee(nik=self.cleaned_data["nik"].strip())
        if not self.employee: raise forms.ValidationError("Karyawan tidak ditemukan atau tidak aktif.")
        return self.cleaned_data["nik"].strip()

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        dept = services.poli_department()
        if dept: self.fields["doctor_name"].widget.attrs.update({"data-q-department": str(dept.pk), "data-q-scope": "all"})  # saran hanya dari departemen Poli (form klinis → scope=all, putaran 52)
        if not self.is_bound and not self.initial.get("doctor_name"):
            from django.conf import settings
            self.initial["doctor_name"] = settings.POLI_DOCTOR_NAME  # bawaan dari .env; bisa diganti per kunjungan

    def clean_examiner_nik(self):
        nik = self.cleaned_data.get("examiner_nik", "").strip(); self.examiner = None
        if nik:
            self.examiner = services.poli_staff(nik)
            if not self.examiner: raise forms.ValidationError("Pemeriksa harus karyawan aktif departemen Poli (NIK tidak ditemukan di departemen itu).")
        return nik

    def clean_doctor_name(self): return " ".join(self.cleaned_data.get("doctor_name", "").split())

    def _resolve_examiner(self):
        """Satu isian: NIK terpilih (tersembunyi) → karyawan Poli; ketikan yang persis NIK petugas Poli → karyawan Poli; selain itu → nama dokter (teks, tanpa NIK)."""
        txt = self.cleaned_data.get("doctor_name", "")
        if getattr(self, "examiner", None):
            if txt and txt.casefold() in (self.examiner.name.casefold(), self.examiner.nik.casefold()): self.cleaned_data["doctor_name"] = ""  # nama yang sama = bukan dokter terpisah
        elif txt:
            st = services.poli_staff(txt)
            if st: self.examiner = st; self.cleaned_data["doctor_name"] = ""

    def clean_diagnosis_code(self):
        c = self.cleaned_data["diagnosis_code"].strip().upper(); self.diagnosis = None
        if c:
            self.diagnosis = Diagnosis.objects.filter(code=c).first()
            if not self.diagnosis: raise forms.ValidationError(f"Diagnosa {c} belum ada di master.")
        return c

    def clean(self):
        d = super().clean(); k = d.get("kind")
        self._resolve_examiner()
        if k == "berobat" and not d.get("complaint", "").strip(): self.add_error("complaint", "Keluhan wajib diisi untuk berobat.")
        if k == "kecelakaan_kerja":
            for f in ("incident_place", "incident_story"):
                if not d.get(f, "").strip(): self.add_error(f, "Wajib diisi untuk kecelakaan kerja.")
        if k == "kehamilan":
            hpht, hpl = d.get("preg_hpht"), d.get("preg_hpl")
            if not (d.get("preg_weeks") or hpht): self.add_error("preg_weeks", "Isi usia kehamilan atau HPHT.")
            if hpht and hpht > date.today(): self.add_error("preg_hpht", "HPHT tidak boleh di masa depan.")
            if hpht and hpl and hpl <= hpht: self.add_error("preg_hpl", "HPL harus setelah HPHT.")
            if hpht and date.today() - hpht > timedelta(days=300): self.add_error("preg_hpht", "HPHT lebih dari 300 hari lalu; periksa tanggalnya.")
            if d.get("preg_g") is not None and d.get("preg_p") is not None and d.get("preg_a") is not None and d["preg_p"] + d["preg_a"] > d["preg_g"]:
                self.add_error("preg_g", "P + A tidak boleh melebihi G.")
            if getattr(self, "employee", None) and self.employee.gender != "P": self.add_error("nik", "Pemeriksaan kehamilan hanya untuk karyawan perempuan.")
        if k == "pemeriksaan" and not d.get("exam_conclusion"): self.add_error("exam_conclusion", "Kesimpulan pemeriksaan wajib dipilih.")
        sub = " ".join((d.get("allergy_substance") or "").split()); d["allergy_substance"] = sub
        if not sub and (d.get("allergy_reaction") or "").strip(): self.add_error("allergy_substance", "Isi alergen bila reaksi diisi.")
        if sub and getattr(self, "employee", None) and PatientAllergy.objects.filter(employee=self.employee, voided_at__isnull=True, substance__iexact=sub).exists():
            self.add_error("allergy_substance", "Alergi ini sudah tercatat pada pasien (tampil di ringkasan); tidak perlu diisi lagi.")
        return d

    def allergy(self):
        """(alergen, reaksi, tingkat) bila petugas mengisi alergi baru, selain itu None."""
        d = self.cleaned_data
        return (d["allergy_substance"], (d.get("allergy_reaction") or "").strip(), d.get("allergy_severity") or "sedang") if d.get("allergy_substance") else None

    def exam(self):
        """Dict JSON-able; hanya bidang yang relevan untuk jenis kunjungan dan yang terisi."""
        d, k = self.cleaned_data, self.cleaned_data["kind"]; out = {}
        num = lambda v: float(v) if isinstance(v, Decimal) else v
        for f in ("tensi", "suhu", "nadi", "bb", "tb"):
            if d.get(f) not in (None, ""): out[f] = num(d[f])
        if k == "kecelakaan_kerja":
            inc = {"lokasi": d["incident_place"].strip(), "kronologi": d["incident_story"].strip(), "bagian_tubuh": d.get("injury_part", "").strip(),
                   "jenis_cedera": d.get("injury_type") or None, "hari_hilang": d.get("lost_days")}
            out["kecelakaan"] = {a: b for a, b in inc.items() if b not in (None, "")}
        if k == "kehamilan":
            hpht = d.get("preg_hpht"); hpl = d.get("preg_hpl") or (hpht + timedelta(days=280) if hpht else None)  # aturan Naegele: HPHT + 280 hari
            weeks = d.get("preg_weeks") or (min(45, max(1, (date.today() - hpht).days // 7)) if hpht else None)
            preg = {"hpht": hpht.isoformat() if hpht else None, "hpl": hpl.isoformat() if hpl else None, "usia_minggu": weeks, "g": d.get("preg_g"), "p": d.get("preg_p"), "a": d.get("preg_a"),
                    "tfu": num(d.get("preg_tfu")), "djj": d.get("preg_djj"), "letak": (d.get("preg_letak") or "").strip() or None}
            out["kehamilan"] = {a: b for a, b in preg.items() if b is not None}
        if k == "pemeriksaan":
            res = {"kesimpulan": d.get("exam_conclusion"), "hasil": (d.get("exam_result") or "").strip()}
            out["pemeriksaan"] = {a: b for a, b in res.items() if b}
        return out


class PrescriptionLineForm(forms.Form):
    medicine = forms.ModelChoiceField(queryset=Medicine.objects.none(), required=False, label="Obat", widget=forms.HiddenInput())  # diisi widget pencarian, bukan dropdown semua obat
    qty = forms.IntegerField(required=False, min_value=1, max_value=10000, label="Jumlah")
    dosage = forms.CharField(required=False, max_length=100, label="Aturan pakai")

    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.fields["medicine"].queryset = Medicine.objects.order_by("name")
        self.fields["medicine"].label_from_instance = lambda m: f"{m.name} (stok {m.stock} {m.unit})"

    @property
    def medicine_label(self):
        """Teks yang tampil di kotak pencarian saat formulir dirender ulang (mis. setelah galat) — satu query hanya bila ada isinya."""
        v = self["medicine"].value()
        m = Medicine.objects.filter(pk=v).first() if str(v or "").isdigit() else None
        return m.name if m else ""

    def clean(self):
        d = super().clean()
        if bool(d.get("medicine")) != bool(d.get("qty")): raise forms.ValidationError("Isi obat dan jumlahnya (atau kosongkan keduanya).")
        return d


class BasePrescriptionFormSet(forms.BaseFormSet):
    def clean(self):
        if any(f.errors for f in self.forms): return
        ids = [f.cleaned_data["medicine"].pk for f in self.forms if f.cleaned_data.get("medicine")]
        if len(set(ids)) != len(ids): raise forms.ValidationError("Obat yang sama dipilih lebih dari sekali; gabungkan jumlahnya dalam satu baris.")

    def lines(self): return [(f.cleaned_data["medicine"].pk, f.cleaned_data["qty"], f.cleaned_data.get("dosage", "")) for f in self.forms if f.cleaned_data.get("medicine")]


PrescriptionFormSet = formset_factory(PrescriptionLineForm, formset=BasePrescriptionFormSet, extra=5, max_num=services.MAX_LINES, validate_max=True)


class MedicinePickMixin(forms.Form):
    """Obat dipilih lewat pencarian (kotak teks + ID tersembunyi); ID divalidasi ulang di server."""
    medicine = forms.ModelChoiceField(queryset=Medicine.objects.all(), label="Obat", widget=forms.HiddenInput(),
                                      error_messages={"required": "Pilih obat dari saran pencarian.", "invalid_choice": "Obat tidak ditemukan."})


class AddPrescriptionForm(MedicinePickMixin):
    qty = forms.IntegerField(label="Jumlah", min_value=1, max_value=10000)
    dosage = forms.CharField(label="Aturan pakai", required=False, max_length=100)


class ReturnPrescriptionForm(forms.Form):
    prescription = forms.IntegerField(widget=forms.HiddenInput())
    qty = forms.IntegerField(label="Jumlah dikurangi", min_value=1, max_value=10000)
    reason = forms.CharField(label="Alasan (wajib)", max_length=300)


class DiagnosisMedicineForm(MedicinePickMixin):
    qty = forms.IntegerField(label="Jumlah bawaan", min_value=1, max_value=10000, initial=1)
    dosage = forms.CharField(label="Aturan pakai bawaan", required=False, max_length=100)


class MedicineForm(forms.ModelForm):
    class Meta:
        model = Medicine; fields = ["code", "name", "unit", "min_stock"]
        labels = {"code": "Kode", "name": "Nama obat", "unit": "Satuan (tablet, botol, …)", "min_stock": "Stok minimum (peringatan)"}
    def clean_code(self):
        c = self.cleaned_data["code"].strip().upper()
        if Medicine.objects.filter(code=c).exclude(pk=self.instance.pk).exists(): raise forms.ValidationError("Kode sudah dipakai.")
        return c
    def clean_min_stock(self):
        v = self.cleaned_data["min_stock"]
        if v is None or v < 0: raise forms.ValidationError("Tidak boleh negatif.")
        return v


class DiagnosisForm(forms.ModelForm):
    class Meta:
        model = Diagnosis; fields = ["code", "name", "category"]
        labels = {"code": "Kode (mis. ICD-10)", "name": "Nama diagnosa", "category": "Kategori"}
    def clean_code(self):
        c = self.cleaned_data["code"].strip().upper()
        if Diagnosis.objects.filter(code=c).exclude(pk=self.instance.pk).exists(): raise forms.ValidationError("Kode sudah dipakai.")
        return c


class StockInForm(forms.Form):
    qty = forms.IntegerField(label="Jumlah masuk", min_value=1, max_value=1000000)
    ref = forms.CharField(label="No. faktur / sumber", required=False, max_length=40)
    note = forms.CharField(label="Catatan", required=False, max_length=300)


class StockAdjustForm(forms.Form):
    delta = forms.IntegerField(label="Penyesuaian (+/−)", min_value=-1000000, max_value=1000000, help_text="Contoh: -5 untuk rusak/kedaluwarsa, 3 untuk koreksi opname.")
    note = forms.CharField(label="Alasan (wajib)", max_length=300)
    def clean_delta(self):
        if self.cleaned_data["delta"] == 0: raise forms.ValidationError("Penyesuaian tidak boleh 0.")
        return self.cleaned_data["delta"]


class AddendumForm(forms.Form):
    note = forms.CharField(label="Catatan tambahan / koreksi", widget=TA(4), max_length=4000)


class ReferralForm(forms.Form):
    """Rujukan (putaran 47): jenis mengikuti formulir Surat Pengantar Rumah Sakit; tanggal kejadian/keluhan terisi dari tanggal kunjungan, boleh diubah."""
    purpose = forms.ChoiceField(label="Jenis rujukan", choices=Referral.PURPOSES, initial="berobat")
    facility = forms.CharField(label="Fasilitas tujuan", max_length=150)
    incident_date = forms.DateField(label="Tanggal kejadian / keluhan", required=False, widget=forms.DateInput(attrs={"type": "date"}),
                                    help_text="Kosong = tanggal kunjungan. Tercetak di surat rujukan (dipakai untuk klaim BPJS bila kecelakaan kerja).")
    note = forms.CharField(label="Catatan klinis singkat", required=False, widget=TA(), max_length=2000)

    def clean_incident_date(self):
        d = self.cleaned_data.get("incident_date")
        if d and d > timezone.localdate(): raise forms.ValidationError("Tanggal kejadian / keluhan tidak boleh di masa depan.")
        return d


class LetterForm(forms.Form):
    """Surat Poli (putaran 47). `istirahat` = Surat Keterangan Istirahat / Cuti: pulang (hari itu juga) ATAU istirahat/cuti beberapa hari — satu surat, bukan dua.
    `izin_hamil` = Surat Keterangan Cuti Hamil (hanya kunjungan kehamilan): mulai tanggal + lama (bulan, bawaan 3)."""
    kind = forms.ChoiceField(label="Jenis surat", choices=[c for c in SickLeaveLetter.KINDS if c[0] in SickLeaveLetter.NEW_KINDS])
    mode = forms.ChoiceField(label="Bentuk", required=False, initial="pulang", widget=forms.RadioSelect,
                             choices=[("pulang", "Pulang — berlaku hari itu juga"), ("istirahat", "Istirahat / cuti beberapa hari")])
    start_date = forms.DateField(label="Mulai tanggal", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    days = forms.IntegerField(label="Lama (hari)", required=False, min_value=1, max_value=365)
    months = forms.IntegerField(label="Lama cuti hamil (bulan)", required=False, min_value=1, max_value=6, initial=3)

    def __init__(self, *a, record=None, **k):
        super().__init__(*a, **k); self.record = record

    def clean(self):
        d = super().clean(); k = d.get("kind")
        if k == "istirahat":
            d["mode"] = d.get("mode") or "pulang"
            if d["mode"] == "istirahat":
                if not d.get("start_date"): self.add_error("start_date", "Tanggal mulai wajib diisi.")
                if not d.get("days"): self.add_error("days", "Lama (hari) wajib diisi.")
                elif d["days"] > 30: self.add_error("days", "Istirahat / cuti maksimal 30 hari; lebih lama perlu rujukan/pengajuan cuti sakit.")
        if k == "izin_hamil":
            if not d.get("start_date"): self.add_error("start_date", "Tanggal mulai cuti hamil wajib diisi.")
            if not d.get("months"): d["months"] = 3
            if self.record is not None and self.record.kind != "kehamilan": self.add_error("kind", "Surat cuti hamil hanya untuk kunjungan jenis kehamilan.")
        return d


# ---------------------------------------------------------------- Tagihan Mitra (putaran 24, P6)
class PartnerBillForm(forms.Form):
    """Satu aturan untuk input manual DAN impor (impor tanpa rincian). Karyawan lewat NIK (aktif atau nonaktif — berobat bisa terjadi sebelum resign); data medis tidak tampil di audit."""
    partner = forms.ModelChoiceField(label="Mitra", queryset=Partner.objects.none(), empty_label="— pilih —")
    bill_number = forms.CharField(label="Nomor tagihan mitra", max_length=40)
    bill_date = forms.DateField(label="Tanggal tagihan", initial=date.today, widget=forms.DateInput(attrs={"type": "date"}))
    nik = forms.CharField(label="NIK karyawan", max_length=20, widget=forms.TextInput(attrs={"data-lookup": "employee", "data-q-scope": "all", "placeholder": "Ketik NIK atau nama…"}))
    service_date = forms.DateField(label="Tanggal pelayanan", widget=forms.DateInput(attrs={"type": "date"}))
    service_type = forms.ChoiceField(label="Jenis layanan", choices=PartnerBill.SERVICES)
    complaint = forms.CharField(label="Keluhan", required=False, widget=TA(), max_length=2000)
    diagnosis_code = forms.CharField(label="Kode diagnosa", required=False, max_length=10, help_text="Kode dari Master Diagnosa (mis. A09). Kosong bila belum ada.")
    payer = forms.ChoiceField(label="Penanggung", choices=PartnerBill.PAYERS)
    total_amount = RupiahField(label="Total tagihan (Rp)", min_value=1, max_value=5_000_000_000)

    def __init__(self, *a, instance=None, user=None, **k):
        super().__init__(*a, **k); self.instance, self.user, self.employee, self.diagnosis = instance, user, None, None
        self.fields["partner"].queryset = Partner.objects.filter(is_active=True)

    def clean_bill_number(self): return " ".join(self.cleaned_data["bill_number"].split())

    def clean(self):
        d = super().clean()
        nik = (d.get("nik") or "").strip()
        self.employee = Employee.objects.filter(nik=nik).first() if nik else None
        if nik and not self.employee: self.add_error("nik", "Karyawan dengan NIK ini tidak ditemukan.")
        code = (d.get("diagnosis_code") or "").strip()
        if code:
            self.diagnosis = Diagnosis.objects.filter(code__iexact=code).first()
            if not self.diagnosis: self.add_error("diagnosis_code", "Kode diagnosa tidak ada di Master Diagnosa.")
        sd, bd = d.get("service_date"), d.get("bill_date")
        if sd and bd and bd < sd: self.add_error("bill_date", "Tanggal tagihan tidak boleh sebelum tanggal pelayanan.")
        if bd and bd > date.today(): self.add_error("bill_date", "Tanggal tagihan tidak boleh di masa depan.")   # bersama aturan di atas ⇒ tanggal pelayanan juga tidak di masa depan
        if d.get("partner") and d.get("bill_number") and PartnerBill.objects.filter(partner=d["partner"], bill_number__iexact=d["bill_number"]).exclude(status="ditolak").exists():
            self.add_error("bill_number", "Nomor tagihan ini dari mitra yang sama sudah tercatat (yang ditolak boleh dicatat ulang).")
        return d

    def save(self, lines=()):
        d = self.cleaned_data
        return services.create_partner_bill(self.user, d["partner"], d["bill_number"], d["bill_date"], self.employee, d["service_date"], d["service_type"], d.get("complaint", ""),
                                            self.diagnosis, d["payer"], d["total_amount"], lines)


class BillLineForm(forms.Form):
    description = forms.CharField(label="Uraian", max_length=200, required=False)
    qty = forms.IntegerField(label="Jml", min_value=1, max_value=10000, required=False, initial=1)
    unit_price = RupiahField(label="Harga satuan (Rp)", min_value=0, max_value=5_000_000_000, required=False)

    def clean(self):
        d = super().clean()
        desc, price = (d.get("description") or "").strip(), d.get("unit_price")
        if not desc and price in (None, ""): return d                      # baris kosong (jumlah bawaan 1 tidak dihitung)
        if not desc or price in (None, ""): raise forms.ValidationError("Baris rincian harus lengkap: uraian dan harga satuan.")
        d["qty"] = d.get("qty") or 1
        return d


class BaseBillLineFormSet(forms.BaseFormSet):
    def lines(self):
        return [(f.cleaned_data["description"], f.cleaned_data["qty"], f.cleaned_data["unit_price"]) for f in self.forms
                if (f.cleaned_data.get("description") or "").strip() and f.cleaned_data.get("unit_price") not in (None, "") and f.cleaned_data.get("qty")]


BillLineFormSet = formset_factory(BillLineForm, formset=BaseBillLineFormSet, extra=6, max_num=40, validate_max=True)


class PartnerForm(forms.Form):
    name = forms.CharField(label="Nama mitra", max_length=150)
    kind = forms.ChoiceField(label="Jenis", choices=Partner.KINDS)
    contact = forms.CharField(label="Kontak", required=False, max_length=200)

    def clean_name(self):
        v = " ".join(self.cleaned_data["name"].split())
        if Partner.objects.filter(name__iexact=v).exists(): raise forms.ValidationError("Mitra dengan nama ini sudah ada.")
        return v


class AllergyForm(forms.Form):
    substance = forms.CharField(label="Alergen", max_length=100, help_text="Obat, makanan, atau lainnya (mis. Amoxicillin, udang).")
    reaction = forms.CharField(label="Reaksi", required=False, max_length=200)
    severity = forms.ChoiceField(label="Tingkat", choices=PatientAllergy.SEVERITY, initial="sedang")


class VisitForm(forms.Form):
    """Rekap visit pasien (putaran 46). NIK dipilih lewat saran; hanya karyawan aktif."""
    nik = forms.CharField(label="NIK karyawan", max_length=20, help_text="Ketik NIK atau nama; pilih dari saran. Hanya karyawan aktif.",
                          widget=forms.TextInput(attrs={"data-lookup": "employee", "data-q-scope": "all", "placeholder": "Ketik NIK atau nama…"}))
    kind = forms.ChoiceField(label="Jenis visit", choices=[("", "— pilih —")] + PatientVisit.KINDS)
    visit_date = forms.DateField(label="Tanggal visit", widget=forms.DateInput(attrs={"type": "date"}))
    location = forms.CharField(label="Lokasi / alamat", required=False, max_length=200, help_text="Wajib untuk kunjungan rumah (alamat atau nama tempat tinggal/mess).")
    rest_days = forms.IntegerField(label="Hari istirahat", required=False, min_value=1, max_value=365, help_text="Untuk jenis Sakit; boleh kosong.")
    complaint = forms.CharField(label="Keluhan / catatan", required=False, widget=TA(), max_length=4000)

    def clean_nik(self):
        self.employee = services.active_employee(nik=self.cleaned_data["nik"].strip())
        if not self.employee: raise forms.ValidationError("Karyawan tidak ditemukan atau tidak aktif.")
        return self.cleaned_data["nik"].strip()

    def clean_visit_date(self):
        d = self.cleaned_data["visit_date"]
        if d > timezone.localdate(): raise forms.ValidationError("Tanggal visit tidak boleh di masa depan.")
        return d

    def clean(self):
        c = super().clean()
        if c.get("kind") == "rumah" and not (c.get("location") or "").strip(): self.add_error("location", "Kunjungan rumah wajib mengisi lokasi / alamat.")
        return c
