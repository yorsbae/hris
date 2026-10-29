"""Patch Master Tanggungan — Penanggung Tagihan Mitra = BPJS / Non BPJS + Master Tanggungan (persen perusahaan per tanggal berlaku), menu MCU dipindah ke info karyawan,
kalimat 'Beban …' dihapus dari cetak. Migrasi data penanggung lama ke yang baru."""
import importlib
from datetime import date, timedelta
from decimal import Decimal as D

from django.apps import apps as django_apps
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core import navigation
from apps.core.models import AuditLog
from apps.poli import services
from apps.poli import test_billing as tb
from apps.poli.models import CoverageRule, PartnerBill

TODAY = date.today()
FUTURE = TODAY + timedelta(days=30)


class RuleLookupTests(tb.Base):
    def rule(self, payer, pct, frm): return CoverageRule.objects.create(payer=payer, company_pct=pct, effective_from=frm)

    def test_defaults_without_rules(self):
        self.assertEqual(services.company_pct_for("bpjs", TODAY), D(75)); self.assertEqual(services.company_pct_for("non_bpjs", TODAY), D(100))

    def test_latest_rule_on_or_before_the_date_wins(self):
        self.rule("bpjs", 80, date(2026, 1, 1)); self.rule("bpjs", 60, date(2026, 6, 1))
        self.assertEqual(services.company_pct_for("bpjs", date(2025, 12, 31)), D(75))      # sebelum aturan pertama → bawaan
        self.assertEqual(services.company_pct_for("bpjs", date(2026, 1, 1)), D(80))        # tanggal mulai = berlaku
        self.assertEqual(services.company_pct_for("bpjs", date(2026, 5, 31)), D(80))
        self.assertEqual(services.company_pct_for("bpjs", date(2026, 6, 1)), D(60))
        self.assertEqual(services.company_pct_for("non_bpjs", date(2026, 6, 1)), D(100))   # penanggung lain tidak terpengaruh

    def test_bill_uses_rule_by_service_date_and_keeps_snapshot(self):
        self.rule("bpjs", 60, tb.SVC)
        b = services.create_partner_bill(self.poli, self.partner, "C-1", tb.BILLD, self.e1, tb.SVC, "rawat_jalan", "", None, "bpjs", D(100000), ())
        self.assertEqual((b.company_pct, b.company_amount, b.employee_amount), (D(60), D(60000), D(40000)))
        old = services.create_partner_bill(self.poli, self.partner, "C-2", tb.BILLD, self.e1, tb.SVC - timedelta(days=5), "rawat_jalan", "", None, "bpjs", D(100000), ())
        self.assertEqual((old.company_pct, old.company_amount), (D(75), D(75000)))         # tanggal pelayanan sebelum aturan → bawaan
        self.rule("bpjs", 50, tb.SVC + timedelta(days=1))                                    # aturan baru kemudian tidak menggeser tagihan lama
        b.refresh_from_db(); self.assertEqual((b.company_pct, b.company_amount, b.employee_amount), (D(60), D(60000), D(40000)))

    def test_non_bpjs_can_also_be_changed(self):
        self.rule("non_bpjs", 90, tb.SVC)
        b = services.create_partner_bill(self.poli, self.partner, "C-3", tb.BILLD, self.e2, tb.SVC, "rawat_jalan", "", None, "non_bpjs", D(100000), ())
        self.assertEqual((b.company_amount, b.employee_amount), (D(90000), D(10000)))

    @override_settings(POLI_BPJS_COMPANY_PCT=70)
    def test_setting_is_only_the_fallback(self):
        self.assertEqual(services.company_pct_for("bpjs", TODAY), D(70))
        self.rule("bpjs", 85, date(2020, 1, 1)); self.assertEqual(services.company_pct_for("bpjs", TODAY), D(85))

    def test_form_creates_bill_with_rule(self):
        self.rule("bpjs", 60, tb.SVC)
        r = self.post(bill_number="C-F1", payer="bpjs", total_amount="100.000"); self.assertEqual(r.status_code, 302)
        b = PartnerBill.objects.get(bill_number="C-F1"); self.assertEqual((b.company_amount, b.employee_amount), (D(60000), D(40000)))

    def test_detail_and_list_show_percent(self):
        self.rule("bpjs", 60, tb.SVC); b = self.bill(total=D(100000))
        self.assertContains(self.client.get(reverse("poli_bill_detail", args=[b.pk])), "60%, BPJS")
        self.assertContains(self.client.get(reverse("poli_bills"), {"period": tb.PER}), "BPJS 60% prs")


