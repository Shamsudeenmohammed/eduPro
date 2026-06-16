"""Operations — timetable, announcements, events, hostel, support tickets."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class DayOfWeek(models.IntegerChoices):
    MONDAY = 1, _("Monday")
    TUESDAY = 2, _("Tuesday")
    WEDNESDAY = 3, _("Wednesday")
    THURSDAY = 4, _("Thursday")
    FRIDAY = 5, _("Friday")
    SATURDAY = 6, _("Saturday")
    SUNDAY = 7, _("Sunday")


class TimetableSlot(TimeStampedModel):
    """Weekly class schedule for a course offering."""
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.CASCADE,
        related_name="timetable_slots",
    )
    day = models.PositiveSmallIntegerField(choices=DayOfWeek.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    venue = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["day", "start_time"]
        unique_together = [("offering", "day", "start_time")]

    def __str__(self):
        return f"{self.offering.course.code} — {self.get_day_display()} {self.start_time}"


class AnnouncementPriority(models.TextChoices):
    LOW = "low", _("Low")
    NORMAL = "normal", _("Normal")
    HIGH = "high", _("High")
    URGENT = "urgent", _("Urgent")


class Announcement(TimeStampedModel):
    """Internal notice board for all roles."""
    title = models.CharField(max_length=200)
    content = models.TextField()
    priority = models.CharField(
        max_length=10, choices=AnnouncementPriority.choices, default=AnnouncementPriority.NORMAL
    )
    target_roles = models.CharField(
        max_length=100, blank=True,
        help_text=_("Comma-separated: admin,teacher,student. Empty = all."),
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name="announcements_posted",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    is_pinned = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]

    def __str__(self):
        return self.title


class CalendarEvent(TimeStampedModel):
    """Academic calendar events."""
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    event_type = models.CharField(max_length=50, default="general")
    location = models.CharField(max_length=200, blank=True)
    is_public = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
    )

    class Meta:
        ordering = ["start_date"]

    def __str__(self):
        return f"{self.title} ({self.start_date})"


class Hostel(TimeStampedModel):
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=200, blank=True)
    capacity = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class HostelRoom(TimeStampedModel):
    hostel = models.ForeignKey(Hostel, on_delete=models.CASCADE, related_name="rooms")
    room_number = models.CharField(max_length=20)
    capacity = models.PositiveSmallIntegerField(default=2)
    is_available = models.BooleanField(default=True)

    class Meta:
        unique_together = [("hostel", "room_number")]

    def __str__(self):
        return f"{self.hostel.name} — Room {self.room_number}"


class HostelAllocation(TimeStampedModel):
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="hostel_allocations", limit_choices_to={"role": "student"},
    )
    room = models.ForeignKey(HostelRoom, on_delete=models.CASCADE, related_name="allocations")
    check_in = models.DateField()
    check_out = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-check_in"]

    def __str__(self):
        return f"{self.student.get_full_name()} → {self.room}"


class TicketStatus(models.TextChoices):
    OPEN = "open", _("Open")
    IN_PROGRESS = "in_progress", _("In Progress")
    RESOLVED = "resolved", _("Resolved")
    CLOSED = "closed", _("Closed")


class SupportTicket(TimeStampedModel):
    """Student complaint / support ticket system."""
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="support_tickets", limit_choices_to={"role": "student"},
    )
    subject = models.CharField(max_length=200)
    description = models.TextField()
    category = models.CharField(max_length=50, default="general")
    status = models.CharField(max_length=15, choices=TicketStatus.choices, default=TicketStatus.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="tickets_assigned",
    )
    resolution = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.pk} — {self.subject}"
