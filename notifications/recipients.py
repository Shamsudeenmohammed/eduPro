"""
notifications/recipients.py

Recipient resolution helpers: normalise single users / querysets / lazy
enrolment queries into an iterable of concrete user instances.
"""

from django.contrib.auth import get_user_model


class EnrolmentQuery:
    """Lazy iterable of active enrolled students for an offering."""

    def __init__(self, offering):
        self.offering = offering

    def users(self):
        from academics.models import Enrolment

        ids = Enrolment.objects.filter(
            offering=self.offering, is_active=True
        ).values_list("student_id", flat=True)
        return get_user_model().objects.filter(pk__in=list(ids))


def as_users(recipients):
    """Return an iterable of User instances (queryset-safe, deduped)."""
    if recipients is None:
        return []

    if isinstance(recipients, EnrolmentQuery):
        return recipients.users()

    if hasattr(recipients, "is_authenticated") and not hasattr(recipients, "all"):
        return [recipients]

    seen = set()
    users = []
    for user in recipients.iterator() if hasattr(recipients, "iterator") else recipients:
        pk = getattr(user, "pk", None)
        if pk in seen or not getattr(user, "is_active", True):
            continue
        seen.add(pk)
        users.append(user)
    return users