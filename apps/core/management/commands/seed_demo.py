"""Data demo untuk uji coba UI (putaran 16). JANGAN dipakai di produksi.
    python manage.py seed_demo                 # 80 karyawan, sandi demo dicetak di akhir
    python manage.py seed_demo --employees 200 --password 'Sandi-Demo-123'
Aman diulang: bila karyawan demo (NIK berawalan DM) sudah ada, perintah berhenti (pakai --force untuk menambah lagi tidak didukung; kosongkan DB demo)."""
import random
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from apps.core.models import AuditLog, Notification, Role, User
from apps.hr import attendance_check as ac, models as hr, overtime, schedule, rotation_table as rt, services as hr_services
from apps.hr.management.commands import remind_attendance_checks, remind_extra_work
from apps.hrd import forms as hrd_forms, models as hrd, services as hrd_services
from apps.poli import models as pl, services as ps
from apps.poli.forms import EXAM_CONCLUSIONS, INJURY_TYPES

FIRST = ["Budi", "Siti", "Andi", "Rina", "Dewi", "Agus", "Wulan", "Rizky", "Fitri", "Joko", "Sri", "Dimas", "Putri", "Eko", "Ayu", "Hendra", "Lestari", "Bayu", "Nur", "Yusuf", "Maya", "Rudi", "Intan", "Fajar", "Tri", "Ahmad", "Dian", "Slamet", "Ratna", "Doni"]
LAST = ["Santoso", "Rahmawati", "Pratama", "Amelia", "Lestari", "Wijaya", "Kusuma", "Hidayat", "Saputra", "Utami", "Nugroho", "Handayani", "Setiawan", "Permana", "Sari", "Firmansyah", "Susanto", "Maharani", "Wibowo", "Purnama"]
DEPTS = [("PRD", "Produksi", 0.34), ("GDG", "Gudang", 0.14), ("QC", "Quality Control", 0.12), ("PKG", "Packaging", 0.14), ("MTC", "Maintenance", 0.09),
         ("HRD", "HRD", 0.04), ("FIN", "Finance", 0.05), ("POL", "Poliklinik", 0.03), ("GA", "General Affair", 0.05)]
POS = [("Staf", 1), ("Operator", 1), ("Teknisi", 2), ("Leader", 3), ("Supervisor", 4), ("Manager", 5), ("Perawat", 2), ("Bidan", 2)]
POLI_STAFF = [("Perawat", "L"), ("Bidan", "P"), ("Perawat", "P"), ("Perawat", "L")]  # karyawan departemen Poli (dipakai sebagai "Pemeriksa" di rekam medis); dokter BUKAN karyawan
DOCTORS = ["dr. Anisa Rahayu", "dr. Bambang Wicaksono, Sp.PD"]
DIAG = [("J00", "Nasofaringitis akut (common cold)"), ("J06.9", "Infeksi saluran napas atas akut"), ("A09", "Diare dan gastroenteritis"), ("K29.7", "Gastritis"),
        ("R51", "Sakit kepala"), ("M54.5", "Nyeri punggung bawah"), ("I10", "Hipertensi esensial"), ("L30.9", "Dermatitis"), ("S61.9", "Luka terbuka pergelangan/tangan"), ("Z00.0", "Pemeriksaan kesehatan umum")]
MEDS = [("OB001", "Paracetamol 500mg", "tablet", 600, 100), ("OB002", "Amoxicillin 500mg", "kapsul", 300, 60), ("OB003", "Antasida DOEN", "tablet", 400, 80), ("OB004", "CTM 4mg", "tablet", 500, 80),
        ("OB005", "Ibuprofen 400mg", "tablet", 350, 60), ("OB006", "Oralit", "sachet", 200, 40), ("OB007", "Vitamin C 500mg", "tablet", 500, 80), ("OB008", "Betadine 30ml", "botol", 40, 10),
        ("OB009", "Kasa steril", "pak", 120, 30), ("OB010", "Amlodipine 5mg", "tablet", 200, 40), ("OB011", "Salep hidrokortison", "tube", 25, 15), ("OB012", "Ambroxol 30mg", "tablet", 250, 50)]
COMPLAINTS = ["Keluhan flu dan demam", "Sakit kepala sejak pagi", "Nyeri lambung setelah makan", "Diare sejak semalam", "Nyeri punggung setelah angkat barang", "Batuk pilek 3 hari", "Pusing dan lemas", "Tensi tinggi, kontrol rutin", "Gatal-gatal di tangan"]
REAL_NOW = timezone.now
# Master shift (Aturan Pengaturan Jadwal Shift 2026): kode, jam, melewati tengah malam, GS?  Jam GS-12/14/16 = DATA CONTOH (jam pulang 12/14/16) — samakan dengan aturan resmi di /master/shift/.
SHIFTS = [("PAGI", "Shift Pagi", time(6), time(14), False, False), ("SIANG", "Shift Siang", time(14), time(22), False, False), ("MALAM", "Shift Malam", time(22), time(6), True, False),
          ("GS-12", "General Shift 12", time(8), time(12), False, True), ("GS-14", "General Shift 14", time(8), time(14), False, True), ("GS-16", "General Shift 16", time(8), time(16), False, True)]
GROUP_LETTERS = "ABCDEFG"
ROTATING_DEPTS = {"PRD": hr.ShiftGroup.P2, "GDG": hr.ShiftGroup.P2, "MTC": hr.ShiftGroup.P2, "PKG": hr.ShiftGroup.P3}  # dept lain = general shift (GS)
REASONS = ["Keperluan keluarga", "Urusan administrasi", "Kontrol dokter", "Acara keluarga", "Tukar giliran dengan rekan"]
EXTRA_REASONS = {"lembur": ["Mengejar target pengiriman minggu ini", "Penyelesaian order mendesak pelanggan", "Perbaikan mesin di luar jam shift"],
                 "standby": ["Mesin tidak boleh berhenti saat istirahat", "Menunggu hasil uji kualitas lot terakhir", "Penjagaan area saat pergantian shift"]}
INCIDENTS = [("Area mesin press", "Tangan terjepit saat mengganti cetakan; mesin sudah dimatikan.", "Tangan kanan", "luka_ringan", 0),
             ("Gudang rak B", "Terpeleset saat membawa kardus di lantai licin.", "Pergelangan kaki", "terkilir", 2),
             ("Lini packaging", "Jari tergores pisau pemotong lakban.", "Jari tangan kiri", "luka_robek", 1),
             ("Area bongkar muat", "Tertimpa kardus dari tumpukan teratas.", "Bahu kiri", "memar", 1)]
