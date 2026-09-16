"""
notifications/templates.py

Renders notification email bodies from per-event Django templates with a
safe generic fallback. SMS bodies come straight from notifications.messages.
"""

from django.conf import settings
from django.template.loader import render_to_string

_EMAIL_TEMPLATE_NAMES = {
    "new_material": "notifications/email/new_material.html",
    "new_assignment": "notifications/email/new_assignment.html",
    "assignment_reminder": "notifications/email/assignment_reminder.html",
    "assignment_submitted": "notifications/email/assignment_submitted.html",
    "assignment_graded": "notifications/email/assignment_graded.html",
    "new_quiz": "notifications/email/new_quiz.html",
    "quiz_reminder": "notifications/email/quiz_reminder.html",
    "quiz_result_available": "notifications/email/quiz_result_available.html",
    "lms_announcement": "notifications/email/lms_announcement.html",
}


class EmailTemplate:
    """Resolve and render the HTML email template for a notification type."""

    @classmethod
    def render(cls, notification_type, title, message, link, context, site_name=None):
        site_name = site_name or getattr(settings, "EDUPRO_SITE_NAME", "eduPro")
        template = "notifications/email/generic.html"
        named = _EMAIL_TEMPLATE_NAMES.get(notification_type)
        if named:
            try:
                render_to_string(named, {"title": "", "message": "", "site_name": site_name, "link": ""})
                template = named
            except Exception:  # template not present -> fall back to generic
                template = "notifications/email/generic.html"

        data = {
            "site_name": site_name,
            "title": title,
            "message": message,
            "link": link or "",
            "context": context or {},
        }
        return render_to_string(template, data)