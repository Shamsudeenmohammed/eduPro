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
    NotificationType.RESULT_SUBMITTED: "Results Submitted",
    NotificationType.RESULT_APPROVED: "Results Approved",
    NotificationType.RESULT_REJECTED: "Results Rejected",
    NotificationType.RESULT_PUBLISHED: "Results Published",
    NotificationType.RESULT_REVISED: "Results Returned for Revision",
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


def _result_view_link(ctx):
    sheet_pk = ctx.get("sheet_pk")
    if sheet_pk:
        from django.urls import reverse

        try:
            return reverse("teachers:result_sheet_view", kwargs={"sheet_pk": sheet_pk})
        except Exception:  # noqa: BLE001
            return ""
    return ctx.get("link", "")


def _revision_suffix(context):
    revision = context.get("revision")
    return f" (revision {revision})" if revision else ""


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
    NotificationType.RESULT_SUBMITTED: lambda c: (
        f"Results submitted for review: {_course_code(c)}",
        f"{c.get('submitted_by_name', 'A teacher')} submitted results for {_course_code(c)}. "
        f"They are awaiting your review.",
        _result_view_link(c),
    ),
    NotificationType.RESULT_APPROVED: lambda c: (
        f"Results approved: {_course_code(c)}",
        f"The result sheet for {_course_code(c)} has been approved and locked. "
        f"Students will be notified once the results are published.",
        _result_view_link(c),
    ),
    NotificationType.RESULT_REJECTED: lambda c: (
        f"Results rejected: {_course_code(c)}",
        f"The result sheet for {_course_code(c)} was rejected for correction. "
        f"{c.get('reason', 'No reason given.')}",
        _result_view_link(c),
    ),
    NotificationType.RESULT_PUBLISHED: lambda c: (
        f"Your results are ready: {_course_code(c)}",
        f"Results for {_course_code(c)} have been published{_revision_suffix(c)}. "
        f"View them from the results page.",
        c.get("student_link") or _result_view_link(c),
    ),
    NotificationType.RESULT_REVISED: lambda c: (
        f"Results returned for correction: {_course_code(c)}",
        f"The published results for {_course_code(c)} have been returned to draft. "
        f"Reason: {c.get('reason', 'No reason given.')}",
        _result_view_link(c),
    ),

    # ── Admissions ───────────────────────────────────────────────────────────
    # Context keys: ref, program, status, action, reason, amount, decision,
    # offer_number, expiry, student_number, missing.
    NotificationType.APPLICATION_SUBMITTED: lambda c: (
        f"Application received — {c.get('ref', '')}",
        f"We have received your application{c.get('program_suffix', '')}. "
        f"Reference {c.get('ref', '')}. {c.get('action', 'Track its progress from your applicant portal.')}",
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_DRAFT_SAVED: lambda c: (
        f"Application draft saved — {c.get('ref', '')}",
        f"Your application has been saved as a draft. Sign in to finish it and "
        f"submit. Reference {c.get('ref', '')}.",
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_PAYMENT_CONFIRMED: lambda c: (
        "Application fee received",
        f"We have received your application fee of {c.get('amount', '')}. "
        f"Your application {c.get('ref', '')} will now proceed to review.",
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_CORRECTION_REQUESTED: lambda c: (
        f"Action needed on application {c.get('ref', '')}",
        f"The admissions team has requested corrections to your application. "
        f"Reason: {c.get('reason', 'No reason given.')} "
        f"Please update and resubmit.",
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_UNDER_REVIEW: lambda c: (
        f"Application under review — {c.get('ref', '')}",
        f"Your application {c.get('ref', '')} is now being reviewed by the "
        f"admissions team. No action is needed from you right now.",
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_SHORTLISTED: lambda c: (
        f"You have been shortlisted — {c.get('ref', '')}",
        f"Congratulations. Your application {c.get('ref', '')}"
        f"{c.get('program_suffix', '')} has been shortlisted. Watch your email "
        f"for the next steps.",
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_INTERVIEW_REQUIRED: lambda c: (
        f"Interview required — {c.get('ref', '')}",
        f"An interview is required as part of your application {c.get('ref', '')}. "
        f"{c.get('reason', 'You will receive the details shortly.')}",
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_DECISION_MADE: lambda c: (
        f"Admission decision — {c.get('ref', '')}",
        f"A decision has been made on your application {c.get('ref', '')}: "
        f"{c.get('decision', 'Pending')}. {c.get('action', '')}",
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_OFFER_ISSUED: lambda c: (
        f"Admission offer issued — {c.get('offer_number', '')}",
        f"You have been offered admission{c.get('program_suffix', '')}. "
        f"Offer {c.get('offer_number', '')} is valid until {c.get('expiry', 'the expiry date')}. "
        f"Please accept or decline it from your applicant portal."
        + (f" Conditions: {c['conditions']}" if c.get("conditions") else ""),
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_OFFER_ACCEPTED: lambda c: (
        f"Offer accepted — {c.get('offer_number', '')}",
        f"Thank you. Your acceptance of offer {c.get('offer_number', '')} has been "
        f"recorded. Our team will complete your registration and contact you with "
        f"your student details.",
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_OFFER_DECLINED: lambda c: (
        f"Offer declined — {c.get('offer_number', '')}",
        f"We have recorded your decision to decline offer {c.get('offer_number', '')}. "
        f"You are welcome to apply again in a future admission cycle.",
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_OFFER_EXPIRING: lambda c: (
        f"Offer expiring soon — {c.get('offer_number', '')}",
        f"Offer {c.get('offer_number', '')} expires on {c.get('expiry', 'soon')}. "
        f"Please accept or decline it before then to keep your place.",
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_CONVERTED: lambda c: (
        f"Welcome to eduPro — {c.get('student_number', '')}",
        f"Your admission is complete. Your student number is "
        f"{c.get('student_number', '')}. Sign in with your student number and the "
        f"password you were given to access your student portal.",
        c.get("link") or "/students/dashboard/",
    ),
    NotificationType.APPLICATION_DOCUMENT_UPLOADED: lambda c: (
        f"Document received — {c.get('ref', '')}",
        f"Thank you. We received your {c.get('document', 'document')} and it is "
        f"awaiting verification.",
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_DOCUMENT_VERIFIED: lambda c: (
        f"Document verified — {c.get('document', 'document')}",
        f"Your {c.get('document', 'document')} has been verified.",
        c.get("link") or "/portal/applicant/",
    ),
    NotificationType.APPLICATION_DOCUMENT_REJECTED: lambda c: (
        f"Document needs attention — {c.get('document', 'document')}",
        f"Your {c.get('document', 'document')} was not accepted. "
        f"Reason: {c.get('reason', 'No reason given.')} "
        f"Please upload a replacement from your applicant portal.",
        c.get("link") or "/portal/applicant/",
    ),
}


def build(notification_type, context=None):
    """Return (title, text, link) for a notification type."""
    context = context or {}
    title, message, link = BUILDERS[notification_type](context)
    return title, message, link