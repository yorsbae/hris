from datetime import timedelta
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from .models import AuditLog, Role, User

PW = "kata-sandi-panjang-123"


class AuditUiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.su = User.objects.create_user("su", password=PW, role=Role.SUPERADMIN)
        cls.hrd = User.objects.create_user("hrd", password=PW, role=Role.HRD)

    def setUp(self):
        self.client.force_login(self.su)

    def mk(self, days_ago=0, **kw):
        a = AuditLog.objects.create(**{"module": "hr", "action": "employee_update", **kw})
        if days_ago: AuditLog.objects.filter(pk=a.pk).update(created_at=timezone.now() - timedelta(days=days_ago))  # queryset.update melewati guard append-only (hanya untuk menyiapkan data uji)
        return a

    def ids(self, url):
        r = self.client.get(url); self.assertEqual(r.status_code, 200); return [a.pk for a in r.context["page"]]

    def test_default_range_is_last_7_days_and_dates_are_inclusive(self):
        new, edge, old = self.mk(0, action="a_new"), self.mk(6, action="a_edge"), self.mk(8, action="a_old")
        got = self.ids("/audit/?action=a_new") + self.ids("/audit/?action=a_edge") + self.ids("/audit/?action=a_old")
        self.assertEqual(got, [new.pk, edge.pk])                                # 8 hari lalu tidak tampil secara bawaan
        today = timezone.localdate().isoformat()
        self.assertEqual(self.ids(f"/audit/?action=a_new&from={today}&to={today}"), [new.pk])   # 'sampai' = inklusif seluruh hari itu
        self.assertEqual(self.ids("/audit/?action=a_old&from=&to="), [old.pk])                  # dikosongkan = tanpa batas

    def test_filters(self):
        e1 = self.mk(user=self.hrd, module="poli", action="view_record", object_type="MedicalRecord", object_id="7", ip="10.0.0.1")
        e2 = self.mk(user=self.su, module="hrd", action="aid_create", object_type="Aid", object_id="3", ip="10.0.0.2")
        e3 = self.mk(user=None, module="auth", action="login_failed", ip="203.0.113.9")
        self.assertEqual(self.ids("/audit/?module=poli"), [e1.pk])
        self.assertEqual(self.ids("/audit/?module=POLI"), [e1.pk])                               # huruf besar dinormalkan
        self.assertEqual(self.ids("/audit/?action=aid_create"), [e2.pk])
        self.assertEqual(self.ids("/audit/?user=HRD&module=poli"), [e1.pk])                      # username tanpa membedakan huruf
        self.assertEqual(self.ids("/audit/?object_type=Aid&object_id=3"), [e2.pk])
        self.assertEqual(self.ids("/audit/?object_type=Aid&object_id=4"), [])
        self.assertEqual(self.ids("/audit/?ip=203.0.113.9"), [e3.pk])
        self.assertEqual(self.ids("/audit/?ip=%20203.0.113.9%20"), [e3.pk])
        self.assertEqual(self.ids("/audit/?ip=bukan-ip"), [])                                    # IP tak valid → kosong, bukan diabaikan diam-diam
        self.assertEqual(self.ids("/audit/?user=tidak-ada"), [])
        self.assertEqual(self.ids("/audit/?module=poli&action=aid_create"), [])                  # filter digabung (AND)

    def test_junk_and_extreme_input_never_500(self):
        for q in ("?from=abc", "?to=2026-13-45", "?from=9999-12-31&to=0001-01-01", "?from=0001-01-01", "?to=9999-12-31", "?page=zzz", "?page=-5",
                  "?object_id=" + "9" * 500, "?module=%27%3B--", "?user=%25", "?from=2026-10-07&to=2026-01-01", "?ip=%3Cscript%3E"):
            self.assertEqual(self.client.get("/audit/" + q).status_code, 200, q)

    def test_reversed_range_is_empty(self):
        self.mk(0); self.assertEqual(self.ids("/audit/?from=2026-10-07&to=2026-01-01"), [])

    def test_pagination_and_newest_first(self):
        for i in range(120): self.mk(0, object_id=str(i))
        r = self.client.get("/audit/?module=hr"); pg = r.context["page"]
        self.assertEqual((len(pg), pg.paginator.count, pg.paginator.num_pages), (50, 120, 3))
        self.assertEqual([a.object_id for a in pg][:2], ["119", "118"])
        self.assertEqual(len(self.client.get("/audit/?module=hr&page=3").context["page"]), 20)
        self.assertContains(r, "page=2&amp;from=")                                                    # tautan halaman membawa filter

    def test_query_count_does_not_grow_with_rows_and_json_columns_not_loaded(self):
        for _ in range(3): self.mk(0, user=self.hrd, before={"x": "b" * 50}, after={"x": "a" * 50})
        with CaptureQueriesContext(connection) as small: self.client.get("/audit/?module=hr")
        for _ in range(60): self.mk(0, user=self.hrd, before={"x": "b" * 50}, after={"x": "a" * 50})
        with CaptureQueriesContext(connection) as big: self.client.get("/audit/?module=hr")
        self.assertEqual(len(small), len(big))                                                   # tanpa N+1 (user di-select_related)
        sql = " ".join(q["sql"] for q in big.captured_queries if "core_auditlog" in q["sql"] and "COUNT" not in q["sql"].upper())
        self.assertNotIn('"before"', sql); self.assertNotIn('"after"', sql)                       # kolom JSON besar tidak dimuat di daftar

    def test_detail_shows_before_after_pretty_and_is_audited(self):
        e = self.mk(0, user=self.hrd, module="hrd", action="aid_update", object_type="Aid", object_id="3", before={"amount": "100"}, after={"amount": "150"})
        r = self.client.get(f"/audit/{e.pk}/")
        self.assertContains(r, "&quot;amount&quot;: &quot;100&quot;"); self.assertContains(r, "&quot;amount&quot;: &quot;150&quot;"); self.assertContains(r, "aid_update")
        v = AuditLog.objects.get(module="audit", action="view_entry")
        self.assertEqual((v.user_id, v.object_type, v.object_id), (self.su.pk, "AuditLog", str(e.pk)))

    def test_detail_handles_empty_json_and_escapes_html(self):
        e0 = self.mk(0); self.assertContains(self.client.get(f"/audit/{e0.pk}/"), "(kosong)")
        e = self.mk(0, after={"nama": "<script>alert(1)</script>", "k<b>": "x"})
        body = self.client.get(f"/audit/{e.pk}/").content.decode()
        self.assertNotIn("<script>alert(1)</script>", body); self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertEqual(self.client.get("/audit/?module=%3Cscript%3E").status_code, 200)

    def test_list_does_not_log_but_detail_does(self):
        self.client.get("/audit/"); self.assertEqual(AuditLog.objects.filter(module="audit").count(), 0)

    def test_audit_is_read_only_via_web(self):
        e = self.mk(0)
        for u in (f"/audit/{e.pk}/delete/", f"/audit/{e.pk}/edit/"): self.assertEqual(self.client.post(u).status_code, 404)
        self.assertEqual(AuditLog.objects.filter(pk=e.pk).count(), 1)

    def test_model_still_append_only(self):
        e = self.mk(0)
        e.action = "diubah"
        with self.assertRaises(PermissionError): e.save()
        with self.assertRaises(PermissionError): e.delete()

    def test_user_detail_links_to_audit_with_unbounded_dates(self):
        r = self.client.get(f"/users/{self.hrd.pk}/"); self.assertContains(r, "/audit/?user=hrd&amp;from=&amp;to=")
        self.mk(30, user=self.hrd, action="old_action")
        self.assertIn("old_action", [a.action for a in self.client.get("/audit/?user=hrd&from=&to=").context["page"]])
