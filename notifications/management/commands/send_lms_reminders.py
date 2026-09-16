"""
send_lms_reminders command.

Sends ASSIGNMENT_REMINDER / QUIZ_REMINDER notifications to students who have
not yet submitted/attempted, at configurable windows before the deadline.

Run on a schedule (e.g. every hour via Render cron / Windows Task Scheduler):

    python manage.py send_lms_reminders

Windows are configured in settings:
    LMS_ASSIGNMENT_REMINDER_DAYS = "7,3,1"
    LMS_QUIZ_REMINDER_HOURS     = "24,1"
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from notifications.models import NotificationType
from notifications.services import NotificationService


class Command(BaseCommand):
    help = "Send assignment and quiz deadline reminders to enrolled students."

    def handle(self, *args, **options):
        from academics.models import Enrolment
        from teachers.models import Assignment, AssignmentSubmission, Quiz, QuizAttempt

        now = timezone.now()
        sent_count = 0

        # --- Assignments -----------------------------------------------------
        for days in settings.LMS_ASSIGNMENT_REMINDER_DAYS:
            window_start = now + timezone.timedelta(hours=days * 24)
            window_end = now + timezone.timedelta(hours=days * 24 + 1)

            assignments = Assignment.objects.filter(
                status="published",
                is_active=True,
                due_date__gte=window_start,
                due_date__lte=window_end,
            )
            for assignment in assignments:
                submitted_ids = set(
                    AssignmentSubmission.all_objects.filter(
                        assignment=assignment
                    ).values_list("student_id", flat=True)
                )
                student_ids = Enrolment.objects.filter(
                    offering=assignment.offering, is_active=True
                ).values_list("student_id", flat=True)
                pending_ids = [sid for sid in student_ids if sid not in submitted_ids]
                if not pending_ids:
                    continue

                from django.contrib.auth import get_user_model

                students = get_user_model().objects.filter(pk__in=pending_ids)
                records = NotificationService.send(
                    notification_type=NotificationType.ASSIGNMENT_REMINDER,
                    recipients=students,
                    context={
                        "offering": assignment.offering,
                        "assignment_title": assignment.title,
                        "due_date": assignment.due_date,
                    },
                    obj=assignment,
                    idempotency_key=f"assignment_reminder:{assignment.pk}:{days}d",
                )
                sent_count += len(records)

        # --- Quizzes ---------------------------------------------------------
        for hours in settings.LMS_QUIZ_REMINDER_HOURS:
            window_start = now + timezone.timedelta(hours=hours)
            window_end = now + timezone.timedelta(hours=hours + 1)

            quizzes = Quiz.objects.filter(
                is_published=True,
                is_active=True,
                end_datetime__gte=window_start,
                end_datetime__lte=window_end,
            )
            for quiz in quizzes:
                attempted_ids = set(
                    QuizAttempt.objects.filter(quiz=quiz, is_complete=True).values_list(
                        "student_id", flat=True
                    )
                )
                student_ids = Enrolment.objects.filter(
                    offering=quiz.offering, is_active=True
                ).values_list("student_id", flat=True)
                pending_ids = [sid for sid in student_ids if sid not in attempted_ids]
                if not pending_ids:
                    continue

                from django.contrib.auth import get_user_model

                students = get_user_model().objects.filter(pk__in=pending_ids)
                records = NotificationService.send(
                    notification_type=NotificationType.QUIZ_REMINDER,
                    recipients=students,
                    context={
                        "offering": quiz.offering,
                        "quiz_title": quiz.title,
                        "end_datetime": quiz.end_datetime,
                    },
                    obj=quiz,
                    idempotency_key=f"quiz_reminder:{quiz.pk}:{hours}h",
                )
                sent_count += len(records)

        self.stdout.write(self.style.SUCCESS(f"LMS reminders queued: {sent_count}"))