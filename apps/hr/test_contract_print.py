"""Cetak kontrak PKWT: engine template Word (penanda, 2 halaman), nomor, terbilang, halaman susun/unduh, pengaturan, hak akses."""
from datetime import date
from io import BytesIO
from django.test import SimpleTestCase, TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from docx import Document
from apps.core.models import Role, User
from . import contract_print as cp, docx_html
from .models import Contract, ContractConfig, Department, Employee, MinimumWage, Position

import struct, zlib


def _png():
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 40, 20, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x00" * 160) * 20)) + chunk(b"IEND", b"")


PNG = _png()


def text_of(data):
    d = Document(BytesIO(data)); out = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            for c in row.cells: out += [p.text for p in c.paragraphs]
    return "\n".join(out)


class PureTests(SimpleTestCase):
    def test_terbilang_rupiah_roman(self):
        self.assertEqual(cp.terbilang(2500000), "dua juta lima ratus ribu")
        self.assertEqual(cp.rupiah(2500000), "2.500.000")
        self.assertEqual(cp.format_number("{seq:03d}/PKWT/{roman}/{year}", 7, date(2026, 11, 3)), "007/PKWT/XI/2026")

    def test_batches(self):
        self.assertEqual([len(b) for b in cp.batches(list(range(7)), 3)], [3, 3, 1])


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dept = Department.objects.create(code="PR", name="Produksi"); cls.pos = Position.objects.create(name="Operator")
        cls.hrd = User.objects.create_user("hrd", password="x", role=Role.HRD)
        cls.adm = User.objects.create_user("adm", password="x", role=Role.DEPT_ADMIN, department=cls.dept)
        MinimumWage.objects.create(year=2026, amount=2500000)
        cfg = ContractConfig.get(); cfg.signer_name = "Budi Santoso"; cfg.signer_title = "HRD Manager"; cfg.save()
        cls.cs = []
        for i, nm in enumerate(["Citra", "Ana", "Bima"], 1):
            e = Employee.objects.create(nik=f"10{i}", name=nm, gender="L", department=cls.dept, position=cls.pos, join_date=date(2025, 1, 1),
                                        address="Jl. Mawar 1", nik_ktp=f"33{i}0000000000001", birth_date=date(1995, 1, 1))
            cls.cs.append(Contract.objects.create(employee=e, number=f"00{i}/PKWT/XI/2026", kind="PKWT", start=date(2026, 11, 1), end=date(2027, 4, 30)))


class EngineTests(Base):
    def test_default_template_valid_and_fills_everything(self):
        cfg = ContractConfig.get(); err, info = cp.validate_template(cp.template_bytes(cfg)); self.assertIsNone(err)
        vals = [cp.values_for(c, cfg) for c in self.cs]
        full = text_of(cp.build(cp.template_bytes(cfg), vals, "penuh", PNG))
        self.assertNotIn("{{", full)
        for c in self.cs: self.assertIn(c.employee.name, full); self.assertIn(c.number, full)
        self.assertIn("2.500.000", full); self.assertIn("Budi Santoso", full)

    def test_depan_and_belakang_are_different_halves(self):
        cfg = ContractConfig.get(); tpl = cp.template_bytes(cfg); v = [cp.values_for(self.cs[0], cfg)]
        depan, belakang = text_of(cp.build(tpl, v, "depan")), text_of(cp.build(tpl, v, "belakang"))
        self.assertNotEqual(depan, belakang); self.assertIn("Citra", depan + belakang)

    def test_invalid_templates_rejected(self):
        self.assertIn("bukan dokumen Word", cp.validate_template(b"junk")[0])
        d = Document(); d.add_paragraph("{{tidak_ada}}"); b = BytesIO(); d.save(b)
        self.assertIn("tidak dikenal", cp.validate_template(b.getvalue())[0])
        d = Document(); d.add_paragraph("{{nama}}"); b = BytesIO(); d.save(b)   # tanpa pemisah halaman
        self.assertIn("TEPAT satu", cp.validate_template(b.getvalue())[0])

    def test_problems_block_missing_end_and_wage(self):
        cfg = ContractConfig.get(); c = self.cs[0]; c.end = None
        self.assertTrue(cp.problems(c, cfg)[0])
        c.end = date(2027, 4, 30); c.start = date(2030, 1, 1); MinimumWage.objects.all().delete(); self.assertTrue(cp.problems(c, cfg)[0])

    def test_next_number_skips_used(self):
        n = cp.next_number(date(2026, 11, 5)); self.assertFalse(Contract.objects.filter(number=n).exists())


