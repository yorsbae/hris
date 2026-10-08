"""Data demo untuk uji coba UI (putaran 16). JANGAN dipakai di produksi.
    python manage.py seed_demo                 # 80 karyawan, sandi demo dicetak di akhir
    python manage.py seed_demo --employees 200 --password 'Sandi-Demo-123'
Aman diulang: bila karyawan demo (NIK berawalan DM) sudah ada, perintah berhenti (pakai --force untuk menambah lagi tidak didukung; kosongkan DB demo)."""
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from apps.core.models import AuditLog, Notification, Role, User
from apps.hr import models as hr
from apps.hrd import models as hrd
from apps.poli import models as pl, services as ps

FIRST = ["Budi", "Siti", "Andi", "Rina", "Dewi", "Agus", "Wulan", "Rizky", "Fitri", "Joko", "Sri", "Dimas", "Putri", "Eko", "Ayu", "Hendra", "Lestari", "Bayu", "Nur", "Yusuf", "Maya", "Rudi", "Intan", "Fajar", "Tri", "Ahmad", "Dian", "Slamet", "Ratna", "Doni"]
LAST = ["Santoso", "Rahmawati", "Pratama", "Amelia", "Lestari", "Wijaya", "Kusuma", "Hidayat", "Saputra", "Utami", "Nugroho", "Handayani", "Setiawan", "Permana", "Sari", "Firmansyah", "Susanto", "Maharani", "Wibowo", "Purnama"]
DEPTS = [("PRD", "Produksi", 0.34), ("GDG", "Gudang", 0.14), ("QC", "Quality Control", 0.12), ("PKG", "Packaging", 0.14), ("MTC", "Maintenance", 0.09),
         ("HRD", "HRD", 0.04), ("FIN", "Finance", 0.05), ("POL", "Poliklinik", 0.03), ("GA", "General Affair", 0.05)]
POS = [("Staf", 1), ("Operator", 1), ("Teknisi", 2), ("Leader", 3), ("Supervisor", 4), ("Manager", 5)]
DIAG = [("J00", "Nasofaringitis akut (common cold)"), ("J06.9", "Infeksi saluran napas atas akut"), ("A09", "Diare dan gastroenteritis"), ("K29.7", "Gastritis"),
        ("R51", "Sakit kepala"), ("M54.5", "Nyeri punggung bawah"), ("I10", "Hipertensi esensial"), ("L30.9", "Dermatitis"), ("S61.9", "Luka terbuka pergelangan/tangan"), ("Z00.0", "Pemeriksaan kesehatan umum")]
MEDS = [("OB001", "Paracetamol 500mg", "tablet", 600, 100), ("OB002", "Amoxicillin 500mg", "kapsul", 300, 60), ("OB003", "Antasida DOEN", "tablet", 400, 80), ("OB004", "CTM 4mg", "tablet", 500, 80),
        ("OB005", "Ibuprofen 400mg", "tablet", 350, 60), ("OB006", "Oralit", "sachet", 200, 40), ("OB007", "Vitamin C 500mg", "tablet", 500, 80), ("OB008", "Betadine 30ml", "botol", 40, 10),
        ("OB009", "Kasa steril", "pak", 120, 30), ("OB010", "Amlodipine 5mg", "tablet", 200, 40), ("OB011", "Salep hidrokortison", "tube", 25, 15), ("OB012", "Ambroxol 30mg", "tablet", 250, 50)]
COMPLAINTS = ["Keluhan flu dan demam", "Sakit kepala sejak pagi", "Nyeri lambung setelah makan", "Diare sejak semalam", "Nyeri punggung setelah angkat barang", "Batuk pilek 3 hari", "Pusing dan lemas", "Tensi tinggi, kontrol rutin", "Gatal-gatal di tangan"]
REAL_NOW = timezone.now


