from django.urls import path

from apps.core.bulk import import_view as bulk_import_view, template_view as bulk_template_view
from apps.core.models import Role
from apps.core.scope import require_roles
from . import bulk_specs as bs, tax_pages as tp, views as v

only = require_roles(Role.PAYROLL)   # Superadmin lolos otomatis

urlpatterns = [
    path("", v.home, name="pay_home"),
    path("periods/", v.periods_list, name="pay_periods"),
    path("runs/", v.runs, name="pay_runs"),
    path("runs/<int:pk>/", v.run_detail, name="pay_run"),
    path("runs/<int:pk>/slips.pdf", v.run_pdf, name="pay_run_pdf"),
    path("runs/<int:pk>/laporan.xlsx", v.run_report, name="pay_run_report"),
    path("runs/<int:pk>/<str:action>/", v.run_action, name="pay_run_action"),
    path("slips/<int:pk>/", v.slip, name="pay_slip"),
    path("slips/<int:pk>/slip.pdf", v.slip_pdf, name="pay_slip_pdf"),
    path("profiles/", v.profiles, name="pay_profiles"),
    path("profiles/new/", v.profile_new, name="pay_profile_new"),
    path("profiles/import/", bulk_import_view(bs.PROFILE, only), name="pay_profile_import"),
    path("profiles/import/template.csv", bulk_template_view(bs.PROFILE, only)),
    path("profiles/<int:pk>/", v.profile_detail, name="pay_profile_detail"),
    path("scale/", v.scale, name="pay_scale"),
    path("scale/new/", v.scale_new, name="pay_scale_new"),
    path("scale/import/", bulk_import_view(bs.SCALE, only), name="pay_scale_import"),
    path("scale/import/template.csv", bulk_template_view(bs.SCALE, only)),
    path("params/", v.params, name="pay_params"),
    path("params/new/", v.param_new, name="pay_param_new"),
    path("rates/", v.rates, name="pay_rates"),
    path("rates/period/new/", v.meal_period_new, name="pay_meal_period_new"),
    path("rates/meal/new/", v.meal_rate_new, name="pay_meal_rate_new"),
    path("rates/meal/shifts/", v.meal_shifts, name="pay_meal_shifts"),
    path("rates/overtime/new/", v.ot_rate_new, name="pay_ot_rate_new"),
    path("rates/education/new/", v.edu_allow_new, name="pay_edu_allow_new"),
    path("adjustments/", v.adjustments, name="pay_adjustments"),
    path("adjustments/new/", v.adjustment_new, name="pay_adjustment_new"),
    path("adjustments/import/", bulk_import_view(bs.ADJUSTMENT, only), name="pay_adjustment_import"),
    path("adjustments/import/template.csv", bulk_template_view(bs.ADJUSTMENT, only)),
    path("deductions/", v.deductions, name="pay_deductions"),
    path("deductions/new/", v.deduction_new, name="pay_deduction_new"),
    path("pph21/tarif/", v.bracket_new, name="pay_bracket_new"),
    path("thr/", tp.thr_page, name="pay_thr"),
    path("pph21/", tp.tax_page, name="pay_tax"),
    path("tali-asih/", v.tali_asih, name="pay_tali"),
    path("tali-asih/rules/new/", v.tali_rule_new, name="pay_tali_rule_new"),
    path("void/<str:kind>/<int:pk>/", v.void, name="pay_void"),
]