class ViewTests(Base):
    def ids(self): return [c.pk for c in self.cs]

    def test_only_hrd(self):
        for url in ("/contracts/", "/contracts/settings/", "/contracts/settings/template/"):
            self.client.force_login(self.adm); self.assertEqual(self.client.get(url).status_code, 403, url)
        self.client.logout(); self.assertEqual(self.client.get("/contracts/").status_code, 302)

    def test_list_plan_download(self):
        self.client.force_login(self.hrd)
        self.assertContains(self.client.get("/contracts/?f=semua"), "Citra")
        r = self.client.post("/contracts/plan/", {"ids": self.ids(), "sort": "nama", "size": 2, "back": "same"}); self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context["groups"]), 2); self.assertEqual([c.employee.name for c in r.context["groups"][0]["rows"]], ["Ana", "Bima"])
        for side in ("depan", "belakang", "daftar"):
            r = self.client.post("/contracts/download/", {"ids": self.ids(), "sort": "nama", "size": 2, "back": "same", "dl": f"1:{side}"})
            self.assertEqual(r.status_code, 200, side); self.assertIn("Ana", text_of(r.content))
        self.assertEqual(Contract.objects.filter(printed_at__isnull=False).count(), 2)
        self.assertEqual(self.client.post("/contracts/download/", {"ids": self.ids(), "dl": "9:depan"}).status_code, 400)
        self.assertEqual(self.client.post("/contracts/download/", {"ids": self.ids(), "dl": "1:xx"}).status_code, 400)

    def test_reverse_back_order(self):
        self.client.force_login(self.hrd)
        r = self.client.post("/contracts/download/", {"ids": self.ids(), "sort": "nama", "size": 3, "back": "reverse", "dl": "1:belakang"})
        t = text_of(r.content); self.assertTrue(t.index("Bima") < t.index("Ana"))

    def test_blocked_prevents_download(self):
        MinimumWage.objects.all().delete(); self.client.force_login(self.hrd)
        self.assertEqual(self.client.post("/contracts/download/", {"ids": self.ids(), "dl": "1:depan"}).status_code, 400)
        self.assertContains(self.client.post("/contracts/plan/", {"ids": self.ids()}), "belum bisa dicetak")

    def test_single_docx(self):
        self.client.force_login(self.hrd); r = self.client.get(f"/contracts/{self.cs[0].pk}/docx/")
        self.assertEqual(r.status_code, 200); self.assertIn("Citra", text_of(r.content))

    def test_settings_template_signature_wage(self):
        self.client.force_login(self.hrd); u = "/contracts/settings/"
        self.assertEqual(self.client.get(u).status_code, 200)
        bad = SimpleUploadedFile("x.docx", b"junk"); self.client.post(u, {"action": "upload_template", "file": bad}); self.assertFalse(ContractConfig.get().template_name)
        good = SimpleUploadedFile("t.docx", cp.template_bytes(ContractConfig.get())); self.client.post(u, {"action": "upload_template", "file": good}); self.assertEqual(ContractConfig.get().template_name, "t.docx")
        self.client.post(u, {"action": "reset_template"}); self.assertFalse(ContractConfig.get().template_name)
        self.client.post(u, {"action": "upload_sig", "file": SimpleUploadedFile("s.png", PNG)}); self.assertTrue(ContractConfig.get().signature_png)
        self.client.post(u, {"action": "upload_sig", "file": SimpleUploadedFile("s.png", b"nope")}); self.assertTrue(ContractConfig.get().signature_png)
        self.client.post(u, {"action": "save_wage", "year": 2027, "amount": 2700000}); self.assertEqual(MinimumWage.objects.get(year=2027).amount, 2700000)
        self.client.post(u, {"action": "save_wage", "year": 2027, "amount": 2800000}); self.assertEqual(MinimumWage.objects.filter(year=2027).count(), 1)
        self.client.post(u, {"action": "delete_wage", "year": 2027}); self.assertFalse(MinimumWage.objects.filter(year=2027).exists())
        self.assertEqual(self.client.get("/contracts/settings/sample/").status_code, 200)

    def test_config_rejects_bad_number_format(self):
        self.client.force_login(self.hrd)
        base = {"action": "save_config", "company_name": "X", "company_address": "", "city": "Batang", "signer_name": "A", "signer_title": "B", "pay_day": 25, "batch_size": 50, "back_order": "same"}
        self.client.post("/contracts/settings/", {**base, "number_format": "{nomor}"}); self.assertNotEqual(ContractConfig.get().number_format, "{nomor}")
        self.client.post("/contracts/settings/", {**base, "number_format": "KTR-{seq:04d}"}); self.assertEqual(ContractConfig.get().number_format, "KTR-{seq:04d}")

    def test_new_contract_form_prefills_number(self):
        self.client.force_login(self.hrd); e = Employee.objects.create(nik="900", name="Baru", gender="L", department=self.dept, join_date=date(2026, 1, 1))
        self.assertContains(self.client.get(f"/employees/{e.pk}/contracts/new/"), cp.next_number(date.today()))