class Command(BaseCommand):
    help = "Isi data demo (karyawan, pengajuan, cuti, operasional HRD, poliklinik) untuk uji coba. Bukan untuk produksi."

    def add_arguments(self, p):
        p.add_argument("--employees", type=int, default=80)
        p.add_argument("--password", default="Demo#HRIS-2026", help="sandi semua akun demo (min. 10 karakter)")
        p.add_argument("--seed", type=int, default=2026)

    @transaction.atomic
    def handle(self, *a, **o):
        if not settings.FIELD_ENCRYPTION_KEY: raise CommandError("FIELD_ENCRYPTION_KEY belum diisi di .env (lihat .env.example); data sensitif karyawan terenkripsi.")
        if len(o["password"]) < 10: raise CommandError("--password minimal 10 karakter")
        if hr.Employee.all_objects.filter(nik__startswith="DM").exists(): raise CommandError("Data demo sudah ada (NIK berawalan DM). Tidak ada yang diubah.")
        self.r, self.pw, self.today = random.Random(o["seed"]), o["password"], timezone.localdate()
        n = max(20, min(o["employees"], 1500))
        self.users(); self.master(); self.employees(n); self.requests(); self.leave(); self.info(); self.hrd_ops(); self.poli(); self.audit()
        s = self.stat
        self.stdout.write(self.style.SUCCESS(f"Data demo selesai: {s}"))
        self.stdout.write("Akun demo (sandi sama untuk semuanya): " + o["password"])
        for u, role in self.accounts: self.stdout.write(f"  {u:<18} {role}")

    # ---------- util
    def at(self, d, h=None, m=None):  # datetime sadar-zona dari tanggal
        return timezone.make_aware(datetime.combine(d, time(h if h is not None else self.r.randint(7, 16), m if m is not None else self.r.randint(0, 59))))

    # ---------- akun
    def users(self):
        self.accounts = []
        def mk(username, first, last, role, dept=None, **kw):
            u = User.objects.create_user(username, password=self.pw, first_name=first, last_name=last, role=role, department=dept, must_change_password=False, **kw)
            self.accounts.append((username, role)); return u
        self.mk = mk
        self.root = mk("superadmin", "Super", "Admin", Role.SUPERADMIN, is_staff=True, is_superuser=True)
        self.hrd_u = mk("hrd", "Yordan", "Abdillah", Role.HRD)
        self.poli_u = mk("poli", "Dr. Anisa", "Rahayu", Role.POLI)

    # ---------- master
    def master(self):
        self.depts = {c: hr.Department.objects.create(code=c, name=n) for c, n, _ in DEPTS}
        self.pos = {n: hr.Position.objects.create(name=n, level=l) for n, l in POS}
        self.shifts = [hr.Shift.objects.create(name="Shift Pagi", start=time(7), end=time(15)), hr.Shift.objects.create(name="Shift Siang", start=time(15), end=time(23)),
                       hr.Shift.objects.create(name="Shift Malam", start=time(23), end=time(7), crosses_midnight=True)]
        for c, nm in (("PRD", "Budi"), ("GDG", "Andi"), ("QC", "Rina"), ("PKG", "Siti")):
            self.mk(f"admin_{c.lower()}", nm, f"Admin {self.depts[c].name}", Role.DEPT_ADMIN, self.depts[c])

    # ---------- karyawan + kontrak + BPJS
    def employees(self, n):
        r, today = self.r, self.today
        codes = [c for c, _, _ in DEPTS]; weights = [w for _, _, w in DEPTS]
        self.emps, used = [], set()
        for i in range(1, n + 1):
            c = r.choices(codes, weights)[0]; d = self.depts[c]
            g = r.choice("LP"); name = f"{r.choice(FIRST)} {r.choice(LAST)}"
            while name in used: name = f"{r.choice(FIRST)} {r.choice(LAST)} {r.choice(LAST)}"
            used.add(name)
            tenure = r.randint(30, 3200); join = today - timedelta(days=tenure)
            lvl = "Operator" if c in ("PRD", "PKG", "GDG") and r.random() < .7 else r.choice(["Staf", "Teknisi", "Leader", "Supervisor"] if tenure > 900 else ["Staf", "Operator", "Teknisi"])
            e = hr.Employee.objects.create(
                nik=f"DM{i:05d}", nik_ktp="33" + "".join(str(r.randint(0, 9)) for _ in range(14)), name=name, gender=g,
                marital_status=r.choice(["Belum menikah", "Menikah", "Menikah"]), education=r.choice(["SMA", "SMK", "D3", "S1"]),
                address=f"Jl. Contoh No. {r.randint(1, 99)}, Tegal", phone="08" + "".join(str(r.randint(0, 9)) for _ in range(10)), department=d, position=self.pos[lvl],
                status="nonaktif" if r.random() < .04 else "aktif", join_date=join, shift=r.choice(self.shifts) if c in ("PRD", "PKG", "GDG", "MTC") else self.shifts[0],
                bpjs_kes="000" + "".join(str(r.randint(0, 9)) for _ in range(10)), bpjs_tk="26" + "".join(str(r.randint(0, 9)) for _ in range(9)),
                npwp="".join(str(r.randint(0, 9)) for _ in range(15)), bank_name=r.choice(["BCA", "BRI", "Mandiri", "BNI"]), bank_account="".join(str(r.randint(0, 9)) for _ in range(10)))
            self.emps.append(e)
            tetap = tenure > 700 and r.random() < .55
            start = join if tetap else max(join, today - timedelta(days=r.randint(10, 330)))
            end = None if tetap else start + timedelta(days=365) if (today - start).days < 300 else today + timedelta(days=r.choice([5, 12, 25, 45, 58, 120]))
            hr.Contract.objects.create(employee=e, number=f"DM-{'PKWTT' if tetap else 'PKWT'}-{i:05d}", kind="PKWTT" if tetap else "PKWT", start=start, end=end,
                                       status="aktif" if e.status == "aktif" else "selesai")
            for sch in ("kes", "tk"):
                st = "aktif" if (e.status == "aktif" and r.random() < .95) else "nonaktif"
                hrd.BpjsMembership.objects.create(employee=e, scheme=sch, status=st, effective_date=join, updated_by=self.hrd_u, note="Data awal (demo)")
                hrd.BpjsStatusLog.objects.create(employee=e, scheme=sch, old_status="", new_status=st, effective_date=join, changed_by=self.hrd_u, note="Data awal (demo)")
        by = {}
        for e in self.emps: by.setdefault(e.department_id, []).append(e)
        for lst in by.values():
            boss = max(lst, key=lambda x: x.position.level if x.position else 0)
            for e in lst:
                if e.pk != boss.pk: e.supervisor = boss; e.save(update_fields=["supervisor"])
        self.active = [e for e in self.emps if e.status == "aktif"]
        self.stat = f"{len(self.emps)} karyawan"

    # ---------- pengajuan (berbagai status & jenis)
    def requests(self):
        r, today = self.r, self.today
        admins = {u.department_id: u for u in User.objects.filter(role=Role.DEPT_ADMIN)}
        specs = [("cuti", "pending", 5), ("mutasi_dept", "pending", 2), ("shift", "pending", 2), ("izin", "pending", 1), ("cuti", "approved", 4), ("cuti", "rejected", 3), ("sakit", "approved", 3),
                 ("cuti", "executed", 8), ("izin_terlambat", "executed", 4), ("tukar_shift", "submitted", 2), ("cuti", "draft", 2), ("izin_pulang", "cancelled", 1), ("mutasi_jabatan", "approved", 1)]
        self.reqs = []
        for typ, st, cnt in specs:
            for _ in range(cnt):
                e = r.choice(self.active); who = admins.get(e.department_id, self.hrd_u)
                s = today + timedelta(days=r.randint(2, 40)) if st in ("pending", "approved", "draft", "submitted") else today - timedelta(days=r.randint(5, 120))
                days = r.randint(1, 4); p = {"reason": r.choice(["Keperluan keluarga", "Acara pernikahan saudara", "Pulang kampung", "Kontrol dokter", "Urusan administrasi"])}
                if typ == "cuti": p.update(start_date=s.isoformat(), end_date=(s + timedelta(days=days - 1)).isoformat())
                elif typ == "mutasi_dept":
                    dest = r.choice([d for d in self.depts.values() if d.pk != e.department_id]); p.update(department_id=dest.pk, department_name=dest.name, effective_date=s.isoformat())
                elif typ == "mutasi_jabatan":
                    ps_ = self.pos["Leader"]; p.update(position_id=ps_.pk, position_name=ps_.name, effective_date=s.isoformat())
                elif typ == "shift":
                    sh = r.choice(self.shifts); p.update(shift_id=sh.pk, shift_name=sh.name, effective_date=s.isoformat())
                elif typ == "tukar_shift":
                    sh = r.choice(self.shifts); p.update(date=s.isoformat(), shift_id=sh.pk, shift_name=sh.name)
                elif typ in ("izin", "sakit"): p.update(start_date=s.isoformat(), end_date=(s + timedelta(days=days - 1)).isoformat())
                else: p.update(date=s.isoformat(), time="10:30")
                q = hr.ChangeRequest.objects.create(type=typ, employee=e, department=e.department, status=st, payload=p, requested_by=who,
                                                    decided_by=self.hrd_u if st in ("approved", "rejected", "executed") else None,
                                                    note="Ditolak: kuota departemen penuh" if st == "rejected" else "")
                hr.ChangeRequest.objects.filter(pk=q.pk).update(created_at=self.at(today if st == "pending" else today - timedelta(days=r.randint(1, 60))))
                self.reqs.append(q)
        self.stat += f", {len(self.reqs)} pengajuan"

    # ---------- saldo cuti
    def leave(self):
        y = self.today.year
        for e in self.active:
            hr.LeaveLedger.objects.create(employee=e, year=y, kind="grant", days=Decimal("12"), note="Jatah tahunan (demo)", created_by=self.hrd_u)
        for q in self.reqs:
            if q.type == "cuti" and q.status == "executed" and q.employee.status == "aktif":
                p = q.payload; d = (date.fromisoformat(p["end_date"]) - date.fromisoformat(p["start_date"])).days + 1
                if date.fromisoformat(p["start_date"]).year == y:
                    hr.LeaveLedger.objects.create(employee=q.employee, year=y, kind="use", days=Decimal(-d), request=q, note="Pemakaian cuti (demo)", created_by=self.hrd_u)

    # ---------- pengumuman + notifikasi
    def info(self):
        for kind, title, body in (("pengumuman", "Jadwal libur Hari Raya & cuti bersama", "Silakan ajukan cuti melalui Admin Departemen paling lambat 7 hari sebelumnya."),
                                  ("peraturan", "Aturan penggunaan APD di area produksi", "Wajib memakai sepatu safety, topi, dan masker selama berada di area produksi."),
                                  ("pemberitahuan", "Pemeriksaan kesehatan berkala (MCU)", "MCU karyawan dilaksanakan di Poliklinik. Jadwal per departemen menyusul.")):
            a = hr.Announcement.objects.create(kind=kind, title=title, body=body, all_departments=True, created_by=self.hrd_u)
        pend = [q for q in self.reqs if q.status == "pending"][:6]
        for u in User.objects.filter(role__in=[Role.HRD, Role.SUPERADMIN]):
            for q in pend:
                Notification.objects.create(user=u, kind="request", title=f"Pengajuan {q.type} {q.employee.name} menunggu persetujuan", link=f"/requests/{q.pk}/")
        Notification.objects.create(user=self.hrd_u, kind="contract", title="Beberapa kontrak berakhir dalam 30 hari", link="/employees/", is_read=True)

    # ---------- operasional HRD
    def hrd_ops(self):
        r, today = self.r, self.today
        for k, st in (("kematian", "diajukan"), ("pernikahan", "disetujui"), ("kelahiran", "dibayar"), ("musibah", "ditolak"), ("pernikahan", "diajukan")):
            e = r.choice(self.active)
            hrd.Aid.objects.create(employee=e, kind=k, event_date=today - timedelta(days=r.randint(3, 50)), amount=r.choice([500_000, 750_000, 1_000_000, 1_500_000]),
                                   description="Data demo", status=st, created_by=self.hrd_u, decided_by=self.hrd_u if st != "diajukan" else None,
                                   decided_at=REAL_NOW() if st != "diajukan" else None, paid_at=today - timedelta(days=2) if st == "dibayar" else None,
                                   decision_note="Tidak memenuhi syarat (demo)" if st == "ditolak" else "")
        women = [e for e in self.active if e.gender == "P"]
        if women:
            e = r.choice(women); due = today + timedelta(days=40)
            hrd.MaternityLeave.objects.create(employee=e, due_date=due, start_date=due - timedelta(days=45), end_date=due + timedelta(days=45), note="Data demo", created_by=self.hrd_u)
        pr = [hrd.Project.objects.create(code="PRJ-2026-01", name="Perluasan Gudang B", location="Area Timur", start_date=today - timedelta(days=60), created_by=self.hrd_u),
              hrd.Project.objects.create(code="PRJ-2026-02", name="Renovasi Kantin", location="Gedung Utama", start_date=today - timedelta(days=20), created_by=self.hrd_u)]
        for p in pr:
            for k in range(4):
                w = r.sample(self.active, 5); lg = hrd.ProjectDailyLog.objects.create(project=p, work_date=today - timedelta(days=k + 1), activity=r.choice(["Pengecoran lantai", "Pemasangan rangka atap", "Pengecatan dinding", "Pemasangan keramik"]), headcount=5, created_by=self.hrd_u)
                lg.workers.set(w)
        for k in range(10):
            d = today - timedelta(days=k)
            hrd.CateringOrder.objects.create(date=d, meal=r.choice(["siang", "malam"]), qty_large=r.randint(40, 90), qty_small=r.randint(10, 30), price_large=18000, price_small=12000,
                                             vendor="Katering Bu Sari", status="diterima" if k else "dipesan", received_large=None, created_by=self.hrd_u)
        self.stat += ", operasional HRD"

    # ---------- poliklinik
    def poli(self):
        r, today = self.r, self.today
        dg = [pl.Diagnosis.objects.create(code=c, name=n, category="Umum") for c, n in DIAG]
        meds = []
        for code, name, unit, stock, mn in MEDS:
            m = pl.Medicine.objects.create(code=code, name=name, unit=unit, stock=0, min_stock=mn); ps._move(m.pk, stock, self.poli_u, "purchase", note="Stok awal (demo)"); meds.append(m)
        ps._move(meds[7].pk, -(meds[7].stock - 6), self.poli_u, "adjustment", note="Penyesuaian demo: stok menipis")  # satu obat di bawah minimum → muncul peringatan
        pool = [e for e in self.active if e.department.code != "POL"]; recs = []
        for back in range(180):  # 6 bulan; kunjungan harian meningkat menuju hari ini
            d = today - timedelta(days=back)
            if d.weekday() == 6: continue
            k = r.randint(1, 4) if back > 7 else r.randint(2, 6) + (3 if back == 0 else 0)
            for _ in range(k):
                kind = r.choices(["berobat", "pemeriksaan", "kecelakaan_kerja", "kehamilan"], [80, 12, 5, 3])[0]
                e = r.choice([x for x in pool if x.gender == "P"] if kind == "kehamilan" and any(x.gender == "P" for x in pool) else pool)
                diag = r.choice(dg) if kind != "pemeriksaan" else dg[-1]
                rec = pl.MedicalRecord.objects.create(employee=e, kind=kind, visit_at=self.at(d, r.randint(7, 16)), complaint="Pemeriksaan kehamilan rutin" if kind == "kehamilan" else r.choice(COMPLAINTS),
                                                     exam={"tensi": f"{r.randint(100, 140)}/{r.randint(65, 90)}", "suhu": round(r.uniform(36, 38.5), 1)}, diagnosis=diag,
                                                     treatment="Istirahat, obat sesuai resep", created_by=self.poli_u)
                recs.append(rec)
                if kind == "berobat" and r.random() < .8:
                    m = r.choice(meds[:7] + meds[9:]); q = r.randint(3, 12)
                    try: ps._move(m.pk, -q, self.poli_u, "prescription", ref=str(rec.pk)); pl.Prescription.objects.create(record=rec, medicine=m, qty=q, dosage="3 x 1 setelah makan")
                    except ValueError: pass
        for rec, st in zip(r.sample(recs[:40], 4), ("diajukan", "dirujuk", "selesai", "batal")):
            ref = ps.create_referral(self.poli_u, rec, r.choice(["RSUD Kardinah Tegal", "RS Mitra Keluarga Tegal", "RS Islam Harapan Anda"]), "Rujukan demo")
            if st in ("dirujuk", "selesai"): ps.referral_transition(ref.pk, "dirujuk", self.poli_u)
            if st == "selesai": ps.referral_transition(ref.pk, "selesai", self.poli_u, "Pasien sudah ditangani, kontrol 1 minggu (demo)")
            if st == "batal": ps.referral_transition(ref.pk, "batal", self.poli_u, "Pasien membaik, rujukan tidak diperlukan (demo)")
        self.stat += f", {len(recs)} kunjungan poli, {len(meds)} obat"

    # ---------- audit contoh
    def audit(self):
        for u, mod, act, ot in ((self.hrd_u, "hr", "create", "Employee"), (self.hrd_u, "hr", "update", "Employee"), (self.hrd_u, "request", "approve", "ChangeRequest"),
                                (self.poli_u, "poli", "create", "MedicalRecord"), (self.root, "core", "create", "User"), (self.hrd_u, "hrd", "update", "BpjsMembership")):
            AuditLog.objects.create(user=u, ip="127.0.0.1", module=mod, action=act, object_type=ot, object_id=str(self.r.randint(1, 50)), after={"demo": True})
