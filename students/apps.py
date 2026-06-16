from django.apps import AppConfig


class StudentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'students'
    verbose_name = 'Students'

    def ready(self):
        import students.signals  # noqa: F401
