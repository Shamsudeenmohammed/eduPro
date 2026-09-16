"""Allocation selectors — filtered querysets for staff views."""

from hostel.models import HostelAllocation


def allocations_qs(status=None, session=None):
    qs = HostelAllocation.objects.select_related(
        "student", "bed__room__hostel", "bed__room__floor__block", "session"
    ).order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    if session:
        qs = qs.filter(session=session)
    return qs


def active_allocations_for_student(student):
    return HostelAllocation.objects.filter(
        student=student,
        status__in=(HostelAllocation.Status.RESERVED, HostelAllocation.Status.ACTIVE),
    ).select_related(
        "bed__room__hostel", "bed__room__floor__block", "room__hostel", "session",
    ).order_by("-created_at")