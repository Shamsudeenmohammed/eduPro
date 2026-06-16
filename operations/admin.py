from django.contrib import admin
from .models import (
    Announcement, CalendarEvent, Hostel, HostelAllocation,
    HostelRoom, SupportTicket, TimetableSlot,
)

admin.site.register(TimetableSlot)
admin.site.register(Announcement)
admin.site.register(CalendarEvent)
admin.site.register(Hostel)
admin.site.register(HostelRoom)
admin.site.register(HostelAllocation)
admin.site.register(SupportTicket)
