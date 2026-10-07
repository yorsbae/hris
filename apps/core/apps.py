from django.apps import AppConfig
class CoreConfig(AppConfig):
    name = "apps.core"
    verbose_name = "Inti Sistem (user, audit, notifikasi)"
    def ready(self): from . import signals  # noqa
