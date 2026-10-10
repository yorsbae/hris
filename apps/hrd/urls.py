from django.urls import path
from apps.core.bulk import import_view as bulk_import_view, template_view as bulk_template_view
from apps.core.models import Role
from apps.core.scope import require_roles
from . import views as v
from .bulk_specs import BPJS_DEDUCTION, UNIFORM_PURCHASE
from . import uniform_views as uv

urlpatterns = [
    path("", v.hub, name="hrd_hub"),
    path("bpjs/", v.bpjs_list, name="hrd_bpjs"),
    path("bpjs/deductions/", v.bpjs_deductions, name="hrd_bpjs_deductions"),
    path("bpjs/deductions/kes/", v.bpjs_deductions, {"scheme": "kes"}, name="hrd_bpjs_deductions_kes"),  # submenu sidebar BPJS → Kesehatan
    path("bpjs/deductions/tk/", v.bpjs_deductions, {"scheme": "tk"}, name="hrd_bpjs_deductions_tk"),  # submenu sidebar BPJS → Ketenagakerjaan
    path("bpjs/deductions/new/", v.bpjs_deduction_new, name="hrd_bpjs_deduction_new"),
    path("bpjs/deductions/import/", bulk_import_view(BPJS_DEDUCTION, require_roles(Role.HRD)), name="hrd_bpjs_deduction_import"),
    path("bpjs/deductions/import/template.csv", bulk_template_view(BPJS_DEDUCTION, require_roles(Role.HRD))),
    path("bpjs/<int:pk>/", v.bpjs_detail, name="hrd_bpjs_detail"),
    path("uniforms/", uv.uniforms, name="hrd_uniforms"),                                                    # satu halaman: tab Stok (bawaan) · Pembelian · Riwayat · Master
    path("uniforms/new/", uv.uniform_new, name="hrd_uniform_new"),
    path("uniforms/stock/", uv.uniforms, {"tab": "riwayat"}, name="hrd_uniform_stock"),                      # alamat lama kartu stok → tab Riwayat
    path("uniforms/stock/in/", uv.uniforms, {"tab": "stok", "mode": "in"}, name="hrd_uniform_stock_in"),      # alamat lama barang masuk → form di tab Stok
    path("uniforms/stock/adjust/", uv.uniforms, {"tab": "stok", "mode": "adjust"}, name="hrd_uniform_stock_adjust"),
    path("uniforms/master/", uv.uniforms, {"tab": "master"}, name="hrd_uniform_master"),                      # alamat lama master → tab Master
    path("uniforms/import/", bulk_import_view(UNIFORM_PURCHASE, require_roles(Role.HRD)), name="hrd_uniform_import"),
    path("uniforms/import/template.csv", bulk_template_view(UNIFORM_PURCHASE, require_roles(Role.HRD))),
    path("uniforms/<int:pk>/", uv.uniform_detail, name="hrd_uniform_detail"),
    path("uniforms/<int:pk>/void/", uv.uniform_void, name="hrd_uniform_void"),
    path("uniforms/<int:pk>/mark/", uv.uniform_mark, name="hrd_uniform_mark"),
    path("aids/", v.aid_list, name="hrd_aids"),
    path("aids/new/", v.aid_new, name="hrd_aid_new"),
    path("aids/<int:pk>/edit/", v.aid_edit, name="hrd_aid_edit"),
    path("aids/<int:pk>/delete/", v.aid_delete, name="hrd_aid_delete"),
    path("maternity/", v.maternity_list, name="hrd_maternity"),
    path("maternity/new/", v.maternity_new, name="hrd_maternity_new"),
    path("maternity/<int:pk>/", v.maternity_detail, name="hrd_maternity_detail"),
    path("maternity/<int:pk>/edit/", v.maternity_edit, name="hrd_maternity_edit"),
    path("maternity/<int:pk>/<str:action>/", v.maternity_action, name="hrd_maternity_action"),
    path("projects/", v.project_list, name="hrd_projects"),
    path("projects/new/", v.project_form, name="hrd_project_new"),
    path("projects/<int:pk>/", v.project_detail, name="hrd_project_detail"),
    path("projects/<int:pk>/edit/", v.project_form, name="hrd_project_edit"),
    path("projects/<int:pk>/work/new/", v.project_work_form, name="hrd_project_work_new"),
    path("projects/<int:pk>/work/<int:work_id>/edit/", v.project_work_form, name="hrd_project_work_edit"),
    path("projects/<int:pk>/work/<int:work_id>/delete/", v.project_work_delete, name="hrd_project_work_delete"),
    path("catering/", v.catering_list, name="hrd_catering"),
    path("catering/new/", v.catering_form, name="hrd_catering_new"),
    path("catering/<int:pk>/edit/", v.catering_form, name="hrd_catering_edit"),
    path("catering/<int:pk>/delete/", v.catering_delete, name="hrd_catering_delete"),
    path("warnings/", v.warning_list, name="hrd_warnings"),
    path("warnings/new/", v.warning_new, name="hrd_warning_new"),
    path("warnings/<int:pk>/", v.warning_detail, name="hrd_warning_detail"),
    path("warnings/<int:pk>/revoke/", v.warning_revoke, name="hrd_warning_revoke"),
    path("warnings/<int:pk>.pdf", v.warning_pdf, name="hrd_warning_pdf"),
]
