"""scheduling templatetags — generic lookups + timetable helpers."""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def get_item(mapping, key):
    """mapping[key] with dictionary fallback (or attribute lookup)."""
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def status_badge_class(status):
    """Badge CSS class for a schedule status string (filter form)."""
    colors = {
        "draft": "badge-amber",
        "generated": "badge-blue",
        "under_review": "badge-purple",
        "hod_approved": "badge-purple",
        "approved": "badge-amber",
        "published": "badge-active",
        "archived": "badge-inactive",
    }
    return colors.get(status, "badge-amber")


@register.simple_tag
def status_badge(status):
    """Badge CSS class for a schedule status string (block-tag form)."""
    return status_badge_class(status)


@register.simple_tag
def entry_block(entry):
    """Render a weekly timetable cell (simple, safe HTML)."""
    room = entry.room_name or "—"
    return mark_safe(
        f'<div class="tblock tblock-{entry.session_type}">'
        f'<span class="tcourse">{entry.course.code}</span>'
        f'<span class="tmeta">{_ampm(entry.start_time)}–{_ampm(entry.end_time)} · {room}</span>'
        f'</div>'
    )


def _ampm(value):
    """Format a time as e.g. '9:05 AM' (cross-platform strftime-safe)."""
    if value is None:
        return ""
    hour = value.hour % 12 or 12
    return f"{hour}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"