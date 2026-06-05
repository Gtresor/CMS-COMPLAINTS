from django.apps import AppConfig


class ComplaintsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'complaints'

    def ready(self):
        # Wire up audit-trail signals (Section 10)
        from . import signals  # noqa: F401
