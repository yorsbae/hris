"""Backend Rekrutmen (internal; HRD/Superadmin): /recruitment/…"""
from django.urls import path
from . import views as v

urlpatterns = [
    path("", v.recruit_hub, name="recruit_hub"),
    path("jobs/", v.crud_list, {"kind": "jobs"}, name="recruit_jobs"),
    path("jobs/new/", v.crud_new, {"kind": "jobs"}, name="recruit_job_new"),
    path("jobs/<int:pk>/", v.crud_edit, {"kind": "jobs"}, name="recruit_job_edit"),
    path("jobs/<int:pk>/delete/", v.crud_delete, {"kind": "jobs"}, name="recruit_job_delete"),
    path("applicants/", v.applicants, name="recruit_applicants"),
    path("applicants/bulk/", v.applicants_bulk, name="recruit_bulk"),
    path("applicants/<int:pk>/", v.applicant_detail, name="recruit_applicant"),
    path("applicants/<int:pk>/move/", v.applicant_move, name="recruit_move"),
    path("applicants/<int:pk>/note/", v.applicant_note, name="recruit_note"),
    path("applicants/<int:pk>/cv/", v.applicant_cv, name="recruit_cv"),
    path("applicants/<int:pk>/export/", v.applicant_export, name="recruit_export"),
    path("applicants/<int:pk>/anonymize/", v.applicant_anonymize, name="recruit_anonymize"),
]
