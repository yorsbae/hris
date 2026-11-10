from django.urls import path
from . import views as v

urlpatterns = [path("", v.index), path("export.ics", v.export_ics), path("event/new/", v.event_edit), path("event/<int:pk>/", v.event_detail),
               path("event/<int:pk>/ubah/", v.event_edit), path("event/<int:pk>/hapus/", v.event_delete)]
