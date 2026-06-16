"""Global template context."""

from django.conf import settings


def global_context(request):
    institution = None
    try:
        from academics.models import Institution
        institution = Institution.objects.first()
    except Exception:
        pass

    unread_notifications = 0
    unread_messages = 0
    if request.user.is_authenticated:
        if getattr(request.user, "is_student", False):
            from students.models import StudentNotification
            unread_notifications = StudentNotification.objects.filter(
                student=request.user, is_read=False
            ).count()
        try:
            from messaging.models import Message
            unread_messages = Message.objects.filter(
                recipient=request.user, is_read=False
            ).count()
        except Exception:
            unread_messages = 0

    return {
        "site_name": institution.name if institution else getattr(
            settings, "EDUPRO_SITE_NAME", "eduPro"
        ),
        "institution": institution,
        "unread_notifications": unread_notifications,
        "unread_messages": unread_messages,
    }
