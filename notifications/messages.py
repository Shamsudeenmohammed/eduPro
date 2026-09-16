"""
notifications/messages.py

Builds the (title, text, link) triplet for each LMS notification type from a
small context dict. SMS bodies reuse `text` (kept concise).
"""

from .models import NotificationType

TYPE_LABELS = {
    NotificationType.NEW_MATERIAL: "New Material",
    NotificationType.NEW_ASSIGNMENT: "New Assignment",
    NotificationType.ASSIGNMENT_REMINDER: "Assignment Reminder",
    NotificationType.ASSIGNMENT_SUBMITTED: "Assignment Submitted",
    NotificationType.ASSIGNMENT_GRADED: "Graded",
    NotificationType.NEW_QUIZ: "New Quiz",
    NotificationType.QUIZ_REMINDER: "Quiz Reminder",
    NotificationType.QUIZ_RESULT_AVAILABLE: "Quiz Result",
    NotificationType.LMS_ANNOUNCEMENT: "Announcement",
}

DEADLINE_FORMAT = "%b %d, %I:%M %p"


def _fmt_dt(value):
    if not value:
        return ""
    from django.utils import timezone

    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    try:
        return value.astimezone(timezone.get_current_timezone()).strftime(DEADLINE_FORMAT)
    except Exception:
        return value.strftime(DEADLINE_FORMAT)


def _course_code(ctx, offering=None):
    offering = offering or ctx.get("offering")
    if offering is not None:
        return getattr(getattr(offering, "course", None), "code", None) or getattr(offering, "code", "")
    return ctx.get("course_code", "")


def _assignment_link(ctx):
    offering = ctx.get("offering")
    if offering is not None:
        from django.urls import reverse

        try:
            return reverse("students:assignment_list", kwargs={"offering_pk": offering.pk})
        except Exception:  # noqa: BLE001
            return ""
    return ctx.get("link", "")


def _quiz_link(ctx):
    offering = ctx.get("offering")
    if offering is not None:
        from django.urls import reverse

        try:
            return reverse("students:quiz_list", kwargs={"offering_pk": offering.pk})
        except Exception:  # noqa: BLE001
            return ""
    return ctx.get("link", "")


BUILDERS = {
    NotificationType.NEW_MATERIAL: lambda c: (
        f"New material in {_course_code(c)}",
        f"{c.get('material_title', 'A new material')} is now available for {_course_code(c)}. "
        f"Open the course materials page to view and download it.",
        _assignment_link(c),
    ),
    NotificationType.NEW_ASSIGNMENT: lambda c: (
        f"New assignment: {c.get('assignment_title', 'Untitled')}",
        f"{c.get('assignment_title', 'An assignment')} posted for {_course_code(c)}. "
        f"Due {_fmt_dt(c.get('due_date'))}. Submit before the deadline to avoid late penalties.",
        _assignment_link(c),
    ),
    NotificationType.ASSIGNMENT_REMINDER: lambda c: (
        f"Reminder: {c.get('assignment_title', 'assignment')} due soon",
        f"{c.get('assignment_title', 'Your assignment')} for {_course_code(c)} is due "
        f"{_fmt_dt(c.get('due_date'))}. Submit now — you have not submitted yet.",
        _assignment_link(c),
    ),
    NotificationType.ASSIGNMENT_SUBMITTED: lambda c: (
        f"Assignment submitted: {c.get('assignment_title', 'Untitled')}",
        f"Your submission for {c.get('assignment_title', 'the assignment')} in "
        f"{_course_code(c)} was recorded successfully.",
        _assignment_link(c),
    ),
    NotificationType.ASSIGNMENT_GRADED: lambda c: (
        f"{c.get('assignment_title', 'Assignment')} graded",
        f"You scored {c.get('score', '')}/{c.get('total', '')} "
        f"{c.get('remark_label', '')} in {_course_code(c)}. Review the breakdown on the assignment page.",
        _assignment_link(c),
    ),
    NotificationType.NEW_QUIZ: lambda c: (
        f"New quiz: {c.get('quiz_title', 'Untitled')}",
        f"A new quiz '{c.get('quiz_title', '')}' is available for {_course_code(c)}. "
        f"Opens {_fmt_dt(c.get('start_datetime'))}, closes {_fmt_dt(c.get('end_datetime'))}. "
        f"You have {c.get('max_attempts', 1)} attempt(s).",
        _quiz_link(c),
    ),
    NotificationType.QUIZ_REMINDER: lambda c: (
        f"Reminder: {c.get('quiz_title', 'quiz')} closes soon",
        f"'{c.get('quiz_title', 'Your quiz')}' for {_course_code(c)} closes "
        f"{_fmt_dt(c.get('end_datetime'))}. Take it now — you have not attempted it yet.",
        _quiz_link(c),
    ),
    NotificationType.QUIZ_RESULT_AVAILABLE: lambda c: (
        f"Quiz result: {c.get('quiz_title', 'Untitled')}",
        f"Your result for '{c.get('quiz_title', 'the quiz')}' in {_course_code(c)} is available. "
        f"Score: {c.get('score', '')}/{c.get('total', '')}.",
        _quiz_link(c),
    ),
    NotificationType.LMS_ANNOUNCEMENT: lambda c: (
        f"Announcement: {c.get('subject', 'Course announcement')}",
        c.get("body", "") or f"New announcement for {_course_code(c)}.",
        c.get("link", ""),
    ),
}


def build(notification_type, context=None):
    """Return (title, text, link) for a notification type."""
    context = context or {}
    title, message, link = BUILDERS[notification_type](context)
    return title, message, link