class PrintInBrowserTests(Base):
    def ids(self): return [c.pk for c in self.cs]

    def test_renderer_keeps_text_pages_and_signature(self):
        cfg = ContractConfig.get(); full = cp.build(cp.template_bytes(cfg), [cp.values_for(self.cs[0], cfg), cp.values_for(self.cs[1], cfg)], "penuh", PNG)
        css, sheets = docx_html.render(full)
        self.assertEqual(len(sheets), 4)   # 2 kontrak x (depan + belakang)
        html = "".join(sheets); self.assertIn("Citra", html); self.assertIn("Ana", html); self.assertIn("2.500.000", html); self.assertIn("<img", html); self.assertNotIn("{{", html)
        self.assertIn("@page", css)

    def test_renderer_escapes_html(self):
        cfg = ContractConfig.get(); v = cp.values_for(self.cs[0], cfg); v["nama"] = "<script>alert(1)</script>"
        _, sheets = docx_html.render(cp.build(cp.template_bytes(cfg), [v], "penuh")); self.assertNotIn("<script>", "".join(sheets))

    def test_print_batch_page_marks_printed(self):
        self.client.force_login(self.hrd)
        r = self.client.post("/contracts/print/", {"ids": self.ids(), "sort": "nama", "size": 2, "back": "same", "dl": "1:depan"})
        self.assertEqual(r.status_code, 200); self.assertContains(r, "window.print()"); self.assertContains(r, "Ana"); self.assertEqual(Contract.objects.filter(printed_at__isnull=False).count(), 2)
        self.assertContains(self.client.post("/contracts/print/", {"ids": self.ids(), "sort": "nama", "size": 2, "dl": "1:daftar"}), "<table")
        self.assertEqual(self.client.post("/contracts/print/", {"ids": self.ids(), "dl": "7:depan"}).status_code, 400)

    def test_print_single_sample_and_permissions(self):
        self.client.force_login(self.hrd)
        self.assertContains(self.client.get(f"/contracts/{self.cs[0].pk}/print/"), "Citra"); self.assertEqual(self.client.get("/contracts/settings/sample/print/").status_code, 200)
        self.client.force_login(self.adm)
        for u in (f"/contracts/{self.cs[0].pk}/print/", "/contracts/settings/sample/print/"): self.assertEqual(self.client.get(u).status_code, 403)
        self.assertEqual(self.client.post("/contracts/print/", {"ids": self.ids(), "dl": "1:depan"}).status_code, 403)

    def test_wage_accepts_rp_formats(self):
        self.client.force_login(self.hrd)
        for raw, want in (("Rp 2.800.000", 2800000), ("2900000", 2900000), ("3.000.000,00", 3000000)):
            MinimumWage.objects.filter(year=2028).delete(); self.client.post("/contracts/settings/", {"action": "save_wage", "year": 2028, "amount": raw})
            self.assertEqual(MinimumWage.objects.get(year=2028).amount, want, raw)
        self.assertContains(self.client.get("/contracts/settings/"), "Rp 3.000.000")
        self.client.post("/contracts/settings/", {"action": "save_wage", "year": 2029, "amount": "270829018"}); self.assertFalse(MinimumWage.objects.filter(year=2029).exists())


class RenewalRaceTests(Base):
    def test_double_submit_renews_once_and_stale_previous_is_refused(self):
        self.client.force_login(self.hrd); c = self.cs[0]; e = c.employee
        data = {"number": "900/PKWT/XI/2026", "kind": "PKWT", "start": "2027-05-01", "end": "2027-10-31", "previous": c.pk, "note": ""}
        self.client.post(f"/employees/{e.pk}/contracts/new/", data); self.client.post(f"/employees/{e.pk}/contracts/new/", {**data, "number": "901/PKWT/XI/2026"})
        self.assertEqual(Contract.objects.filter(previous=c).count(), 1); c.refresh_from_db(); self.assertEqual(c.status, "diperpanjang")

    def test_locked_recheck_blocks_when_previous_changed_after_validation(self):
        from unittest import mock
        from .emp_forms import ContractForm
        self.client.force_login(self.hrd); c = self.cs[1]; e = c.employee
        data = {"number": "902/PKWT/XI/2026", "kind": "PKWT", "start": "2027-05-01", "end": "2027-10-31", "previous": c.pk, "note": ""}
        real = ContractForm.is_valid
        def stale_valid(form):   # form lolos validasi, lalu sesi lain memperpanjang kontrak itu sebelum transaksi simpan
            ok = real(form); Contract.objects.filter(pk=c.pk).update(status="diperpanjang"); return ok
        with mock.patch.object(ContractForm, "is_valid", stale_valid): r = self.client.post(f"/employees/{e.pk}/contracts/new/", data)
        self.assertEqual(r.status_code, 200); self.assertContains(r, "sudah berstatus"); self.assertFalse(Contract.objects.filter(number="902/PKWT/XI/2026").exists())


