from django.contrib import admin
from . import models
for m in (models.Department, models.Position, models.Shift, models.Employee, models.Contract, models.ChangeRequest, models.Announcement):
    admin.site.register(m)
