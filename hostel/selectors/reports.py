"""Report selectors — aggregate queries for the reports dashboard."""

from django.db.models import Count, Q, Sum

from hostel.models import (
    HostelAllocation,
    HostelApplication,
    HostelBed,
    HostelIncident,
    HostelTransfer,
)


def occupancy_report():
    """
    Per-hostel occupancy summary. Returns a list of dicts:
    {
        "hostel": Hostel instance,
        "total_beds": int,
        "available": int, "reserved": int, "occupied": int,
        "maintenance": int, "blocked": int,
        "occupancy_pct": float,
    }
    """
    from hostel.models import Hostel
    hostels = Hostel.objects.filter(is_active=True)
    rows = []
    for h in hostels:
        counts = dict(
            HostelBed.objects.filter(room__hostel=h)
            .values_list("state")
            .annotate(n=Count("id"))
            .values_list("state", "n")
        )
        total = sum(counts.values()) or 1
        occupied = counts.get("occupied", 0)
        rows.append({
            "hostel": h,
            "total_beds": sum(counts.values()),
            "available": counts.get("available", 0),
            "reserved": counts.get("reserved", 0),
            "occupied": occupied,
            "maintenance": counts.get("maintenance", 0),
            "blocked": counts.get("blocked", 0),
            "occupancy_pct": round(occupied / total * 100, 1),
        })
    return rows


def application_funnel():
    """Application status counts (all-time)."""
    return dict(
        HostelApplication.objects.values_list("status")
        .annotate(n=Count("id"))
        .values_list("status", "n")
    )


def incident_summary():
    """Open / investigating / resolved / closed counts."""
    return dict(
        HostelIncident.objects.values_list("status")
        .annotate(n=Count("id"))
        .values_list("status", "n")
    )


def transfer_summary():
    """Transfer status counts."""
    return dict(
        HostelTransfer.objects.values_list("status")
        .annotate(n=Count("id"))
        .values_list("status", "n")
    )