"""Kelola User Fingerspot (pengganti Pemetaan PIN): daftar gabungan, filter multi-departemen, nonaktif/aktif/hapus, lepas pemetaan, buat user dari karyawan, ekspor."""
from datetime import timedelta
from unittest import mock
from django.urls import reverse
from django.utils import timezone
from apps.core.models import AuditLog
from apps.hr import attendance_sync as sync
from apps.hr.models import AttendanceDevice, AttendancePin, AttendanceRaw, DeviceFingerprint, DeviceUser
from apps.hr.test_attendance import Base


class FingerspotUserTests(Base):
    def setUp(self):
        super().setUp(); self.login("hrd")
        DeviceUser.objects.create(pin="001", name="Budi"); DeviceUser.objects.create(pin="002", name="Sari"); DeviceUser.objects.create(pin="777", name="Orang Baru")
        AttendancePin.objects.create(pin="001", employee=self.e1, source="nik"); AttendancePin.objects.create(pin="002", employee=self.e2, source="nik")
        AttendancePin.objects.create(pin="009", employee=self.e_off, source="nik"); DeviceUser.objects.create(pin="009", name="Mantan")

    def rows(self, **q): return {r["pin"]: r for r in self.client.get(reverse("att_pins"), q).context["page"]}
    def post(self, **k): return self.client.post(reverse("att_pins"), k, follow=True)

    def test_combined_list_states_and_tab_counts(self):
        r = self.client.get(reverse("att_pins")); self.assertEqual(r.status_code, 200); self.assertContains(r, "Kelola user Fingerspot")
        rows = {x["pin"]: x for x in r.context["page"]}
        self.assertEqual({p: x["state"] for p, x in rows.items()}, {"001": "mapped", "002": "mapped", "009": "mapped", "777": "unmapped"})
        self.assertTrue(rows["009"]["attention"]); self.assertFalse(rows["001"]["attention"])                     # karyawan nonaktif, user masih aktif
        self.assertEqual(r.context["tabs"], {"all": 4, "unmapped": 1, "mapped": 3, "attention": 1, "inactive": 0, "nopin": 1})   # e3 belum punya PIN
        self.assertEqual(list(self.rows(tab="attention")), ["009"])

    def test_multi_department_filter(self):
        self.assertEqual(sorted(self.rows(dept=[self.d1.pk])), ["001", "009"])
        self.assertEqual(sorted(self.rows(dept=[self.d1.pk, self.d2.pk])), ["001", "002", "009"])
        self.assertEqual(sorted(self.rows(dept=[self.d2.pk, 0])), ["002", "777"])                                  # 0 = belum dipetakan (tanpa departemen)
        self.assertEqual(sorted(self.rows(dept=["abc", "%"])), ["001", "002", "009", "777"])                       # nilai sampah diabaikan, bukan galat
        self.assertEqual(sorted(self.rows(dept=[self.d1.pk], q="bud")), ["001"])

    def test_privilege_fingerprint_and_sort_filters(self):
        u = DeviceUser.objects.get(pin="002"); u.privilege = 14; u.save(); DeviceFingerprint.objects.create(user=DeviceUser.objects.get(pin="001"), finger=1, template="QQ==")
        self.assertEqual(list(self.rows(priv="admin")), ["002"]); self.assertEqual(list(self.rows(fp="ada")), ["001"]); self.assertEqual(sorted(self.rows(fp="tidak")), ["002", "009", "777"])
        self.assertEqual(list(self.client.get(reverse("att_pins"), {"sort": "name"}).context["page"])[0]["pin"], "001")                  # Budi < Mantan < Orang Baru < Sari
        self.assertEqual(list(self.client.get(reverse("att_pins"), {"sort": "pin", "desc": "1"}).context["page"])[0]["pin"], "777")

    def test_deactivate_requires_reason_and_hides_from_unmapped_queue_and_upload(self):
        self.assertContains(self.post(action="deactivate", pins=["777"], reason=""), "Alasan menonaktifkan wajib"); self.assertTrue(DeviceUser.objects.get(pin="777").active)
        self.assertContains(self.post(action="deactivate", pins=["777"], reason="PIN uji"), "1 user dinonaktifkan")
        u = DeviceUser.objects.get(pin="777"); self.assertFalse(u.active); self.assertEqual(u.deactivated_reason, "PIN uji"); self.assertIsNotNone(u.deactivated_at)
        self.assertEqual(self.rows(tab="unmapped"), {}); self.assertEqual(list(self.rows(tab="inactive")), ["777"])
        self.assertEqual(AuditLog.objects.filter(action="attendance_device_user_deactivate").count(), 1)
        dev = AttendanceDevice.objects.create(code="M1", name="M1", protocol="zk", host="127.0.0.1"); sent = {}
        class Drv:
            def users_up(self, items): sent["pins"] = sorted(i["pin"] for i in items); return len(items), 0, [], 0
        with mock.patch.object(sync, "_user_driver", return_value=Drv()), mock.patch.object(sync, "_zk_only"): sync.upload_users(dev)
        self.assertEqual(sent["pins"], ["001", "002", "009"])                                                      # 777 nonaktif tidak dikirim
        self.assertContains(self.post(action="activate", pins=["777"]), "1 user diaktifkan"); self.assertTrue(DeviceUser.objects.get(pin="777").active)

    def test_deactivate_orphan_scan_pin_without_device_user_creates_record(self):
        AttendanceRaw.objects.create(employee=None, pin="9901", device_id="FP1", punched_at=timezone.now())
        self.assertIn("9901", self.rows(tab="unmapped")); self.post(action="deactivate", pins=["9901"], reason="PIN sampah")
        self.assertFalse(DeviceUser.objects.get(pin="9901").active); self.assertNotIn("9901", self.rows(tab="unmapped")); self.assertIn("9901", self.rows(tab="inactive"))

    def test_delete_only_inactive_and_keeps_mapping_and_scans(self):
        DeviceFingerprint.objects.create(user=DeviceUser.objects.get(pin="009"), finger=0, template="QQ==")
        self.assertContains(self.post(action="delete", pins=["009"]), "nonaktifkan dulu"); self.assertTrue(DeviceUser.objects.filter(pin="009").exists())
        self.post(action="deactivate", pins=["009"], reason="Keluar"); self.assertContains(self.post(action="delete", pins=["009"]), "1 user dihapus")
        self.assertFalse(DeviceUser.objects.filter(pin="009").exists()); self.assertEqual(DeviceFingerprint.objects.count(), 0); self.assertTrue(AttendancePin.objects.filter(pin="009").exists())

    def test_deactivate_resigned_bulk(self):
        self.assertContains(self.post(action="deactivate_resigned"), "1 user milik karyawan nonaktif dinonaktifkan")
        self.assertFalse(DeviceUser.objects.get(pin="009").active); self.assertTrue(DeviceUser.objects.get(pin="001").active)
        self.assertContains(self.post(action="deactivate_resigned"), "Tidak ada user aktif milik karyawan nonaktif")

    def test_unlink_needs_reason_and_is_blocked_when_scans_exist(self):
        self.assertContains(self.post(action="unlink", pin="002", reason=""), "Alasan wajib")
        self.scan(self.e2, timezone.localdate() - timedelta(days=3), (7, 0), pin="002"); self.assertContains(self.post(action="unlink", pin="002", reason="salah"), "Gunakan Pindahkan")
        self.assertContains(self.post(action="unlink", pin="001", reason="salah petakan"), "dilepas"); self.assertFalse(AttendancePin.objects.filter(pin="001").exists())
        self.assertIn("001", self.rows(tab="unmapped"))                                                            # kembali ke antrean

    def test_create_users_for_employees_without_pin(self):
        self.assertEqual([e.nik for e in self.client.get(reverse("att_pins"), {"tab": "nopin"}).context["page"]], ["003"])
        self.assertContains(self.post(action="create_for_employees", niks=["003", "009", "NOPE"]), "1 user mesin dibuat")
        u = DeviceUser.objects.get(pin="003"); self.assertEqual((u.name, u.active), ("Dewi", True)); self.assertEqual(AttendancePin.objects.get(pin="003").source, "nik")
        self.assertEqual(self.client.get(reverse("att_pins"), {"tab": "nopin"}).context["tabs"]["nopin"], 0)
        self.assertContains(self.post(action="create_for_employees", niks=[]), "Pilih minimal satu karyawan")

    def test_create_for_employee_reactivates_existing_inactive_user(self):
        DeviceUser.objects.create(pin="003", name="Dewi", active=False, deactivated_reason="x"); self.post(action="create_for_employees", niks=["003"])
        self.assertTrue(DeviceUser.objects.get(pin="003").active)

    def test_edit_user_via_page_and_validation(self):
        self.assertContains(self.post(action="user_edit", pin="777", name="Nama Baru", privilege="14", card="55", link="0"), "diperbarui")
        u = DeviceUser.objects.get(pin="777"); self.assertEqual((u.name, u.privilege, u.card), ("Nama Baru", 14, 55)); self.assertFalse(AttendancePin.objects.filter(pin="777").exists())
        self.assertContains(self.post(action="user_edit", pin="777", name="X", password="abc"), "Password mesin")
        self.assertContains(self.post(action="user_add", pin="778", name="Baru", nik=self.e3.nik, link="1"), "PIN dipetakan ke 003")

    def test_export_respects_filters_and_is_audited(self):
        r = self.client.get(reverse("att_pins"), {"dept": [self.d2.pk], "export": "1", "format": "csv"}); self.assertEqual(r.status_code, 200)
        body = r.content.decode(); self.assertIn("Sari", body); self.assertNotIn("Budi", body); self.assertEqual(AuditLog.objects.filter(action="attendance_device_user_export").count(), 1)

    def test_back_param_is_relative_only(self):
        r = self.client.post(reverse("att_pins"), {"action": "auto_name", "back": "https://evil.example/x"}); self.assertEqual(r["Location"], reverse("att_pins"))
        r = self.client.post(reverse("att_pins"), {"action": "auto_name", "back": "tab=mapped&dept=1"}); self.assertEqual(r["Location"], reverse("att_pins") + "?tab=mapped&dept=1")

    def test_unknown_action_and_non_hrd_forbidden(self):
        self.assertContains(self.post(action="hack"), "Aksi tidak dikenal")
        for who in ("adm", "poli"): self.login(who); self.assertEqual(self.client.post(reverse("att_pins"), {"action": "delete", "pins": ["001"]}).status_code, 403)
