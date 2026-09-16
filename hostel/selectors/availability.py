"""Availability selectors — read-only queries for templates."""

from hostel.models import Hostel, HostelBed

from hostel.services.availability import HostelAvailabilityService


def hostel_overview():
    """Student-facing overview of all active hostels with room-level detail."""
    return HostelAvailabilityService.hostel_overview()


def bed_state_summary(hostel_ids=None):
    """Flat {hostel_id: {state: count}} mapping."""
    return HostelAvailabilityService.bed_counts(hostel_ids)