"""
academics/signals.py

Post-save signal: automatically create a StudentProfile when a new
EduProUser with role='student' is created.
"""

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_student_academic_profile(sender, instance, created, **kwargs):
    """
    On first save of a student user, create a blank StudentProfile.
    Importing here (not at module level) avoids circular import at app load.
    """
    if not created:
        return
    if getattr(instance, "role", None) != "student":
        return

    from academics.models import StudentProfile

    StudentProfile.objects.get_or_create(student=instance)
