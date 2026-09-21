"""scheduling.urls — namespace "scheduling"."""

from django.urls import path

from . import views

app_name = "scheduling"


def _pk(name, view, kw=None):
    return path(f"<int:pk>/{name or ''}/", view, name=kw or name)


urlpatterns = [
    # ── command center ────────────────────────────────────────────────
    path("", views.dashboard, name="dashboard"),

    # ── academic calendar ─────────────────────────────────────────────
    path("calendar/", views.calendar, name="calendar"),
    path("calendar/event/<int:pk>/", views.event_detail,
         name="calendar_event_detail"),
    path("calendar/search/", views.calendar_search, name="calendar_search"),
    path("events/", views.event_list, name="event_list"),

    # ── HOD approvals & dashboard ─────────────────────────────────────
    path("hod-approvals/", views.hod_approvals, name="hod_approvals"),
    path("hod-decide/<int:pk>/", views.hod_decide, name="hod_decide"),
    path("hod/dashboard/", views.hod_dashboard, name="hod_dashboard"),
    path("events/add/", views.event_create, name="event_create"),
    path("events/<int:pk>/edit/", views.event_edit, name="event_edit"),
    path("events/<int:pk>/delete/", views.event_delete, name="event_delete"),

    # ── buildings & rooms ─────────────────────────────────────────────
    path("rooms/", views.room_list, name="room_list"),
    path("rooms/add/", views.room_create, name="room_create"),
    path("rooms/<int:pk>/edit/", views.room_edit, name="room_edit"),
    path("rooms/<int:pk>/", views.room_detail, name="room_detail"),
    path("rooms/<int:pk>/toggle/", views.room_toggle, name="room_toggle"),
    path("buildings/add/", views.building_create, name="building_create"),
    path("rooms/<int:pk>/timetable/", views.room_timetable,
         name="room_timetable"),

    # ── time slots ────────────────────────────────────────────────────
    path("slots/", views.slot_list, name="slot_list"),
    path("slots/add/", views.slot_create, name="slot_create"),
    path("slots/<int:pk>/delete/", views.slot_delete, name="slot_delete"),
    path("slots/regenerate/", views.slot_regenerate, name="slot_regenerate"),

    # ── lecturer unavailability ───────────────────────────────────────
    path("availability/", views.availability_list, name="availability_list"),
    path("availability/add/", views.availability_add, name="availability_add"),
    path("availability/<int:pk>/delete/", views.availability_delete,
         name="availability_delete"),

    # ── settings ──────────────────────────────────────────────────────
    path("settings/", views.settings, name="settings"),

    # ── schedules ─────────────────────────────────────────────────────
    path("list/", views.schedule_list, name="schedule_list"),
    path("new/", views.schedule_create, name="schedule_create"),
    path("schedule/entry/<int:pk>/detail/", views.entry_detail,
         name="entry_detail"),
    path("schedule/<int:pk>/", views.schedule_detail, name="schedule_detail"),
    path("schedule/<int:pk>/entries/", views.schedule_entries,
         name="schedule_entries"),
    path("schedule/<int:pk>/generate/", views.schedule_generate,
         name="schedule_generate"),
    path("schedule/<int:pk>/options/", views.schedule_options,
         name="schedule_options"),
    path("schedule/<int:pk>/optimize/", views.schedule_optimize,
         name="schedule_optimize"),
    path("schedule/<int:pk>/repair/", views.schedule_repair,
         name="schedule_repair"),
    path("schedule/<int:pk>/conflicts/", views.schedule_conflicts,
         name="schedule_conflicts"),
    path("schedule/<int:pk>/versions/", views.schedule_versions,
         name="schedule_versions"),

    # ── workflow ──────────────────────────────────────────────────────
    path("schedule/<int:pk>/submit/", views.schedule_submit,
         name="schedule_submit"),
    path("schedule/<int:pk>/approve/", views.schedule_approve,
         name="schedule_approve"),
    path("schedule/<int:pk>/publish/", views.schedule_publish,
         name="schedule_publish"),
    path("schedule/<int:pk>/unpublish/", views.schedule_unpublish,
         name="schedule_unpublish"),
    path("schedule/<int:pk>/archive/", views.schedule_archive,
         name="schedule_archive"),
    path("schedule/<int:pk>/unarchive/", views.schedule_unarchive,
         name="schedule_unarchive"),
    path("schedule/<int:pk>/clear-unplaced/", views.schedule_clear_unplaced,
         name="schedule_clear_unplaced"),
    path("schedule/<int:pk>/edit/", views.schedule_edit,
         name="schedule_edit"),
    path("schedule/<int:pk>/delete/", views.schedule_delete,
         name="schedule_delete"),

    # ── personal timetables ───────────────────────────────────────────
    path("my-timetable/", views.my_timetable, name="my_timetable"),
]