class CoveragePageTests(tb.Base):
    URL = "poli_coverage"

    def add(self, **kw):
        d = {"action": "add", "payer": "bpjs", "company_pct": "70", "effective_from": TODAY.isoformat(), "note": "kebijakan baru"}; d.update(kw)
        return self.client.post(reverse(self.URL), d)

    def test_access_poli_and_superadmin_only(self):
        for who, code in (("poli", 200), ("su", 200), ("hrd", 403), ("adm", 403)):
            self.login(who); self.assertEqual(self.client.get(reverse(self.URL)).status_code, code, who)
        for who in ("hrd", "adm"):
            self.login(who); self.assertEqual(self.add().status_code, 403); self.assertFalse(CoverageRule.objects.exists())

    def test_add_rule_audited_and_listed(self):
        r = self.add(); self.assertEqual(r.status_code, 302)
        rule = CoverageRule.objects.get(); self.assertEqual((rule.payer, rule.company_pct, rule.effective_from, rule.created_by), ("bpjs", 70, TODAY, self.poli))
        self.assertTrue(AuditLog.objects.filter(module="poli", action="coverage_add").exists())
        page = self.client.get(reverse(self.URL)); self.assertContains(page, "kebijakan baru"); self.assertContains(page, "berlaku")

    def test_validation(self):
        for bad in ({"company_pct": "101"}, {"company_pct": "-1"}, {"company_pct": "abc"}, {"payer": "perusahaan"}, {"effective_from": "bukan-tanggal"}):
            r = self.add(**bad); self.assertEqual(r.status_code, 200, bad)
        self.assertFalse(CoverageRule.objects.exists())

    def test_same_payer_same_date_rejected_not_overwritten(self):
        self.add(); r = self.add(company_pct="10"); self.assertEqual(r.status_code, 200); self.assertIn("effective_from", r.context["form"].errors)
        self.assertEqual(CoverageRule.objects.get().company_pct, 70)
        self.assertEqual(self.add(payer="non_bpjs").status_code, 302)                       # penanggung lain, tanggal sama → boleh

    def test_states_current_superseded_upcoming(self):
        old = CoverageRule.objects.create(payer="bpjs", company_pct=80, effective_from=TODAY - timedelta(days=60))
        cur = CoverageRule.objects.create(payer="bpjs", company_pct=70, effective_from=TODAY - timedelta(days=10))
        nxt = CoverageRule.objects.create(payer="bpjs", company_pct=60, effective_from=FUTURE)
        st = {r.pk: r.state for r in self.client.get(reverse(self.URL)).context["rules"]}
        self.assertEqual((st[old.pk], st[cur.pk], st[nxt.pk]), ("diganti", "berlaku", "akan berlaku"))

    def test_only_upcoming_rules_can_be_cancelled(self):
        cur = CoverageRule.objects.create(payer="bpjs", company_pct=70, effective_from=TODAY)
        nxt = CoverageRule.objects.create(payer="bpjs", company_pct=60, effective_from=FUTURE)
        self.client.post(reverse(self.URL), {"action": "delete", "id": cur.pk}); self.assertTrue(CoverageRule.objects.filter(pk=cur.pk).exists())   # sudah berlaku → ditolak
        self.client.post(reverse(self.URL), {"action": "delete", "id": nxt.pk}); self.assertFalse(CoverageRule.objects.filter(pk=nxt.pk).exists())
        self.assertTrue(AuditLog.objects.filter(module="poli", action="coverage_cancel").exists())
        self.assertEqual(self.client.post(reverse(self.URL), {"action": "delete", "id": 99999}).status_code, 200)   # tidak ada → pesan galat, bukan 500

    def test_bill_form_and_list_link_to_master(self):
        self.assertContains(self.client.get(reverse("poli_bill_new")), reverse(self.URL))
        self.assertContains(self.client.get(reverse("poli_bills")), reverse(self.URL))


