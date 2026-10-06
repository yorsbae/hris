from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth
from apps.hr import views as hr, request_views as rq, info_views as iv
from apps.core import notif_views as nv
from apps.poli import views as poli
from apps.core.dashboard import dashboard, home
urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth.LoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("api/employees/", hr.employee_list),
    path("api/employees/<int:pk>/", hr.employee_detail),
    path("api/requests/<int:pk>/<str:to>/", hr.request_transition),
    path("", home),
    path("employees/", hr.employees_page),
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
    path("api/dashboard/", dashboard),
    path("api/poli/records/", poli.record_create),
    path("api/poli/records/<int:record_id>/letter.pdf", poli.letter_pdf),
]
