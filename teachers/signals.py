"""
teachers/signals.py

Auto-creates a TeacherProfile when a new EduProUser with role='teacher' is saved.
"""

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_teacher_profile(sender, instance, created, **kwargs):
    if not created:
        return
    if getattr(instance, "role", None) != "teacher":
        return
    from teachers.models import TeacherProfile
    TeacherProfile.objects.get_or_create(teacher=instance)