class PayerChoiceTests(tb.Base):
    def test_form_offers_only_bpjs_and_non_bpjs(self):
        r = self.client.get(reverse("poli_bill_new")); self.assertEqual([c for c, _ in r.context["form"].fields["payer"].choices], ["bpjs", "non_bpjs"])
        self.assertContains(r, "Non BPJS")

    def test_import_accepts_label_spellings(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        hdr = "mitra,nomor_tagihan,tanggal_tagihan,nik,tanggal_layanan,jenis_layanan,kode_diagnosa,penanggung,total,keluhan\n"
        rows = "".join(f"RS Sehat,IMP-{i},{tb.BILLD.isoformat()},001,{tb.SVC.isoformat()},rawat_jalan,A09,{py},100000,\n" for i, py in enumerate(("BPJS", "Non BPJS", "non-bpjs", "non_bpjs")))
        self.client.post(reverse("poli_bill_import"), {"file": SimpleUploadedFile("t.csv", (hdr + rows).encode()), "mode": "import"})
        self.assertEqual(dict(PartnerBill.objects.values_list("bill_number", "payer")), {"IMP-0": "bpjs", "IMP-1": "non_bpjs", "IMP-2": "non_bpjs", "IMP-3": "non_bpjs"})

    def test_filter_by_payer(self):
        a = self.bill(payer="bpjs", bill_number="F-1"); self.bill(payer="non_bpjs", bill_number="F-2")
        page = self.client.get(reverse("poli_bills"), {"period": tb.PER, "payer": "non_bpjs"}).context["page"]
        self.assertEqual([b.bill_number for b in page], ["F-2"]); self.assertNotEqual(a.payer, "non_bpjs")


class MenuTests(TestCase):
    class U:  # pengganti pengguna untuk navigation.build (hanya butuh .role)
        def __init__(self, role): self.role = role

    def labels(self, role, path="/poli/"):
        groups, _ = navigation.build(self.U(role), path)
        return [i["label"] for _, g in [(g["title"], g) for g in groups] for i in g["items"]]

    def test_poli_menu_has_no_mcu_but_has_master_tanggungan(self):
        from apps.core.models import Role
        labels = self.labels(Role.POLI); self.assertNotIn("MCU Karyawan", labels)
        self.assertIn("Tagihan Mitra", labels); self.assertIn("Master Tanggungan", labels)

    def test_master_tanggungan_active_on_its_page_and_is_a_submenu(self):
        from apps.core.models import Role
        groups, crumb = navigation.build(self.U(Role.POLI), "/poli/billing/coverage/")
        self.assertEqual(crumb, "Master Tanggungan")
        items = [i for g in groups for i in g["items"] if i["label"] == "Master Tanggungan"]; self.assertTrue(items[0]["sub"])


class PrintTests(tb.Base):
    def test_rekap_footer_has_no_burden_sentence(self):
        from unittest import mock
        from reportlab.pdfgen import canvas as rl_canvas
        from apps.poli import pdf
        self.bill()
        texts, orig = [], rl_canvas.Canvas.drawString
        def spy(c, x, y, t, *a, **k): texts.append(str(t)); return orig(c, x, y, t, *a, **k)
        bills = list(PartnerBill.objects.select_related("employee__position", "employee__department"))
        with mock.patch.object(rl_canvas.Canvas, "drawString", spy): pdf.bills_pdf(bills, tb.PER, self.partner, self.poli)
        j = "\n".join(texts); self.assertNotIn("Beban", j); self.assertNotIn("ber-BPJS aktif", j); self.assertIn("Dicetak", j)


class MigrationRemapTests(TestCase):
    """Fungsi pemetaan data migrasi 0011 dijalankan terhadap model sungguhan (nominal tidak berubah, hanya label penanggung & salinan persen)."""
    def test_remap(self):
        from apps.hr.models import Department, Employee
        mig = importlib.import_module("apps.poli.migrations.0011_coverage_rules_bpjs_payers")
        from apps.poli.models import Partner
        dept = Department.objects.create(name="X", code="X"); p = Partner.objects.create(name="RS")
        e = Employee.objects.create(nik="900", name="E", department=dept, join_date=date(2020, 1, 1))
        def mk(n, payer, has, co, emp):
            return PartnerBill.objects.create(partner=p, bill_number=n, bill_date=TODAY, employee=e, service_date=TODAY, payer=payer, total_amount=100, has_bpjs=has, company_amount=co, employee_amount=emp)
        mk("a", "perusahaan", True, 75, 25); mk("b", "perusahaan", False, 100, 0); mk("c", "karyawan", False, 0, 100); mk("d", "bpjs", True, 0, 0)
        mig.remap_payers(django_apps, None)
        got = {x.bill_number: (x.payer, x.company_pct, x.company_amount, x.employee_amount) for x in PartnerBill.objects.all()}
        self.assertEqual(got, {"a": ("bpjs", D(75), D(75), D(25)), "b": ("non_bpjs", D(100), D(100), D(0)), "c": ("non_bpjs", D(0), D(0), D(100)), "d": ("bpjs", D(0), D(0), D(0))})