DEPT_ADMINS = [("PRD", "Budi"), ("GDG", "Andi"), ("QC", "Rina"), ("PKG", "Siti"), ("MTC", "Joko"), ("FIN", "Dewi"), ("GA", "Agus"), ("HRD", "Eko"), ("POL", "Maya")]  # tiap departemen punya admin (A74)


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
        self.users(); self.master(); self.employees(n); self.requests(); self.history(); self.swaps(); self.leave(); self.info()
        self.extra_work(); self.attendance(); self.hrd_ops(); self.poli(); self.separations(); self.audit()
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
        made = [hr.Shift.objects.create(code=c, name=n, start=a, end=b, crosses_midnight=x, is_gs=g) for c, n, a, b, x, g in SHIFTS]
        self.shifts, self.gs = [x for x in made if not x.is_gs], [x for x in made if x.is_gs]  # shifts = PAGI/SIANG/MALAM (rotasi); gs = general shift
        self.group_cycle = {hr.ShiftGroup.P2: 0, hr.ShiftGroup.P3: 0}
        self.groups = {hr.ShiftGroup.P2: [hr.ShiftGroup.objects.create(code=rt.group_code(l, hr.ShiftGroup.P2), pattern=hr.ShiftGroup.P2) for l in GROUP_LETTERS],
                       hr.ShiftGroup.P3: [hr.ShiftGroup.objects.create(code=rt.group_code(l, hr.ShiftGroup.P3), pattern=hr.ShiftGroup.P3) for l in GROUP_LETTERS]}
        self.rotation()
        for c, nm in DEPT_ADMINS:  # satu Admin Departemen per departemen → Validasi Kehadiran bisa menanyakan karyawan mana pun, tanpa banner "belum ada admin"
            self.mk(f"admin_{c.lower()}", nm, f"Admin {self.depts[c].name}", Role.DEPT_ADMIN, self.depts[c])

    def rotation(self):
        """Tabel rotasi RESMI dari docs/jadwal_shift_2026.md (apps/hr/rotation_table.py): 2 shift = A–G, 3 shift/PACK = A_pack–G_pack."""
        by_code = {x.code: x for x in self.shifts}
        for pattern, groups in self.groups.items():
            cells = rt.official(pattern)
            for grp in groups:
                for w, sc in enumerate(cells[grp.code]): hr.ShiftRotation.objects.create(group=grp, weekday=w, shift=by_code[sc] if sc else None)

    def shift_for(self, dept_code):
        """(shift tetap/GS, kelompok rotasi) untuk karyawan baru: dept rotasi → kelompok bergilir A..G (shift kosong), lainnya → general shift."""
        pattern = ROTATING_DEPTS.get(dept_code)
        if not pattern: return next(x for x in self.gs if x.code == "GS-16"), None  # semua GS: 08–16; GS-14/GS-12 hanya sebelum libur GS (Employee.gs_short)
        i = self.group_cycle[pattern]; self.group_cycle[pattern] += 1
        return None, self.groups[pattern][i % len(GROUP_LETTERS)]

    # ---------- karyawan + kontrak + BPJS
    def employees(self, n):
        r, today = self.r, self.today
        codes = [c for c, _, _ in DEPTS]; weights = [w for _, _, w in DEPTS]
        self.emps, used = [], set()
        for i in range(1, n + 1):
            staff = POLI_STAFF[i - 1] if i <= len(POLI_STAFF) else None  # empat karyawan pertama = staf Poli (selalu aktif)
            c = "POL" if staff else r.choices(codes, weights)[0]; d = self.depts[c]
            g = staff[1] if staff else r.choice("LP"); name = f"{r.choice(FIRST)} {r.choice(LAST)}"
            while name in used: name = f"{r.choice(FIRST)} {r.choice(LAST)} {r.choice(LAST)}"
            used.add(name)
            tenure = r.randint(30, 3200); join = today - timedelta(days=tenure)
            lvl = staff[0] if staff else "Operator" if c in ("PRD", "PKG", "GDG") and r.random() < .7 else r.choice(["Staf", "Teknisi", "Leader", "Supervisor"] if tenure > 900 else ["Staf", "Operator", "Teknisi"])
            shift, group = self.shift_for(c)
            e = hr.Employee.objects.create(
                nik=f"DM{i:05d}", nik_ktp="33" + "".join(str(r.randint(0, 9)) for _ in range(14)), name=name, gender=g,
                marital_status=r.choice(["Belum menikah", "Menikah", "Menikah"]), education=r.choice(["SMA", "SMK", "D3", "S1"]),
                address=f"Jl. Contoh No. {r.randint(1, 99)}, Tegal", phone="08" + "".join(str(r.randint(0, 9)) for _ in range(10)), department=d, position=self.pos[lvl],
                status="nonaktif" if (r.random() < .04 and not staff) else "aktif", join_date=join, shift=shift, shift_group=group, gs_short=r.choice(["14", "12"]),
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
                 ("cuti", "executed", 8), ("izin_terlambat", "executed", 4), ("cuti", "draft", 2), ("izin_pulang", "cancelled", 1), ("mutasi_jabatan", "approved", 1),
                 # jenis yang sebelumnya belum punya data demo (semua 15 jenis pengajuan umum kini terwakili; tukar_shift/tukar_libur di swaps(), standby/lembur di extra_work())
                 ("promosi", "pending", 2), ("demosi", "pending", 1), ("rotasi", "pending", 1), ("izin_khusus", "approved", 2), ("izin_khusus", "pending", 1), ("status", "pending", 1)]
        self.reqs = []
        for typ, st, cnt in specs:
            for _ in range(cnt):
                e = r.choice([x for x in self.active if x.shift_id] if typ == "shift" else self.active); who = self.hrd_u if typ == "status" else admins.get(e.department_id, self.hrd_u)  # perubahan shift tetap hanya untuk non-rotasi (GS); perubahan status hanya HRD
                s = today + timedelta(days=r.randint(2, 40)) if st in ("pending", "approved", "draft", "submitted") else today - timedelta(days=r.randint(5, 120))
                days = r.randint(1, 4); p = {"reason": r.choice(["Keperluan keluarga", "Acara pernikahan saudara", "Pulang kampung", "Kontrol dokter", "Urusan administrasi"])}
                if typ == "cuti": p.update(start_date=s.isoformat(), end_date=(s + timedelta(days=days - 1)).isoformat())
                elif typ in ("mutasi_dept", "rotasi"):
                    dest = r.choice([d for d in self.depts.values() if d.pk != e.department_id]); p.update(department_id=dest.pk, department_name=dest.name, effective_date=s.isoformat())
                elif typ in ("mutasi_jabatan", "promosi", "demosi"):
                    ps_ = self.pos[{"promosi": "Supervisor", "demosi": "Operator"}.get(typ, "Leader")]; p.update(position_id=ps_.pk, position_name=ps_.name, effective_date=s.isoformat())
                elif typ == "status": p.update(status="nonaktif", effective_date=s.isoformat())  # hanya menunggu keputusan; tidak dilaksanakan di data demo
                elif typ == "shift":
                    sh = r.choice([x for x in self.gs if x.pk != e.shift_id]); p.update(shift_id=sh.pk, shift_name=sh.name, effective_date=s.isoformat())
                elif typ in ("izin", "sakit", "izin_khusus"): p.update(start_date=s.isoformat(), end_date=(s + timedelta(days=days - 1)).isoformat())
                else: p.update(date=s.isoformat(), time="10:30")
                q = hr.ChangeRequest.objects.create(type=typ, employee=e, department=e.department, status=st, payload=p, requested_by=who,
                                                    decided_by=self.hrd_u if st in ("approved", "rejected", "executed") else None,
                                                    note="Ditolak: kuota departemen penuh" if st == "rejected" else "")
                hr.ChangeRequest.objects.filter(pk=q.pk).update(created_at=self.at(today if st == "pending" else today - timedelta(days=r.randint(1, 60))))
                self.reqs.append(q)
        self.stat += f", {len(self.reqs)} pengajuan"


    # ---------- riwayat karyawan: perubahan jabatan yang BENAR-BENAR dilaksanakan lewat alur resmi (EmployeeHistory terisi, bukan baris tulis tangan)
    def history(self):
        r, today = self.r, self.today
        admins = {u.department_id: u for u in User.objects.filter(role=Role.DEPT_ADMIN)}
        cand = [e for e in self.active if e.position and e.position.name in ("Operator", "Staf", "Teknisi")]
        n = 0
        for typ, target in (("promosi", "Leader"), ("promosi", "Supervisor"), ("mutasi_jabatan", "Teknisi")):
            if not cand: break
            e = cand.pop(r.randrange(len(cand))); pos = self.pos[target]
            if e.position_id == pos.pk: continue
            who = admins.get(e.department_id, self.hrd_u)
            p = {"position_id": pos.pk, "position_name": pos.name, "effective_date": (today - timedelta(days=r.randint(3, 40))).isoformat(), "reason": "Penilaian kinerja (demo)"}
            q = hr.ChangeRequest.objects.create(type=typ, employee=e, department=e.department, payload=p, requested_by=who)
            q = hr_services.submit(q, who, notify=False)
            q = hr_services.transition(q, "approved", self.hrd_u, notify=False); q = hr_services.transition(q, "executed", self.hrd_u, notify=False)
            self.reqs.append(q); n += 1
        self.stat += f", {n} perubahan jabatan dilaksanakan (riwayat karyawan)"

    # ---------- Stand By & Lembur (putaran 30–32): satu kiriman banyak karyawan = satu pengajuan per karyawan yang berbagi `batch`
    def extra_work(self):
        """Lewat ChangeRequest + services yang sama dengan form (submit → keputusan HRD → otomatis 'Dilaksanakan'); karyawan yang bentrok dengan izin/cuti/sakit
        atau jam lain dilewati memakai overtime.conflict (aturan yang sama dengan form). Pengingat harian dijalankan sekali di akhir, seperti cron."""
        r, today = self.r, self.today
        admins = {u.department_id: u for u in User.objects.filter(role=Role.DEPT_ADMIN)}
        # (departemen, jenis, selisih hari dari hari ini, jam mulai, menit, jumlah orang, keputusan)
        plans = [("PRD", "lembur", -3, time(17, 0), 150, 8, "approved"), ("PKG", "standby", -1, time(12, 0), 45, 6, "approved"), ("MTC", "lembur", -5, time(7, 0), 120, 4, "rejected"),
                 ("QC", "lembur", -2, time(16, 30), 90, 3, "pending"), ("GDG", "lembur", 1, time(17, 0), 120, 5, "pending"), ("PRD", "standby", 2, time(12, 0), 60, 6, "pending")]
        hrd_users, batches, people_n, cancelled = list(User.objects.filter(role=Role.HRD, is_active=True)), 0, 0, 0
        for code, kind, off, start, mins, k, decision in plans:
            dept = self.depts[code]; admin = admins.get(dept.pk); d = today + timedelta(days=off)
            pool = [e for e in self.active if e.department_id == dept.pk]
            people = [e for e in r.sample(pool, len(pool)) if not overtime.conflict(e, kind, d, start, mins)][:k]
            if not people or admin is None: continue
            batch = uuid.uuid4().hex
            payload = {"date": d.isoformat(), "time": start.strftime("%H:%M"), "time_to": (datetime.combine(d, start) + timedelta(minutes=mins)).strftime("%H:%M"),
                       "minutes": mins, "reason": r.choice(EXTRA_REASONS[kind]), "batch": batch}
            rows = []
            for e in people:
                q = hr.ChangeRequest.objects.create(type=kind, employee=e, department=e.department, payload=payload, requested_by=admin)
                rows.append(hr_services.submit(q, admin, notify=False))
            Notification.objects.bulk_create([Notification(user=u, kind="request", link=f"/requests/g/lembur/?batch={batch}", is_read=decision != "pending",
                title=f"{len(rows)} pengajuan {kind} {payload['date']} dari {dept.name} menunggu keputusan") for u in hrd_users])
            if decision != "pending":
                note = "Target produksi bulan ini belum perlu lembur tambahan (demo)" if decision == "rejected" else ""
                rows = [hr_services.transition(q, decision, self.hrd_u, note, notify=False) for q in rows]
                word = "disetujui" if decision == "approved" else "ditolak"
                Notification.objects.create(user=admin, kind="approval", link=f"/requests/g/lembur/?batch={batch}",
                                            title=f"Pengajuan {kind} {payload['date']} ({len(rows)} orang) {word} HRD" + (f": {note[:80]}" if note else ""))
            if decision == "approved" and kind == "lembur" and len(rows) > 1:  # A80: HRD membatalkan satu yang sudah final (alasan wajib) → bukti jalur pembatalan
                hr_services.transition(rows[-1], "cancelled", self.hrd_u, "Karyawan sakit mendadak, tidak jadi lembur (demo)"); cancelled += 1
            batches += 1; people_n += len(rows)
        remind_extra_work.run(today)  # pengingat yang sama dengan `manage.py remind_extra_work` (pengajuan menunggu yang tanggalnya besok/lewat)
        self.stat += f", lembur/stand by {batches} kiriman ({people_n} orang, {cancelled} dibatalkan)"

    # ---------- tukar shift/libur: 1 orang (sendiri) dan 2 orang (dengan rekan), konsisten dengan jadwal efektif
    def swaps(self):
        """Pengajuan tukar demo yang dihitung dari jadwal efektif (schedule.build_rows) — jadi aturan yang sama dengan form/pelaksanaan. Yang berstatus
        'executed' benar-benar menulis ShiftAssignment untuk kedua karyawan. Bila kombinasi tidak ditemukan (data kecil) pengajuan itu dilewati."""
        r, today = self.r, self.today
        admins = {u.department_id: u for u in User.objects.filter(role=Role.DEPT_ADMIN)}
        used, cals, made = set(), {}, {"1 orang": 0, "2 orang": 0}
        pool = [e for e in self.active if e.department.code != "POL"]
        by_dept = {}
        for e in pool: by_dept.setdefault(e.department_id, []).append(e)

        def cal(e):
            if e.pk not in cals: cals[e.pk] = {x["date"]: x for x in schedule.schedule_range(e, today + timedelta(days=2), 28)}
            return cals[e.pk]

        def days(e, off): return [d for d, x in cal(e).items() if x["off"] == off and (e.pk, d) not in used]

        def mk(typ, st, e, partner, rows, payload):
            who = admins.get(e.department_id, self.hrd_u)
            p = {**payload, "reason": r.choice(REASONS)}
            if partner: p.update(partner_id=partner.pk, partner_nik=partner.nik, partner_name=partner.name)
            q = hr.ChangeRequest.objects.create(type=typ, employee=e, department=e.department, status=st, payload=p, requested_by=who,
                                                decided_by=self.hrd_u if st in ("approved", "rejected", "executed") else None,
                                                note="Ditolak: jadwal produksi padat" if st == "rejected" else "")
            hr.ChangeRequest.objects.filter(pk=q.pk).update(created_at=self.at(today if st == "pending" else today - timedelta(days=r.randint(1, 10))))
            for emp, d, kind, sh in rows:
                used.add((emp.pk, d))
                if st == "executed": hr.ShiftAssignment.objects.create(employee=emp, date=d, kind=kind, shift=sh, request=q, created_by=self.hrd_u)
            cals.pop(e.pk, None); cals.pop(partner.pk, None) if partner else None  # jadwal efektif berubah → hitung ulang
            self.reqs.append(q); made["2 orang" if partner else "1 orang"] += 1

        def pairs():
            for lst in by_dept.values():
                for i, a in enumerate(lst):
                    for b in lst[i + 1:]: yield a, b

        def solo_shift(st):
            for e in r.sample(pool, len(pool)):
                for d in r.sample(days(e, False), len(days(e, False))):
                    cur = cal(e)[d]["shift"]
                    opts = [x for x in self.shifts if cur and x.pk != cur.pk and not (x.crosses_midnight and e.shift_group and e.shift_group.pattern == hr.ShiftGroup.P2)] if cur else []
                    if not opts: continue
                    sh = r.choice(opts); pl = {"date": d.isoformat(), "shift_id": sh.pk, "shift_name": sh.name}
                    return mk("tukar_shift", st, e, None, schedule.build_rows("tukar_shift", e, None, pl), pl)

        def solo_libur(st):
            for e in r.sample(pool, len(pool)):
                offs, works = days(e, True), days(e, False)
                if offs and works:
                    d1, d2 = r.choice(offs), r.choice(works); pl = {"date": d1.isoformat(), "date_to": d2.isoformat()}
                    return mk("tukar_libur", st, e, None, schedule.build_rows("tukar_libur", e, None, pl), pl)

        def duo_shift(st):
            for a, b in r.sample(list(pairs()), len(list(pairs()))):
                ok = [d for d in days(a, False) if d in set(days(b, False)) and cal(a)[d]["shift"] and cal(b)[d]["shift"] and cal(a)[d]["shift"].pk != cal(b)[d]["shift"].pk]
                if ok:
                    pl = {"date": r.choice(ok).isoformat()}
                    return mk("tukar_shift", st, a, b, schedule.build_rows("tukar_shift", a, b, pl), pl)

        def duo_libur(st):
            for a, b in r.sample(list(pairs()), len(list(pairs()))):
                d1s = [d for d in days(a, True) if d in set(days(b, False))]; d2s = [d for d in days(b, True) if d in set(days(a, False))]
                if d1s and d2s:
                    pl = {"date": r.choice(d1s).isoformat(), "date_to": r.choice(d2s).isoformat()}
                    return mk("tukar_libur", st, a, b, schedule.build_rows("tukar_libur", a, b, pl), pl)

        for fn, st in ((duo_shift, "executed"), (duo_shift, "approved"), (duo_shift, "pending"), (duo_shift, "pending"), (duo_shift, "rejected"), (duo_shift, "draft"),
                       (duo_libur, "executed"), (duo_libur, "approved"), (duo_libur, "pending"), (duo_libur, "pending"), (duo_libur, "cancelled"),
                       (solo_libur, "executed"), (solo_libur, "approved"), (solo_libur, "pending"), (solo_libur, "pending"),
                       (solo_shift, "executed"), (solo_shift, "pending"), (solo_shift, "pending")): fn(st)
        self.stat += f", tukar jadwal {made['1 orang']} (1 orang) + {made['2 orang']} (2 orang)"

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

    # ---------- Validasi kehadiran (putaran 26–29): HRD → Admin Departemen → verifikasi HRD, lewat servis yang sama dengan UI (jejak & notifikasi ikut)
    def attendance(self):
        """Skenario yang mewakili tiap status: diminta (dua di antaranya terlambat), dijawab (menunggu HRD), dikembalikan, diverifikasi (terkunci; ada yang diubah HRD
        dan satu dikoreksi), dibatalkan, serta satu jawaban Alfa yang bertabrakan dengan cuti yang sudah dilaksanakan (ditandai 'Konflik'). Tiap skenario dilewati
        bila departemennya terlalu kecil (--employees kecil)."""
        r, today = self.r, self.today
        admins = {u.department_id: u for u in User.objects.filter(role=Role.DEPT_ADMIN)}

        def wd(n):  # n hari kerja ke belakang (Minggu dilewati)
            d = today
            while n > 0:
                d -= timedelta(days=1)
                if d.weekday() != 6: n -= 1
            return d

        def ask(code, k, day, note=""):
            pool = [e for e in self.active if e.department.code == code and not hr.AttendanceCheck.objects.filter(employee=e).exclude(status="dibatalkan").exists()]
            emps = r.sample(pool, min(k, len(pool)))
            return ac.create_checks(self.hrd_u, emps, [day], note) if emps else []

        def reply(check, value, note=""): return ac.answer(admins[check.department_id], check.pk, value, note)

        made = 0
        c = ask("PRD", 6, wd(1), "Mohon konfirmasi kehadiran regu kemarin (demo)"); made += len(c)           # baru diminta
        hr.AttendanceCheck.objects.filter(pk__in=[x.pk for x in c[:2]]).update(due_date=today - timedelta(days=1))  # dua di antaranya sudah lewat batas → "terlambat"
        c = ask("GDG", 5, wd(3)); made += len(c)                                                                 # dijawab → sebagian diverifikasi, satu dikembalikan, satu diubah HRD
        plan = [("hadir", ""), ("hadir", ""), ("hadir", ""), ("izin", "Izin keperluan keluarga (demo)"), ("alfa", "Tidak ada kabar sampai akhir shift (demo)")]
        for x, (v, note) in zip(c, plan): reply(x, v, note)
        for x in c[:2]: ac.verify(self.hrd_u, x.pk, "terima")                                                    # c[2] sengaja dibiarkan "dijawab" (menunggu HRD)
        if len(c) > 3: ac.verify(self.hrd_u, c[3].pk, "kembalikan", "Mohon lampirkan bukti izin (demo)")
        if len(c) > 4: ac.verify(self.hrd_u, c[4].pk, "ubah", "Ternyata sakit, surat dokter menyusul (demo)", final="sakit")
        c = ask("QC", 4, wd(5)); made += len(c)                                                                  # terverifikasi lengkap + satu koreksi
        for i, x in enumerate(c):
            reply(x, "sakit" if i == 1 else "hadir"); ac.verify(self.hrd_u, x.pk, "terima")
        if c: ac.correct(self.hrd_u, c[-1].pk, "izin", "Koreksi: ternyata izin resmi, bukan hadir (demo)")
        c = ask("PKG", 2, wd(2), "Konfirmasi lembur regu malam (demo)"); made += len(c)                         # satu dibatalkan, satu tetap diminta
        if len(c) > 1: ac.cancel(self.hrd_u, c[0].pk, "Salah tanggal, akan dibuat ulang (demo)")
        for q in self.reqs:                                                                                      # Alfa vs cuti yang sudah dilaksanakan → ditandai "Konflik"
            if q.type == "cuti" and q.status == "executed" and q.employee.status == "aktif" and q.employee.department_id in admins:
                d = date.fromisoformat(q.payload["start_date"])
                if d <= today and not hr.AttendanceCheck.objects.filter(employee=q.employee, date=d).exclude(status="dibatalkan").exists():
                    x = ac.create_checks(self.hrd_u, [q.employee], [d], "Konfirmasi ketidakhadiran (demo)")[0]; reply(x, "alfa", "Tidak masuk tanpa keterangan (demo)"); made += 1
                    break
        remind_attendance_checks.run(today)  # pengingat yang sama dengan `manage.py remind_attendance_checks`
        self.stat += f", {made} validasi kehadiran"

    # ---------- operasional HRD
    def hrd_ops(self):
        r, today = self.r, self.today
        for k in ("kematian", "pernikahan", "kelahiran", "musibah", "pernikahan"):
            e = r.choice(self.active)
            hrd.Aid.objects.create(employee=e, kind=k, event_date=today - timedelta(days=r.randint(3, 50)), amount=r.choice([500_000, 750_000, 1_000_000, 1_500_000]),
                                   description="Data demo", created_by=self.hrd_u)
        # cuti hamil: tiga karyawati hamil (34/26/14 minggu). HPHT disimpan di self.pregnant → poli() membuat kunjungan kehamilan yang konsisten dengan HPL di sini.
        women = [e for e in self.active if e.gender == "P" and e.department.code != "POL"]
        self.pregnant = [(e, today - timedelta(days=wk * 7 + r.randint(0, 3))) for e, wk in zip(r.sample(women, min(3, len(women))), (34, 26, 14))]
        if self.pregnant:  # yang paling lanjut: cuti hamil sedang berjalan (HPL ±40 hari lagi)
            e, hpht = self.pregnant[0]; due = hpht + timedelta(days=280); s, en = hrd_services.default_maternity_window(due)
            hrd.MaternityLeave.objects.create(employee=e, due_date=due, start_date=s, end_date=en, note="Data demo", created_by=self.hrd_u)
        others = [e for e in women if e.pk not in {p[0].pk for p in self.pregnant}]
        if others:  # selesai (sudah melahirkan)
            due = today - timedelta(days=100); s, en = hrd_services.default_maternity_window(due)
            m = hrd.MaternityLeave.objects.create(employee=others[0], due_date=due, start_date=s, end_date=en, note="Data demo", created_by=self.hrd_u)
            hrd_services.maternity_finish(m.pk, due + timedelta(days=2))
        if len(others) > 1:  # dibatalkan (alasan wajib)
            due = today + timedelta(days=120); s, en = hrd_services.default_maternity_window(due)
            m = hrd.MaternityLeave.objects.create(employee=others[1], due_date=due, start_date=s, end_date=en, note="Data demo", created_by=self.hrd_u)
            hrd_services.maternity_cancel(m.pk, "Dicatat pada karyawan yang salah, dibuat ulang (demo)")
        pr = [hrd.Project.objects.create(code="PRJ-2026-01", name="Perluasan Gudang B", location="Area Timur", start_date=today - timedelta(days=60), created_by=self.hrd_u),
              hrd.Project.objects.create(code="PRJ-2026-02", name="Renovasi Kantin", location="Gedung Utama", start_date=today - timedelta(days=20), created_by=self.hrd_u)]
        names = ["Sutrisno", "Wahyu", "Ngatiman", "Slamet", "Parman", "Darto"]
        for p in pr:
            for k in range(4):
                for nm in r.sample(names, 3):
                    hrd.ProjectWork.objects.create(project=p, work_date=today - timedelta(days=k + 1), worker_name=nm, wage=r.choice([100_000, 120_000, 150_000]), created_by=self.hrd_u,
                                                   activity=r.choice(["Pengecoran lantai", "Pemasangan rangka atap", "Pengecatan dinding", "Pemasangan keramik"]))
        for k in range(10):
            d = today - timedelta(days=k)
            for meal in ("0900", "1200", "1800", "0200")[:2 + k % 3]:
                ql, qs = r.randint(40, 90), r.randint(10, 30)
                hrd.CateringOrder.objects.create(date=d, meal=meal, qty_large=ql, qty_small=qs, received_large=ql if k else None, received_small=qs if k else None, created_by=self.hrd_u)
        for lvl, nik_i in ((1, 0), (2, 1)):
            e = self.active[nik_i]
            w = hrd_services.issue_warning(e, lvl, today - timedelta(days=30 * lvl), hrd_services.add_months(today - timedelta(days=30 * lvl), 6), "Terlambat berulang (demo)", "Data demo", self.hrd_u)
        w = hrd_services.issue_warning(self.active[2], 1, today - timedelta(days=90), hrd_services.add_months(today - timedelta(days=90), 6), "Tidak memakai APD (demo)", "Data demo", self.hrd_u)
        hrd_services.revoke_warning(w.pk, "Diterbitkan keliru: pelanggaran dilakukan karyawan lain (demo)", self.hrd_u)  # SP yang dicabut (alasan wajib) tetap tampil di riwayat
        # potongan BPJS (putaran 22/23): hanya anggota berstatus aktif. Bulan lalu lengkap; bulan ini sebagian karyawan sengaja dilewati agar anomali "belum dipotong" terlihat
        period, prev = today.strftime("%Y-%m"), (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        for per, members in ((prev, self.active), (period, self.active[: max(5, len(self.active) * 2 // 3)])):
            for m in hrd.BpjsMembership.objects.filter(status="aktif", employee__in=members).select_related("employee"):
                if per == prev and m.employee.join_date.strftime("%Y-%m") > prev: continue  # belum bekerja pada bulan itu
                base = Decimal(r.choice([2_500_000, 3_000_000, 3_500_000]))
                hrd.BpjsDeduction.objects.get_or_create(employee=m.employee, scheme=m.scheme, period=per, defaults={
                    "employee_amount": (base * Decimal("0.01" if m.scheme == "kes" else "0.02")).quantize(Decimal(1)), "employer_amount": (base * Decimal("0.04" if m.scheme == "kes" else "0.037")).quantize(Decimal(1)),
                    "note": "Data demo", "created_by": self.hrd_u})
        # seragam (putaran 23 P5 + putaran 25 stok): master + tarif L/P dibuat migrasi hrd/0007. Urutan seperti pemakaian nyata: ambang minimum → barang masuk dari vendor →
        # pembelian karyawan LEWAT UniformPurchaseForm (aturan yang sama dengan input/impor; stok keluar otomatis ke kartu stok) → batal / koreksi / ditandai dipotong.
        sizes, utype = list(hrd.UniformSize.objects.filter(is_active=True)), hrd.UniformType.objects.filter(is_active=True).first()
        bought = []
        if sizes and utype:
            bysize = {z.code: z for z in sizes}
            for code, minimum in {"S": 15, "M": 25, "L": 25, "XL": 15, "XXL": 8}.items():   # 3XL sengaja tanpa ambang (tidak dipantau)
                if code in bysize: hrd_services.set_min_stock(utype.pk, bysize[code].pk, minimum)
            for code, qty, ago, doc in (("S", 40, 45, "SJ-0457"), ("M", 80, 45, "SJ-0457"), ("L", 80, 45, "SJ-0457"), ("XL", 20, 45, "SJ-0457"), ("XXL", 12, 45, "SJ-0457"), ("M", 40, 12, "SJ-0488")):
                if code in bysize: hrd_services.stock_move(utype.pk, bysize[code].pk, "masuk", qty, today - timedelta(days=ago), self.hrd_u, note=f"Vendor Seragam Jaya — {doc} (demo)")

            def buy(e, d, z, q):
                f = hrd_forms.UniformPurchaseForm({"nik": e.nik, "purchase_date": d.isoformat(), "utype": utype.pk, "size": z.pk, "quantity": q, "note": "Data demo"}, user=self.hrd_u)
                return f.save() if f.is_valid() else None

            for e in r.sample(self.active, min(14, len(self.active))):
                p = buy(e, today - timedelta(days=r.randint(0, 25)), r.choice(sizes), r.randint(1, 3))
                if p: bought.append(p)
            if "3XL" in bysize:  # 3XL tidak pernah dicatat masuk → saldo MINUS: contoh peringatan "stok belum dicatat masuk" (A63)
                big = next((e for e in self.active if e.gender == "L"), self.active[0]); p = buy(big, today, bysize["3XL"], 1)
                if p: bought.append(p)
            if "XL" in bysize:  # opname: koreksi beralasan menurunkan XL di bawah ambang → baris koreksi di kartu + notifikasi "stok menipis"
                st = hrd.UniformStock.objects.get(utype=utype, size=bysize["XL"])
                if st.balance != 11: hrd_services.stock_move(utype.pk, bysize["XL"].pk, "koreksi", 11 - st.balance, today - timedelta(days=2), self.hrd_u, note="Selisih stok opname: 5 rusak/hilang (demo)")
            old = [p for p in bought if p.purchase_date.strftime("%Y-%m") < period]
            for p in old[:3]: hrd_services.mark_uniform_deducted(p.pk, period, self.hrd_u)           # sudah dipotong dari gaji bulan ini (satu arah)
            fresh = next((p for p in reversed(bought) if p.deduction_status == "belum" and p.size.code != "3XL"), None)   # 3XL dipertahankan agar saldo minusnya tetap terlihat
            if fresh: hrd_services.void_uniform_purchase(fresh.pk, "Salah ukuran, akan dicatat ulang (demo)", self.hrd_u)   # batal beralasan → stok kembali (baris 'batal')
        self.stat += f", operasional HRD ({len(bought)} pembelian seragam)"

    # ---------- karyawan keluar (putaran 38c): lewat SeparationForm + servis yang sama dengan UI (riwayat status, nomor paklaring, kunci tali asih ikut).
    # Dijalankan SETELAH modul lain agar staf Poli tidak ikut nonaktif; karyawati hamil (cuti hamil/kunjungan kehamilan berjalan) dilewati.
    # Karyawan yang keluar SENGAJA masih punya keanggotaan/potongan BPJS aktif: halaman BPJS menandainya sebagai anomali "nonaktif tetapi BPJS masih aktif" (tugas nyata bagi HRD; A98).
    def separations(self):
        r, today = self.r, self.today
        pool = [e for e in self.active if e.department.code != "POL" and e.pk not in {x.pk for x, _ in self.pregnant} and e.join_date <= today - timedelta(days=120)]
        r.shuffle(pool); made = 0

        def keluar(kind, days, reason, tali=0, note="", paid=False, pak=False, void=""):
            if not pool: return 0
            e = pool.pop(); last = today + timedelta(days=days)
            f = hrd_forms.SeparationForm({"nik": e.nik, "kind": kind, "request_date": (last - timedelta(days=30)).isoformat() if kind == "resign" else "", "last_date": last.isoformat(),
                                          "reason": reason, "tali_asih": str(tali) if tali else "", "tali_asih_note": note}, user=self.hrd_u)
            if not f.is_valid(): return 0
            s = f.save()
            if paid and s.tali_asih: hrd_services.set_tali_asih(s.pk, s.tali_asih, s.tali_asih_note, last + timedelta(days=3), self.hrd_u)   # dibayar → nominal terkunci
            if pak and s.applied_at: hrd_services.issue_paklaring(s.pk, min(today, last + timedelta(days=5)), self.hrd_u)
            if void: hrd_services.void_separation(s.pk, void, self.hrd_u)                                                                      # batal beralasan → status karyawan dipulihkan
            return 1

        made += keluar("resign", -25, "Pindah kota (demo)", 1_500_000, "1 bulan gaji (demo)", paid=True, pak=True)    # lengkap: dibayar + paklaring
        made += keluar("habis_kontrak", -40, "Kontrak tidak diperpanjang (demo)", pak=True)                            # tanpa tali asih, paklaring terbit
        made += keluar("phk", -8, "Pelanggaran disiplin berat (demo)", 2_000_000, "Sesuai kebijakan perusahaan (demo)")  # tali asih belum dibayar, paklaring belum terbit → muncul sebagai tugas
        made += keluar("pensiun", 25, "Memasuki usia pensiun (demo)", 5_000_000, "Penghargaan masa kerja (demo)")     # terjadwal: masih aktif sampai hari keluar
        keluar("resign", -3, "Salah catat, karyawan batal keluar (demo)", void="Karyawan ternyata tidak jadi mengundurkan diri (demo)")  # contoh catatan batal; tidak dihitung
        self.stat += f", {made} karyawan keluar"

    # ---------- poliklinik
    def poli(self):
        r, today = self.r, self.today
        dg = [pl.Diagnosis.objects.update_or_create(code=c, defaults={"name": n, "category": "Umum"})[0] for c, n in DIAG]
        meds = []
        for code, name, unit, stock, mn in MEDS:
            m = pl.Medicine.objects.create(code=code, name=name, unit=unit, stock=0, min_stock=mn); ps._move(m.pk, stock, self.poli_u, "purchase", note="Stok awal (demo)"); meds.append(m)
        ps._move(meds[7].pk, -(meds[7].stock - 6), self.poli_u, "adjustment", note="Penyesuaian demo: stok menipis")  # satu obat di bawah minimum → muncul peringatan
        by_med = {m.code: m for m in meds}  # master diagnosa ↔ obat lazim (form kunjungan mengisi resep otomatis dari sini)
        for dcode, links in {"J00": [("OB004", 10, "3 x 1"), ("OB001", 10, "3 x 1 bila demam"), ("OB007", 10, "1 x 1")], "J06.9": [("OB002", 15, "3 x 1 sesudah makan"), ("OB012", 10, "3 x 1")],
                             "A09": [("OB006", 6, "diminum tiap diare"), ("OB003", 10, "3 x 1 sebelum makan")], "K29.7": [("OB003", 15, "3 x 1 sebelum makan")], "R51": [("OB001", 10, "3 x 1")],
                             "M54.5": [("OB005", 10, "3 x 1 sesudah makan")], "I10": [("OB010", 30, "1 x 1 pagi")], "L30.9": [("OB011", 1, "dioles 2 x sehari")],
                             "S61.9": [("OB008", 1, "pembersih luka"), ("OB009", 2, "pembalut")]}.items():
            dd = next(x for x in dg if x.code == dcode)
            for i, (mc, q, dose) in enumerate(links): pl.DiagnosisMedicine.objects.create(diagnosis=dd, medicine=by_med[mc], qty=q, dosage=dose, position=i)
        pool = [e for e in self.active if e.department.code != "POL"]; recs = []
        staff = [e for e in self.active if e.department.code == "POL"]; bidan = next((e for e in staff if e.position.name == "Bidan"), None)
        def who(kind):  # pemeriksa = karyawan Poli (bidan untuk kehamilan); dokter = bukan karyawan, hanya nama. Sebagian kunjungan hanya petugas (tanpa dokter) dan sebagian tanpa pemeriksa.
            ex = bidan if (kind == "kehamilan" and bidan) else r.choice(staff) if staff and r.random() < .85 else None
            return {"examiner": ex, "doctor_name": r.choice(DOCTORS) if (kind in ("kehamilan", "kecelakaan_kerja") or r.random() < .6) else ""}
        for e in r.sample(pool, min(25, len(pool))):  # alergi demo (putaran 35): tampil di ringkasan pasien form kunjungan
            sub, rx_, sev = r.choice([("Amoxicillin", "Ruam kulit", "sedang"), ("Penisilin", "Sesak napas", "berat"), ("Udang", "Gatal-gatal", "ringan"), ("Ibuprofen", "Nyeri lambung", "sedang"), ("Debu", "Bersin", "ringan")])
            pl.PatientAllergy.objects.create(employee=e, substance=sub, reaction=rx_, severity=sev, created_by=self.poli_u)
        for back in range(180):  # 6 bulan; kunjungan harian meningkat menuju hari ini
            d = today - timedelta(days=back)
            if d.weekday() == 6: continue
            k = r.randint(1, 4) if back > 7 else r.randint(2, 6) + (3 if back == 0 else 0)
            for _ in range(k):
                kind = r.choices(["berobat", "pemeriksaan", "kecelakaan_kerja"], [85, 10, 5])[0]  # kunjungan kehamilan dibuat terpisah di bawah (konsisten dengan cuti hamil)
                e = r.choice(pool)
                diag = r.choice(dg) if kind != "pemeriksaan" else dg[-1]
                exam = {"tensi": f"{r.randint(100, 140)}/{r.randint(65, 90)}", "suhu": round(r.uniform(36, 38.5), 1)}
                if kind == "kecelakaan_kerja":  # bentuk sama dengan RecordForm.build_exam: lokasi + kronologi wajib, bagian tubuh/jenis cedera/hari hilang opsional
                    place, story, part, jenis, lost = r.choice(INCIDENTS); exam["kecelakaan"] = {"lokasi": place, "kronologi": story, "bagian_tubuh": part, "jenis_cedera": jenis, **({"hari_hilang": lost} if lost else {})}
                if kind == "pemeriksaan":
                    exam["pemeriksaan"] = {"kesimpulan": r.choices([c for c, _ in EXAM_CONCLUSIONS], [70, 25, 5])[0], "hasil": "Pemeriksaan berkala (MCU) — tanda vital dalam batas wajar (demo)"}
                    if exam["pemeriksaan"]["kesimpulan"] != "fit": exam["pemeriksaan"]["hasil"] = "Perlu kontrol tekanan darah dalam 2 minggu (demo)"
                rec = pl.MedicalRecord.objects.create(employee=e, kind=kind, visit_at=self.at(d, r.randint(7, 16)), complaint="Kecelakaan kerja" if kind == "kecelakaan_kerja" else "Pemeriksaan kesehatan berkala" if kind == "pemeriksaan" else r.choice(COMPLAINTS),
                                                     exam=exam, diagnosis=diag, treatment="Istirahat, obat sesuai resep", created_by=self.poli_u, **who(kind))
                recs.append(rec)
                if kind == "berobat" and r.random() < .8:
                    m = r.choice(meds[:7] + meds[9:]); q = r.randint(3, 12)
                    try: ps._move(m.pk, -q, self.poli_u, "prescription", ref=str(rec.pk)); pl.Prescription.objects.create(record=rec, medicine=m, qty=q, dosage="3 x 1 setelah makan")
                    except ValueError: pass
        # kunjungan kehamilan: HPHT sama dengan cuti hamil di hrd_ops (self.pregnant) → HPL & usia konsisten; tiap karyawati 2–3 kontrol (bentuk exam = RecordForm.exam)
        preg_recs = []
        for e, hpht in getattr(self, "pregnant", []):
            for back in sorted(r.sample(range(0, 56), 2) + [0], reverse=True):
                vd = today - timedelta(days=back); weeks = max(1, (vd - hpht).days // 7)
                if vd < hpht: continue
                exam = {"tensi": f"{r.randint(100, 125)}/{r.randint(65, 80)}", "bb": round(r.uniform(55, 75), 1),
                        "kehamilan": {"hpht": hpht.isoformat(), "hpl": (hpht + timedelta(days=280)).isoformat(), "usia_minggu": weeks, "g": 2, "p": 1, "a": 0, "tfu": round(weeks * 1.0, 1), "djj": r.randint(125, 155), "letak": "kepala"}}
                rec = pl.MedicalRecord.objects.create(employee=e, kind="kehamilan", visit_at=self.at(vd, r.randint(8, 14)), complaint="Pemeriksaan kehamilan rutin", exam=exam, diagnosis=dg[-1],
                                                     treatment="Lanjutkan vitamin; kontrol 2 minggu lagi", created_by=self.poli_u, **who("kehamilan"))
                preg_recs.append(rec); recs.append(rec)
        # catatan tambahan (append-only) pada beberapa rekam medis
        for rec in r.sample(recs[:60], 3): ps.add_addendum(self.poli_u, rec, "Koreksi: berat badan tercatat 58 kg, seharusnya 56 kg (demo)")
        # surat Poli lewat penghitung nomor resmi: izin pulang/libur untuk kunjungan berobat, izin hamil untuk kunjungan kehamilan
        nl = 0
        for rec in [x for x in recs if x.kind == "berobat"][:6]:
            pl.SickLeaveLetter.objects.create(record=rec, kind="izin_pulang", number=pl.LetterCounter.next_number("sip", timezone.now())); nl += 1
        for rec in [x for x in recs if x.kind == "berobat"][6:11]:
            pl.SickLeaveLetter.objects.create(record=rec, kind="izin_libur", number=pl.LetterCounter.next_number("sil", timezone.now()), start_date=rec.visit_at.date(), days=r.randint(1, 3)); nl += 1
        for rec, purpose in zip(preg_recs[-3:], ("kontrol", "ringan", "cuti_melahirkan")):
            pl.SickLeaveLetter.objects.create(record=rec, kind="izin_hamil", number=pl.LetterCounter.next_number("sih", timezone.now()), start_date=rec.visit_at.date(), days=1 if purpose != "cuti_melahirkan" else 90, purpose=purpose); nl += 1
        self.letters = nl
        for rec, st in zip(r.sample(recs[:40], 4), ("diajukan", "dirujuk", "selesai", "batal")):
            ref = ps.create_referral(self.poli_u, rec, r.choice(["RSUD Kardinah Tegal", "RS Mitra Keluarga Tegal", "RS Islam Harapan Anda"]), "Rujukan demo")
            if st in ("dirujuk", "selesai"): ps.referral_transition(ref.pk, "dirujuk", self.poli_u)
            if st == "selesai": ps.referral_transition(ref.pk, "selesai", self.poli_u, "Pasien sudah ditangani, kontrol 1 minggu (demo)")
            if st == "batal": ps.referral_transition(ref.pk, "batal", self.poli_u, "Pasien membaik, rujukan tidak diperlukan (demo)")
        # tagihan mitra (putaran 24, P6): dari kunjungan yang sama (keluhan/diagnosa ikut rekam medis) dengan status beragam
        partners = [pl.Partner.objects.create(name=n, kind=k, contact="Data demo") for n, k in (("RSUD Kardinah Tegal", "rs"), ("Klinik Sehat Sentosa", "klinik"), ("Lab Medika Prima", "lab"))]
        flows = [(), ("diverifikasi",), ("diverifikasi", "disetujui"), ("diverifikasi", "disetujui", "dibayar"), ("ditolak",), ()]
        for i, rec in enumerate(r.sample(recs[:40], 6)):
            bd = min(timezone.localdate(), rec.visit_at.date() + timedelta(days=1)); total = Decimal(r.choice([185_000, 320_000, 450_000, 1_250_000]))
            b = ps.create_partner_bill(self.poli_u, partners[i % 3], f"DEMO-{2026}{i + 1:03d}", bd, rec.employee, rec.visit_at.date(), r.choice(["rawat_jalan", "lab", "obat"]), rec.complaint,
                                       rec.diagnosis, r.choice(["perusahaan", "perusahaan", "bpjs"]), total, [("Pelayanan (demo)", 1, total)] if i % 2 == 0 else ())
            for st in flows[i]:
                ps.bill_transition(b.pk, st, self.poli_u, note="Data demo" if st == "ditolak" else "", paid_date=timezone.localdate() if st == "dibayar" else None, payment_ref="TRF-DEMO" if st == "dibayar" else "")
        self.stat += f", {len(recs)} kunjungan poli ({len(preg_recs)} kehamilan), {self.letters} surat Poli, {len(meds)} obat, 6 tagihan mitra"

    # ---------- audit contoh
    def audit(self):
        for u, mod, act, ot in ((self.hrd_u, "hr", "create", "Employee"), (self.hrd_u, "hr", "update", "Employee"), (self.hrd_u, "request", "approve", "ChangeRequest"),
                                (self.poli_u, "poli", "create", "MedicalRecord"), (self.root, "core", "create", "User"), (self.hrd_u, "hrd", "update", "BpjsMembership")):
            AuditLog.objects.create(user=u, ip="127.0.0.1", module=mod, action=act, object_type=ot, object_id=str(self.r.randint(1, 50)), after={"demo": True})
