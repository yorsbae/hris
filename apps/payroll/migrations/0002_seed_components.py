"""Komponen standar slip gaji harian (mengikuti urutan slip contoh Okt-2025) + Tali Asih. Idempoten; tidak menimpa suntingan."""
from django.db import migrations

# kode, label, bagian, jenis, urutan, boleh negatif
COMPONENTS = [
    ("gaji_pokok", "Gaji Pokok", "pokok", "auto", 10, False),
    ("t_jabatan", "T Jabatan", "tunjangan", "auto", 20, False),
    ("t_masa_kerja", "T Masa Kerja", "tunjangan", "auto", 21, False),
    ("t_pendidikan", "T Pddkan", "tunjangan", "auto", 22, False),
    ("t_khusus", "T Khusus", "tunjangan", "auto", 23, False),
    ("t_lain", "T Lain - Lain", "tunjangan", "auto", 24, False),
    ("premi_produksi", "Premi Produksi", "premi", "manual", 30, False),
    ("lembur_otomatis", "Otomatis", "lembur", "manual", 40, False),
    ("lembur_standby", "Stanby", "lembur", "auto", 41, False),
    ("lembur_overtime", "Overtime", "lembur", "auto", 42, False),
    ("lembur_libur", "Hari Libur", "lembur", "auto", 43, False),
    ("uang_makan", "Uang Makan", "makan", "auto", 50, False),
    ("sisa_cuti", "Sisa Cuti", "lain", "auto", 60, False),
    ("insentif", "Insentif", "lain", "manual", 61, False),
    ("koreksi", "Lain2 / Koreksi", "lain", "manual", 62, True),
    ("tali_asih", "Tali Asih", "lain", "auto", 63, False),
    ("pot_jamsostek", "Jamsostek", "potongan", "auto", 70, False),
    ("pot_bpjs", "BPJS", "potongan", "auto", 71, False),
    ("pot_pinjaman", "Pinjaman", "potongan", "manual", 72, False),
    ("pot_pengobatan", "Pengobatan", "potongan", "manual", 73, False),
    ("pot_materai", "Materai", "potongan", "manual", 74, False),
    ("pot_seragam", "Seragam", "potongan", "auto", 75, False),
    ("pot_grade_d", "Grade D", "potongan", "manual", 76, False),
    ("pot_lain", "Lain2", "potongan", "manual", 77, False),
    ("pot_absensi", "Pot. Absensi", "potongan", "auto", 78, False),
]


def seed(apps, schema_editor):
    C = apps.get_model("payroll", "PayComponent")
    for code, label, section, kind, order, signed in COMPONENTS:
        C.objects.get_or_create(code=code, defaults={"label": label, "section": section, "kind": kind, "order": order, "signed": signed})


class Migration(migrations.Migration):
    dependencies = [("payroll", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
