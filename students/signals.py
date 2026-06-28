"""
students/signals.py

Signals for the students app:
  1. When a CourseRegistrationRequest is APPROVED → create academics.Enrolment
  2. When a new LectureMaterial is published → notify enrolled students
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


@receiver(post_save, sender="students.CourseRegistrationRequest")
def auto_enrol_on_approval(sender, instance, **kwargs):
    """Create an Enrolment when a registration request is approved."""
    if instance.status != "approved":
        return
    from academics.models import Enrolment
    defaults = {
        "status": Enrolment.EnrolmentStatus.ACTIVE,
        "is_active": True,
        "is_retake": instance.is_retake,
    }
    if instance.is_retake:
        original = (
            Enrolment.objects
            .filter(student=instance.student, offering__course=instance.offering.course)
            .exclude(is_retake=True)
            .order_by("-enrolled_at")
            .first()
        )
        if original:
            defaults["original_enrolment"] = original
    Enrolment.objects.get_or_create(
        student=instance.student,
        offering=instance.offering,
        defaults=defaults,
    )


@receiver(post_save, sender="teachers.LectureMaterial")
def notify_students_new_material(sender, instance, created, **kwargs):
    """Notify enrolled students when a new published material is added."""
    if not created or not instance.is_published:
        return
    from academics.models import Enrolment
    from students.models import StudentNotification
    enrolments = Enrolment.objects.filter(
        offering=instance.offering, is_active=True, status="active"
    ).select_related("student")
    notifications = [
        StudentNotification(
            student=e.student,
            category="material",
            title=f"New material: {instance.title}",
            message=(
                f"A new {instance.get_material_type_display()} has been uploaded "
                f"for {instance.offering.course.code}."
            ),
            link=f"/students/courses/{instance.offering.pk}/materials/",
        )
        for e in enrolments
    ]
    if notifications:
        StudentNotification.objects.bulk_create(notifications, ignore_conflicts=True)
