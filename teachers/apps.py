from django.apps import AppConfig


class TeachersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'teachers'
    verbose_name = 'Teachers'

    def ready(self):
        import teachers.signals  # noqa: F401
