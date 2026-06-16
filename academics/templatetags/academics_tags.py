"""
academics/templatetags/academics_tags.py

Custom template tags for the academics app.
"""

from django import template

from academics.models import AcademicSession, Semester

register = template.Library()


@register.simple_tag
def current_session():
    """Return the current AcademicSession or None."""
    return AcademicSession.get_current()


@register.simple_tag
def current_semester():
    """Return the current Semester or None."""
    return Semester.get_current()


@register.filter
def credit_badge_class(credits):
    """Return a CSS colour class based on credit unit count."""
    try:
        c = int(credits)
    except (TypeError, ValueError):
        return "bg-slate-100 text-slate-600"
    if c <= 1:
        return "bg-slate-100 text-slate-600"
    if c <= 3:
        return "bg-blue-100 text-blue-700"
    if c <= 5:
        return "bg-indigo-100 text-indigo-700"
    return "bg-purple-100 text-purple-700"


@register.filter
def course_type_badge(course_type):
    """Return CSS classes for a course type badge."""
    mapping = {
        "core":       "bg-indigo-100 text-indigo-700",
        "elective":   "bg-emerald-100 text-emerald-700",
        "general":    "bg-amber-100 text-amber-700",
        "lab":        "bg-cyan-100 text-cyan-700",
        "project":    "bg-violet-100 text-violet-700",
        "internship": "bg-rose-100 text-rose-700",
    }
    return mapping.get(course_type, "bg-slate-100 text-slate-600")


@register.filter
def enrolment_status_class(status):
    mapping = {
        "active":     "bg-emerald-100 text-emerald-700",
        "dropped":    "bg-red-100 text-red-700",
        "incomplete": "bg-amber-100 text-amber-700",
        "completed":  "bg-blue-100 text-blue-700",
    }
    return mapping.get(status, "bg-slate-100 text-slate-600")
