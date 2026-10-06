from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth
from apps.hr import views as hr
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
    path("api/dashboard/", dashboard),
    path("api/poli/records/", poli.record_create),
    path("api/poli/records/<int:record_id>/letter.pdf", poli.letter_pdf),
]
