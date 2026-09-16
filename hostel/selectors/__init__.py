"""Selectors — thin query helpers for views (pure reads, no side-effects)."""

from .allocations import active_allocations_for_student, allocations_qs  # noqa: F401
from .availability import hostel_overview, bed_state_summary  # noqa: F401
from .reports import occupancy_report  # noqa: F401