from django.urls import path
from . import pages as v

urlpatterns = [
    path("", v.hub, name="poli_hub"),
    path("records/", v.record_list, name="poli_records"),
    path("records/new/", v.record_new, name="poli_record_new"),
    path("records/<int:pk>/", v.record_detail, name="poli_record_detail"),
    path("records/<int:pk>/addendum/", v.record_addendum, name="poli_record_addendum"),
    path("records/<int:pk>/referral/new/", v.referral_new, name="poli_referral_new"),
    path("employees/<int:pk>/", v.employee_history, name="poli_employee_history"),
    path("medicines/", v.medicine_list, name="poli_medicines"),
    path("medicines/new/", v.medicine_form, name="poli_medicine_new"),
    path("medicines/<int:pk>/", v.medicine_detail, name="poli_medicine_detail"),
    path("medicines/<int:pk>/edit/", v.medicine_form, name="poli_medicine_edit"),
    path("medicines/<int:pk>/stock/<str:action>/", v.medicine_stock, name="poli_medicine_stock"),
    path("diagnoses/", v.diagnosis_list, name="poli_diagnoses"),
    path("diagnoses/new/", v.diagnosis_form, name="poli_diagnosis_new"),
    path("diagnoses/<int:pk>/edit/", v.diagnosis_form, name="poli_diagnosis_edit"),
    path("referrals/", v.referral_list, name="poli_referrals"),
    path("referrals/<int:pk>/", v.referral_detail, name="poli_referral_detail"),
    path("referrals/<int:pk>/letter.pdf", v.referral_letter, name="poli_referral_letter"),
    path("referrals/<int:pk>/<str:action>/", v.referral_action, name="poli_referral_action"),
]
