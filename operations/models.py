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

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def total_rooms(self):
        return self.rooms.count()

    @property
    def available_rooms(self):
        occupied_room_ids = HostelAllocation.objects.filter(
            room__hostel=self, is_active=True
        ).values_list("room_id", flat=True)
        return self.rooms.exclude(pk__in=occupied_room_ids).filter(is_available=True)


class HostelRoom(TimeStampedModel):
    hostel = models.ForeignKey(Hostel, on_delete=models.CASCADE, related_name="rooms")
    room_number = models.CharField(max_length=20)
    capacity = models.PositiveSmallIntegerField(default=2)
    is_available = models.BooleanField(default=True)

    class Meta:
        unique_together = [("hostel", "room_number")]

    def __str__(self):
        return f"{self.hostel.name} — Room {self.room_number}"

    @property
    def occupied_beds(self):
        return self.allocations.filter(is_active=True).count()

    @property
    def available_beds(self):
        return self.capacity - self.occupied_beds

    @property
    def is_full(self):
        return self.available_beds <= 0


class HostelAllocation(TimeStampedModel):
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="hostel_allocations", limit_choices_to={"role": "student"},
    )
    room = models.ForeignKey(HostelRoom, on_delete=models.CASCADE, related_name="allocations")
    check_in = models.DateField()
    check_out = models.DateField(null=True, blank=True)
    expires_at = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-check_in"]

    def save(self, *args, **kwargs):
        from datetime import timedelta
        if not self.expires_at and self.check_in:
            self.expires_at = self.check_in + timedelta(days=365)
        super().save(*args, **kwargs)

    def __str__(self):
        expiry = self.expires_at.strftime("%b %d, %Y") if self.expires_at else "no expiry"
        return f"{self.student.get_full_name()} → {self.room} (expires {expiry})"

    @property
    def is_expired(self):
        from datetime import date
        return date.today() > self.expires_at if self.expires_at else False

    @property
    def days_left(self):
        from datetime import date
        if not self.expires_at:
            return 0
        delta = (self.expires_at - date.today()).days
        return max(delta, 0)

    def expire(self):
        self.is_active = False
        self.check_out = self.expires_at or date.today()
        self.save(update_fields=["is_active", "check_out"])


class HostelApplication(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending Review")
        APPROVED = "approved", _("Approved — Payment Required")
        REJECTED = "rejected", _("Rejected")
        CONFIRMED = "confirmed", _("Confirmed — Allocated")

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="hostel_applications", limit_choices_to={"role": "student"},
    )
    room = models.ForeignKey(
        HostelRoom, on_delete=models.CASCADE, related_name="applications",
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING,
    )
    admin_remark = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="hostel_reviews",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student.get_full_name()} → {self.room} ({self.get_status_display()})"


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
