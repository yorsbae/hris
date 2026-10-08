from django.contrib import admin
from django.urls import include, path
from django.contrib.auth import views as auth
from apps.hr import views as hr, request_views as rq, info_views as iv, emp_views as ev, doc_views as dv, import_views as iv2, leave_views as lv
from apps.core import notif_views as nv, user_views as uv, audit_views as av
from apps.poli import views as poli
from apps.core import lookup
from apps.core.dashboard import dashboard, home
from apps.core.dashboard_panels import panels as dashboard_panels
admin.site.site_header = admin.site.site_title = "HRIS & Poliklinik — Administrasi data"
admin.site.index_title = "Administrasi data (khusus Superadmin; pekerjaan harian lewat menu aplikasi)"
urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth.LoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("api/employees/", hr.employee_list),
    path("api/employees/<int:pk>/", hr.employee_detail),
    path("api/requests/<int:pk>/<str:to>/", hr.request_transition),
    path("", home),
    path("employees/", hr.employees_page),
    path("employees/import/", iv2.employee_import, name="employee_import"),
    path("employees/import/template.csv", iv2.import_template),
    path("employees/<int:pk>/documents/new/", dv.document_new, name="document_new"),
    path("employees/<int:pk>/documents/<int:doc_id>/", dv.document_download, name="document_download"),
    path("employees/<int:pk>/documents/<int:doc_id>/delete/", dv.document_delete, name="document_delete"),
    path("employees/new/", ev.employee_new, name="employee_new"),
    path("employees/<int:pk>/", ev.employee_detail_page, name="employee_detail_page"),
    path("employees/<int:pk>/edit/", ev.employee_edit, name="employee_edit"),
    path("employees/<int:pk>/delete/", ev.employee_delete, name="employee_delete"),
    path("employees/<int:pk>/contracts/new/", ev.contract_new, name="contract_new"),
    path("leave/", lv.leave_list, name="leave_list"),
    path("leave/<int:pk>/", lv.leave_detail, name="leave_detail"),
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
    path("requests/<int:pk>/", rq.request_detail, name="request_detail"),
    path("requests/<int:pk>/<str:action>/", rq.request_action),
    path("hrd/", include("apps.hrd.urls")),
    path("poli/", include("apps.poli.urls")),
    path("api/lookup/employees/", lookup.employees),
    path("api/lookup/medicines/", lookup.medicines),
    path("api/lookup/diagnoses/", lookup.diagnoses),
    path("api/dashboard/", dashboard),
    path("api/dashboard/panels/", dashboard_panels),
    path("api/poli/records/", poli.record_create),
    path("api/poli/records/<int:record_id>/letter.pdf", poli.letter_pdf),
]
