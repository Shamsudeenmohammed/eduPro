from decimal import Decimal

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import StudentRetakeFee, get_retake_fee_amount


@receiver(post_save, sender="teachers.StudentResult")
def create_retake_fee_on_fail(sender, instance, **kwargs):
    """Auto-create a StudentRetakeFee record when a student fails (grade=F)."""
    if instance.grade != "F":
        return

    enrolment = instance.enrolment
    student = enrolment.student
    course = enrolment.offering.course
    session = enrolment.offering.semester.session

    if StudentRetakeFee.objects.filter(
        student=student, course=course, session=session
    ).exists():
        return

    amount = get_retake_fee_amount(session)
    if amount == Decimal("0"):
        from academics.models import Semester
        current = Semester.objects.filter(is_current=True).select_related("session").first()
        if current:
            amount = get_retake_fee_amount(current.session)

    if amount > Decimal("0"):
        StudentRetakeFee.objects.create(
            student=student,
            course=course,
            session=session,
            amount=amount,
        )
