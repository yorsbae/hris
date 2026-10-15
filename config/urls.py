from django.contrib import admin
from django.urls import include, path
from django.contrib.auth import views as auth
from apps.hr import views as hr, request_views as rq, info_views as iv, emp_views as ev, doc_views as dv, import_views as iv2, leave_views as lv, schedule_views as sv, attendance_views as av7, attendance_pages as ap
from apps.core import notif_views as nv, user_views as uv, audit_views as av
from apps.poli import views as poli
from apps.core import lookup, search_views
from apps.hr import saved_filters as sf
from apps.core.dashboard import dashboard, home
from apps.core.bulk import import_view as bulk_import_view, template_view as bulk_template_view
from apps.core.scope import require_roles
from apps.core.models import Role
from apps.hr import bulk_specs as hbs, overtime as ot
from apps.core.dashboard_panels import panels as dashboard_panels
from django.conf import settings
admin.site.site_header = admin.site.site_title = "HRIS & Poliklinik — Administrasi data"
admin.site.index_title = "Administrasi data (khusus Superadmin; pekerjaan harian lewat menu aplikasi)"
urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth.LoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("api/employees/", hr.employee_list),
    path("api/employees/export/", hr.employee_export),
    path("api/employees/ids/", hr.employee_ids),
    path("api/employees/facets/", hr.employee_facets),
    path("api/saved-filters/", sf.saved_filters),
    path("api/saved-filters/<int:pk>/delete/", sf.saved_filter_delete),
    path("search/", search_views.global_search, name="global_search"),
    path("api/employees/<int:pk>/", hr.employee_detail),
    path("api/requests/<int:pk>/<str:to>/", hr.request_transition),
    path("", home),
    path("validasi/", av7.check_list, name="attcheck_list"),
    path("validasi/new/", av7.check_new, name="attcheck_new"),
    path("validasi/<int:pk>/", av7.check_detail, name="attcheck_detail"),
    path("validasi/<int:pk>/answer/", av7.check_answer, name="attcheck_answer"),
    path("validasi/<int:pk>/foto/<int:att_id>/", av7.check_attachment, name="attcheck_attachment"),
    path("validasi/<int:pk>/verify/", av7.check_verify, name="attcheck_verify"),
    path("validasi/<int:pk>/cancel/", av7.check_cancel, name="attcheck_cancel"),
    path("validasi/<int:pk>/correct/", av7.check_correct, name="attcheck_correct"),
    path("absensi/", ap.daily_list, name="att_daily"),
    path("absensi/bulanan/", ap.monthly, name="att_monthly"),
    path("absensi/karyawan/<int:pk>/", ap.employee_month, name="att_employee"),
    path("absensi/olah/", ap.reprocess, name="att_process"),
    path("absensi/minta-validasi/", ap.ask_validation, name="att_ask"),
    path("absensi/impor/", ap.import_home, name="att_import"),
    path("absensi/impor/<int:pk>/", ap.import_detail, name="att_import_detail"),
    path("absensi/pin/", ap.pins, name="att_pins"),
    path("absensi/libur/", ap.holidays, name="att_holidays"),
    path("employees/", hr.employees_page),
    path("employees/import/", iv2.employee_import, name="employee_import"),
    path("employees/import/template.csv", iv2.import_template),
    path("employees/<int:pk>/documents/new/", dv.document_new, name="document_new"),
    path("employees/<int:pk>/documents/<int:doc_id>/", dv.document_download, name="document_download"),
    path("employees/<int:pk>/documents/<int:doc_id>/delete/", dv.document_delete, name="document_delete"),
    path("employees/new/", ev.employee_new, name="employee_new"),
    path("employees/bulk/", ev.employee_bulk, name="employee_bulk"),
    path("employees/<int:pk>/", ev.employee_detail_page, name="employee_detail_page"),
    path("employees/<int:pk>/edit/", ev.employee_edit, name="employee_edit"),
    path("employees/<int:pk>/delete/", ev.employee_delete, name="employee_delete"),
    path("employees/<int:pk>/contracts/new/", ev.contract_new, name="contract_new"),
    path("schedule/", sv.schedule_week, name="schedule_week"),
    path("leave/", lv.leave_list, name="leave_list"),
    path("leave/<int:pk>/", lv.leave_detail, name="leave_detail"),
    path("master/department/import/", bulk_import_view(hbs.DEPARTMENT, require_roles(Role.HRD))),
    path("master/department/import/template.csv", bulk_template_view(hbs.DEPARTMENT, require_roles(Role.HRD))),
    path("master/position/import/", bulk_import_view(hbs.POSITION, require_roles(Role.HRD))),
    path("master/position/import/template.csv", bulk_template_view(hbs.POSITION, require_roles(Role.HRD))),
    path("master/shift/import/", bulk_import_view(hbs.SHIFT, require_roles(Role.HRD))),
    path("master/shift/import/template.csv", bulk_template_view(hbs.SHIFT, require_roles(Role.HRD))),
    path("master/shift/rotation/", ev.rotation_edit, name="rotation_edit"),
    path("master/<str:kind>/export/", ev.master_export, name="master_export"),
    path("master/<str:kind>/", ev.master_list, name="master_list"),
    path("master/<str:kind>/new/", ev.master_form, name="master_new"),
    path("master/<str:kind>/<int:pk>/", ev.master_form, name="master_edit"),
    path("password/change/", uv.PasswordChange.as_view(), name="password_change"),
    path("users/", uv.user_list, name="user_list"),
    path("users/new/", uv.user_new, name="user_new"),
    path("users/<int:pk>/", uv.user_detail, name="user_detail"),
    path("users/<int:pk>/edit/", uv.user_edit, name="user_edit"),
    path("users/<int:pk>/reset-password/", uv.user_reset_password, name="user_reset_password"),
    path("users/<int:pk>/deactivate/", uv.user_deactivate, name="user_deactivate"),
    path("users/<int:pk>/activate/", uv.user_activate, name="user_activate"),
    path("users/<int:pk>/unlock/", uv.user_unlock, name="user_unlock"),
    path("audit/", av.audit_list, name="audit_list"),
    path("audit/<int:pk>/", av.audit_detail, name="audit_detail"),
    path("notifications/", nv.notification_list),
    path("notifications/read-all/", nv.notification_read_all),
    path("notifications/<int:pk>/open/", nv.notification_open),
    path("announcements/", iv.announcement_list),
    path("announcements/new/", iv.announcement_new),
    path("announcements/<int:pk>/", iv.announcement_detail, name="announcement_detail"),
    path("announcements/<int:pk>/file/", iv.announcement_file),
    path("requests/", rq.request_list),
    path("requests/new/", rq.request_new),
    path("requests/g/lembur/new/", ot.extra_work_new, name="extra_work_new"),
    path("requests/g/lembur/rekap/", ot.extra_work_recap, name="extra_work_recap"),
    path("requests/batch/<str:batch>/<str:action>/", ot.batch_action, name="extra_work_batch"),
    path("requests/g/<slug:grp>/", rq.request_list, name="request_group"),
    path("requests/<int:pk>/", rq.request_detail, name="request_detail"),
    path("requests/<int:pk>/foto/", rq.request_attach, name="request_attach"),
    path("requests/<int:pk>/foto/<int:att_id>/", rq.request_attachment, name="request_attachment"),
    path("requests/<int:pk>/<str:action>/", rq.request_action),
    path("hrd/", include("apps.hrd.urls")),
    path("poli/", include("apps.poli.urls")),
    path("company/", include("apps.company.urls")),            # putaran 48: backend Company Profile (HRD/Superadmin)
    path("payroll/", include("apps.payroll.urls")),        # putaran 57: Payroll (akun Payroll + Superadmin; HRD hanya persetujuan)
    path("recruitment/", include("apps.company.recruit_urls")),  # putaran 48: lowongan & pelamar (HRD/Superadmin)
    path("api/lookup/employees/", lookup.employees),
    path("api/lookup/medicines/", lookup.medicines),
    path("api/lookup/diagnoses/", lookup.diagnoses),
    path("api/dashboard/", dashboard),
    path("api/dashboard/panels/", dashboard_panels),
    path("api/poli/records/", poli.record_create),
    path("api/poli/records/<int:record_id>/letter.pdf", poli.letter_pdf),
]
if settings.PUBLIC_PREVIEW:  # pratinjau situs publik di aplikasi internal (draf tampil untuk HRD/Superadmin yang login); situs publik sebenarnya: SITE_MODE=public
    urlpatterns.append(path("situs/", include("apps.company.public_urls")))
