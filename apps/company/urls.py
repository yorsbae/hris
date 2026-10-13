"""Backend Company Profile (internal; HRD/Superadmin): /company/…"""
from django.urls import path
from . import views as v

urlpatterns = [
    path("", v.hub, name="company_hub"),
    path("settings/", v.site_settings, name="company_settings"),
    path("messages/", v.message_list, name="company_messages"),
    path("messages/<int:pk>/", v.message_detail, name="company_message"),
    path("products/<int:pk>/images/add/", v.product_image_add, name="company_product_image_add"),
    path("products/<int:pk>/images/<int:image_id>/delete/", v.product_image_delete, name="company_product_image_delete"),
    path("products/<int:pk>/colors/add/", v.product_color_add, name="company_product_color_add"),
    path("products/<int:pk>/colors/<int:color_id>/delete/", v.product_color_delete, name="company_product_color_delete"),
    path("<str:kind>/", v.crud_list, name="company_list"),
    path("<str:kind>/new/", v.crud_new, name="company_new"),
    path("<str:kind>/<int:pk>/", v.crud_edit, name="company_edit"),
    path("<str:kind>/<int:pk>/delete/", v.crud_delete, name="company_delete"),
]
