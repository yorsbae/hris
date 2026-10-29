"""Alur kerja per modul: setiap status yang ada di model punya langkah + kalimat giliran; peran menentukan "Giliran Anda"."""
from django.template import Context, Template
from django.test import SimpleTestCase

from apps.core.flow import FLOWS, describe


class _U:
    def __init__(self, role): self.role = role


def render(kind, status, role):
    return Template('{% load ui %}{% flow kind status %}').render(Context({"kind": kind, "status": status, "user": _U(role)}))


class FlowTests(SimpleTestCase):
    def test_every_state_is_consistent(self):
        for kind, (labels, states) in FLOWS.items():
            for st, (done, actor, what, mode) in states.items():
                self.assertIn(mode, ("open", "done", "off", "wait"), (kind, st))
                self.assertTrue(0 <= done <= len(labels), (kind, st))
                d = describe(kind, st, "hrd")
                self.assertEqual(len(d["steps"]), len(labels))
                if mode == "open": self.assertIsNotNone(actor, (kind, st))
                if mode == "done": self.assertTrue(all(c == "done" for _, c in d["steps"]), (kind, st))

    def test_status_values_match_models(self):
        from apps.company.models import Stage
        from apps.hr.models import ChangeRequest, StaffOrder
        from apps.poli.models import Referral, PatientVisit, PartnerBill, PurchaseOrder
        self.assertEqual({v for v in FLOWS["applicant"][1]}, {s.value for s in Stage})
        self.assertEqual(set(FLOWS["order"][1]), {s.value for s in StaffOrder.Status})
        self.assertEqual(set(FLOWS["visit"][1]), {k for k, _ in PatientVisit.STATUSES})
        self.assertEqual(set(FLOWS["referral"][1]), {k for k, _ in Referral.STATUSES})
        self.assertEqual(set(FLOWS["bill"][1]), {k for k, _ in PartnerBill.STATUSES})
        self.assertEqual(set(FLOWS["purchase"][1]), {k for k, _ in PurchaseOrder.STATUS})
        self.assertTrue(set(FLOWS["request"][1]) >= set(ChangeRequest.FLOW) | {t for v in ChangeRequest.FLOW.values() for t in v})

    def test_turn_depends_on_role(self):
        self.assertIn("Giliran Anda: setujui atau tolak", render("request", "pending", "hrd"))
        self.assertIn('class="note now"', render("request", "pending", "hrd"))
        self.assertIn("Menunggu HRD: setujui atau tolak", render("request", "pending", "dept_admin"))
        self.assertIn('class="note wait"', render("request", "pending", "dept_admin"))
        self.assertIn("Menunggu Poli", render("bill", "diterima", "hrd"))

    def test_terminal_states(self):
        out = render("request", "rejected", "hrd")
        self.assertIn('class="off"', out); self.assertIn("ditolak HRD", out)
        self.assertIn('class="note done"', render("bill", "dibayar", "poli"))

    def test_unknown_status_renders_nothing(self):
        self.assertEqual(render("request", "nope", "hrd"), "")
        self.assertEqual(render("nope", "x", "hrd"), "")