class RapatAndChecklistTests(Base):
    def ids(self): return [c.pk for c in self.cs]

    def test_rapat_date_replaces_today_wording_and_is_remembered(self):
        self.client.force_login(self.hrd)
        r = self.client.post("/contracts/download/", {"ids": self.ids(), "sort": "nama", "size": 3, "rapat": "2026-10-30", "dl": "1:depan"})
        t = text_of(r.content); self.assertIn("Berdasarkan hasil rapat/keputusan pada hari Jumat, tanggal 30 Oktober 2026", t); self.assertNotIn("Pada hari ini", t)
        self.assertEqual(ContractConfig.get().decision_date, date(2026, 10, 30))
        t2 = text_of(self.client.post(f"/contracts/{self.cs[0].pk}/print/" if False else "/contracts/download/", {"ids": self.ids(), "sort": "nama", "size": 3, "dl": "1:depan"}).content)
        self.assertIn("30 Oktober 2026", t2)   # tanpa isian baru: memakai tanggal rapat terakhir

    def test_old_placeholder_names_still_work_as_aliases(self):
        cfg = ContractConfig.get(); v = cp.values_for(self.cs[0], cfg, date(2026, 10, 30))
        self.assertEqual((v["tgl_dibuat"], v["hari_dibuat"]), (v["tgl_rapat"], v["hari_rapat"])); self.assertEqual(v["hari_rapat"], "Jumat")
        self.assertEqual(cp.values_for(self.cs[0], ContractConfig(city="B"), None)["tgl_rapat"], "1 November 2026")   # tanpa tanggal rapat: tanggal mulai

    def test_plan_page_has_date_field_and_warning_when_empty(self):
        self.client.force_login(self.hrd); r = self.client.post("/contracts/plan/", {"ids": self.ids(), "sort": "nama"})
        self.assertContains(r, 'name="rapat"'); self.assertContains(r, "belum diisi"); self.assertContains(r, "/contracts/checklist/")

    def test_checklist_xlsx_order_columns_and_summary(self):
        from openpyxl import load_workbook
        prev = Contract.objects.create(employee=self.cs[0].employee, number="OLD/1", kind="PKWT", start=date(2026, 5, 1), end=date(2026, 10, 31), status="diperpanjang")
        self.cs[0].previous = prev; self.cs[0].save()
        self.client.force_login(self.hrd)
        r = self.client.post("/contracts/checklist/", {"ids": self.ids(), "sort": "nama", "size": 2, "rapat": "2026-10-30", "dl": "2:x"})
        self.assertEqual(r.status_code, 200); self.assertIn("spreadsheetml", r["Content-Type"])
        ws = load_workbook(BytesIO(r.content))["Ceklis"]; self.assertIn("gelombang 2", ws["A1"].value)
        self.assertEqual([ws.cell(6, c).value for c in (1, 2, 3)], [3, 2, "Citra"])   # urut nama: Ana, Bima | Citra → gelombang 2 berisi Citra (no. 3)
        self.assertEqual(ws["H6"].value.date() if hasattr(ws["H6"].value, "date") else ws["H6"].value, date(2026, 10, 31))
        allr = load_workbook(BytesIO(self.client.post("/contracts/checklist/", {"ids": self.ids(), "sort": "nama", "size": 2}).content))
        self.assertEqual([allr["Ceklis"].cell(r, 3).value for r in (6, 7, 8)], ["Ana", "Bima", "Citra"]); self.assertIn("Per tanggal berakhir", allr.sheetnames)
        self.assertEqual(allr["Ceklis"].auto_filter.ref, "A5:M8")

    def test_checklist_permissions_and_empty(self):
        self.client.force_login(self.adm); self.assertEqual(self.client.post("/contracts/checklist/", {"ids": self.ids()}).status_code, 403)
        self.client.force_login(self.hrd); self.assertEqual(self.client.post("/contracts/checklist/", {}).status_code, 400)


class HangingNumberTests(TestCase):
    def test_numbered_clause_html_aligns_number_and_wrapped_lines(self):
        from . import docx_html
        cfg = ContractConfig.get()
        css, sheets = docx_html.render(cp.build(cp.template_bytes(cfg), [cp.sample_values(cfg)], "penuh"))
        html = "".join(sheets)
        # nomor dibungkus kotak selebar gantungan (bukan tab 1,25 cm yang membuat teks menjorok)
        self.assertIn("display:inline-block;width:", html)
        self.assertIn("text-indent:-", html)
        self.assertNotIn('<span class="tab"></span>1.', html)
