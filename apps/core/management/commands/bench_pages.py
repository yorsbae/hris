"""Ukur waktu halaman/API utama terhadap data uji beban (jalankan `seed_load` dulu), di database yang sedang dipakai (sebaiknya PostgreSQL).

    python manage.py bench_pages                  # 15 ulangan per titik, bandingkan dengan sasaran VISION
    python manage.py bench_pages --repeat 30 --json hasil.json

PERHATIAN pada arti angka: pengukuran ini DI DALAM proses (Django test Client, satu utas, tanpa jaringan/gunicorn/Nginx) sehingga mengukur kerja aplikasi + database
(middleware, view, query, render), bukan beban serentak banyak pengguna. Kolom `q` = jumlah query SQL (kenaikan tajam antar-ukuran data = tanda N+1).
Sasaran VISION (usulan): daftar & detail p95 < 500 ms, dashboard < 1 dtk. Untuk beban serentak gunakan Locust/k6 terhadap server sungguhan."""
import json
import math
import time
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext, override_settings
from apps.core.models import User
from apps.hr.models import Employee

TARGET_MS = {"list": 500, "detail": 500, "dashboard": 1000}  # sasaran VISION (usulan); titik lain hanya dilaporkan


def pct(xs, p): s = sorted(xs); return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


class Command(BaseCommand):
    help = "Ukur waktu halaman/API utama terhadap data uji beban (seed_load)."

    def add_arguments(self, p):
        p.add_argument("--repeat", type=int, default=15)
        p.add_argument("--json", default="", help="simpan hasil mentah ke berkas JSON")
        p.add_argument("--no-warmup", action="store_true", help="lewati pemanasan: ukur panggilan PERTAMA (dingin: cache kosong, template belum dikompilasi)")
        p.add_argument("--hrd", default="load_hrd"); p.add_argument("--su", default="load_su")

    def handle(self, *a, **o):
        n = Employee.objects.filter(nik__startswith="LD").count()
        if not n: raise CommandError("Belum ada data uji beban. Jalankan: python manage.py seed_load")
        hrd, su = User.objects.filter(username=o["hrd"]).first(), User.objects.filter(username=o["su"]).first()
        if not hrd or not su: raise CommandError("Akun load_hrd/load_su tidak ada (dibuat oleh seed_load).")
        mid = Employee.objects.filter(nik__startswith="LD").order_by("pk")[n // 2]
        ids = ",".join(str(i) for i in Employee.objects.filter(nik__startswith="LD").values_list("pk", flat=True))
        L = "/api/employees/"
        # (nama, kelompok-sasaran, pengguna, metode, url, data)
        points = [
            ("Halaman /employees/", "list", hrd, "GET", "/employees/", None),
            ("API daftar hal.1", "list", hrd, "GET", L, {}),
            ("API daftar hal.60 (dalam)", "list", hrd, "GET", L, {"page": 60}),
            ("API daftar size=200", "list", hrd, "GET", L, {"size": 200}),
            ("API cari nama 'Santoso'", "list", hrd, "GET", L, {"q": "Santoso"}),
            ("API cari NIK 'LD0150'", "list", hrd, "GET", L, {"q": "LD0150"}),
            ("API urut tgl masuk + kelompok dept", "list", hrd, "GET", L, {"sort": "-join_date", "group": "department"}),
            ("API filter dept+status", "list", hrd, "GET", L, {"department": mid.department_id, "status": "aktif"}),
            ("API filter lanjutan (gender+masa kerja+usia)", "list", hrd, "GET", L, {"gender": "P", "tenure": "3-5", "age_min": 25, "age_max": 45}),
            ("API data bermasalah (any)", "list", hrd, "GET", L, {"problem": "any"}),
            ("API kontrak segera berakhir", "list", hrd, "GET", L, {"contract": "expiring"}),
            ("API ids semua hasil (pilih semua)", "list", hrd, "GET", L + "ids/", {}),
            ("API facets jabatan", "list", hrd, "GET", L + "facets/", {"department": mid.department_id}),
            ("Detail karyawan", "detail", hrd, "GET", f"/employees/{mid.pk}/", None),
            ("Dashboard HRD (halaman)", "dashboard", hrd, "GET", "/", None),
            ("Dashboard HRD (panel agregat /api/dashboard/panels/)", "dashboard", hrd, "GET", "/api/dashboard/panels/", {}),
            ("Dashboard Superadmin (panel agregat)", "dashboard", su, "GET", "/api/dashboard/panels/", {}),
            ("Pengajuan (daftar)", "list", hrd, "GET", "/requests/", None),
            ("Absensi harian", "list", hrd, "GET", "/absensi/", None),
            ("Absensi bulanan", "list", hrd, "GET", "/absensi/bulanan/", None),
            ("Riwayat Impor", "list", hrd, "GET", "/absensi/scanlog/", None),
            ("Data Scan (1 hari)", "list", hrd, "GET", "/absensi/scanlog/data/", None),
            ("Data Scan (31 hari)", "list", hrd, "GET", "/absensi/scanlog/data/?from=2026-09-01&to=2026-10-01", None),
            ("Shift Harian (31 hari)", "list", hrd, "GET", "/absensi/shift/?from=2026-09-01&to=2026-10-01", None),
            ("Kontrol Harian", "list", hrd, "GET", "/absensi/kontrol/", None),
            ("Master Mesin", "list", hrd, "GET", "/absensi/mesin/", None),
            ("Pencarian global 'Santoso'", "list", hrd, "GET", "/search/", {"q": "Santoso"}),
            ("Pratinjau aksi massal (semua karyawan)", None, hrd, "POST", "/employees/bulk/", {"action": "status", "ids": ids}),  # mendekati batas BULK_MAX
            ("Ekspor CSV (semua)", None, hrd, "GET", L + "export/", {"format": "csv"}),
            ("Ekspor XLSX (semua)", None, hrd, "GET", L + "export/", {"format": "xlsx"}),
            ("Audit (superadmin)", "list", su, "GET", "/audit/", None),
        ]
        out, c = [], Client(HTTP_HOST="localhost")
        self.stdout.write(f"Data: {n} karyawan · ulangan: {o['repeat']} · DB: {connection.vendor} · satu utas, di dalam proses\n")
        self.stdout.write(f"{'Titik':46} {'st':>3} {'p50':>7} {'p95':>7} {'maks':>7} {'q':>4} {'KB':>6}  sasaran")
        with override_settings(ALLOWED_HOSTS=["*"], THROTTLE_ZONES={}, PUBLIC_THROTTLE={}):
            for name, grp, user, method, url, data in points:
                c.force_login(user)
                call = (lambda: c.post(url, data)) if method == "POST" else (lambda: c.get(url, data or {}))
                if not o["no_warmup"]: call()  # pemanasan (cache, koneksi, kompilasi template)
                times, queries, size, status = [], 0, 0, 0
                for _ in range(o["repeat"]):
                    with CaptureQueriesContext(connection) as q:
                        t = time.perf_counter(); r = call(); times.append((time.perf_counter() - t) * 1000)
                    queries, size, status = len(q), len(r.content) if hasattr(r, "content") else 0, r.status_code
                p50, p95 = pct(times, 50), pct(times, 95); tgt = TARGET_MS.get(grp)
                verdict = "-" if tgt is None else ("OK" if p95 < tgt else f"LEWAT (>{tgt} ms)")
                out.append({"name": name, "status": status, "p50_ms": round(p50, 1), "p95_ms": round(p95, 1), "max_ms": round(max(times), 1), "queries": queries, "kb": round(size / 1024, 1), "target_ms": tgt, "verdict": verdict})
                self.stdout.write(f"{name:46} {status:>3} {p50:7.0f} {p95:7.0f} {max(times):7.0f} {queries:4} {size / 1024:6.0f}  {verdict}")
        bad = [x for x in out if x["status"] != 200 or x["verdict"].startswith("LEWAT")]
        if o["json"]:
            with open(o["json"], "w", encoding="utf-8") as f: json.dump({"employees": n, "vendor": connection.vendor, "repeat": o["repeat"], "points": out}, f, ensure_ascii=False, indent=1)
        self.stdout.write(self.style.WARNING(f"\n{len(bad)} titik bermasalah (status ≠ 200 atau melewati sasaran): " + ", ".join(x["name"] for x in bad)) if bad else self.style.SUCCESS("\nSemua titik 200 dan memenuhi sasaran."))
