from django.urls import path
from . import views as v

urlpatterns = [
    path("", v.hub, name="hrd_hub"),
    path("bpjs/", v.bpjs_list, name="hrd_bpjs"),
    path("bpjs/<int:pk>/", v.bpjs_detail, name="hrd_bpjs_detail"),
    path("aids/", v.aid_list, name="hrd_aids"),
    path("aids/new/", v.aid_new, name="hrd_aid_new"),
    path("aids/<int:pk>/", v.aid_detail, name="hrd_aid_detail"),
    path("aids/<int:pk>/edit/", v.aid_edit, name="hrd_aid_edit"),
    path("aids/<int:pk>/<str:action>/", v.aid_action, name="hrd_aid_action"),
    path("maternity/", v.maternity_list, name="hrd_maternity"),
    path("maternity/new/", v.maternity_new, name="hrd_maternity_new"),
    path("maternity/<int:pk>/", v.maternity_detail, name="hrd_maternity_detail"),
    path("maternity/<int:pk>/edit/", v.maternity_edit, name="hrd_maternity_edit"),
    path("maternity/<int:pk>/<str:action>/", v.maternity_action, name="hrd_maternity_action"),
    path("projects/", v.project_list, name="hrd_projects"),
    path("projects/new/", v.project_form, name="hrd_project_new"),
    path("projects/<int:pk>/", v.project_detail, name="hrd_project_detail"),
    path("projects/<int:pk>/edit/", v.project_form, name="hrd_project_edit"),
    path("projects/<int:pk>/logs/new/", v.project_log_form, name="hrd_project_log_new"),
    path("projects/<int:pk>/logs/<int:log_id>/edit/", v.project_log_form, name="hrd_project_log_edit"),
    path("catering/", v.catering_list, name="hrd_catering"),
    path("catering/new/", v.catering_form, name="hrd_catering_new"),
    path("catering/<int:pk>/edit/", v.catering_form, name="hrd_catering_edit"),
    path("catering/<int:pk>/<str:action>/", v.catering_action, name="hrd_catering_action"),
]
