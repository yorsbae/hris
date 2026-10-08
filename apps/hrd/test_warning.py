"""Putaran 20: Surat Peringatan (SP1–SP3)."""
from django.urls import reverse
from .models import WarningLetter
from . import services
from .test_base import HrdBase


class WarningTests(HrdBase):
    def setUp(self): self.login()

    def new(self, **kw):
        d = {"nik": "001", "level": 1, "issue_date": self.today().isoformat(), "valid_until": "", "violation": "Terlambat 5 kali", "description": "Kronologi"}; d.update(kw)
        return self.client.post(reverse("hrd_warning_new"), d)

    def test_issue_defaults_to_six_months_and_numbers_by_pk(self):
        r = self.new(); w = WarningLetter.objects.get(); self.assertRedirects(r, reverse("hrd_warning_detail", args=[w.pk]))
        self.assertEqual(w.valid_until, services.add_months(self.today(), 6)); self.assertRegex(w.number, rf"^{w.pk:04d}/SP-1/HRD/[IVX]+/{self.today().year}$")
        self.assertEqual(self.last_audit("warning_create").after["level"], 1)

    def test_add_months_clamps_month_end(self):
        from datetime import date
        self.assertEqual(services.add_months(date(2026, 8, 31), 6), date(2027, 2, 28)); self.assertEqual(services.add_months(date(2026, 10, 8), 6), date(2027, 4, 8))

    def test_bad_input(self):
        for bad in ({"nik": "999"}, {"nik": "009"}, {"level": 4}, {"level": 0}, {"violation": ""}, {"valid_until": self.days(-5).isoformat()}):
            self.assertEqual(self.new(**bad).status_code, 200, bad)
        self.assertEqual(WarningLetter.objects.count(), 0)

    def test_cannot_issue_same_or_lower_while_active_but_higher_ok_and_skip_warns(self):
        self.new(level=2)
        self.assertContains(self.new(level=2), "masih punya SP 2"); self.assertContains(self.new(level=1), "masih punya SP 2")
        self.assertEqual(self.new(level=3).status_code, 302); self.assertEqual(WarningLetter.objects.count(), 2)
        r = self.client.post(reverse("hrd_warning_new"), {"nik": "003", "level": 3, "issue_date": self.today().isoformat(), "valid_until": "", "violation": "Berat"}, follow=True)
        self.assertTrue(any("lompat tingkat" in m for m in self.msgs(r)))

    def test_new_allowed_after_expiry_or_revoke(self):
        old = services.issue_warning(self.e1, 1, self.days(-400), self.days(-220), "Lama", "", self.hrd)
        self.assertEqual(self.new(level=1).status_code, 302)
        w = WarningLetter.objects.exclude(pk=old.pk).get(); self.client.post(reverse("hrd_warning_revoke", args=[w.pk]), {"reason": "Salah terbit"})
        self.assertEqual(self.new(level=1).status_code, 302)

    def test_revoke_requires_reason_once_and_blocks_print(self):
        self.new(); w = WarningLetter.objects.get(); u = reverse("hrd_warning_revoke", args=[w.pk])
        self.client.post(u, {"reason": " "}); w.refresh_from_db(); self.assertIsNone(w.revoked_at)
        self.client.post(u, {"reason": "Salah terbit"}); w.refresh_from_db(); self.assertIsNotNone(w.revoked_at); self.assertEqual(w.state(self.today()), "dicabut")
        r = self.client.post(u, {"reason": "lagi"}, follow=True); self.assertTrue(any("sudah dicabut" in m for m in self.msgs(r)))
        self.assertEqual(self.client.get(reverse("hrd_warning_pdf", args=[w.pk])).status_code, 404)
        self.assertEqual(self.client.get(u).status_code, 405)

    def test_pdf_uses_configured_signer_and_is_audited(self):
        from django.test import override_settings
        self.new(); w = WarningLetter.objects.get()
        with override_settings(HRD_SIGNER_NAME="Ir. Pejabat Uji", HRD_SIGNER_TITLE="Manager HRD"):
            r = self.client.get(reverse("hrd_warning_pdf", args=[w.pk]))
        self.assertEqual(r["Content-Type"], "application/pdf"); self.assertTrue(r.content.startswith(b"%PDF")); self.assertIsNotNone(self.last_audit("print_warning"))

    def test_states_and_list_filters(self):
        a = services.issue_warning(self.e1, 1, self.days(-10), self.days(100), "A", "", self.hrd); b = services.issue_warning(self.e2, 2, self.days(-300), self.days(-120), "B", "", self.hrd)
        self.assertEqual((a.state(self.today()), b.state(self.today())), ("aktif", "kedaluwarsa"))
        ids = lambda q: {w.pk for w in self.client.get(reverse("hrd_warnings") + q).context["page"]}
        self.assertEqual(ids("?state=aktif"), {a.pk}); self.assertEqual(ids("?state=kedaluwarsa"), {b.pk}); self.assertEqual(ids("?level=2"), {b.pk}); self.assertEqual(ids("?q=002"), {b.pk})
        self.assertEqual(self.client.get(reverse("hrd_warnings") + "?level=x&state=%27&q=%00").status_code, 400)  # NUL ditolak middleware
        self.assertEqual(self.client.get(reverse("hrd_warnings") + "?level=x&state=zz").status_code, 200)

    def test_detail_escapes_and_shows_history(self):
        a = services.issue_warning(self.e1, 1, self.days(-400), self.days(-220), "<script>1</script>", "", self.hrd); b = services.issue_warning(self.e1, 2, self.days(-5), self.days(150), "B", "", self.hrd)
        r = self.client.get(reverse("hrd_warning_detail", args=[a.pk])); self.assertNotContains(r, "<script>1</script>"); self.assertContains(r, b.number)
