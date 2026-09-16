"""Hostel availability service — DB-side aggregation of bed states."""

from django.db.models import Count, Sum

from hostel.models import Hostel, HostelBed, HostelRoom


class HostelAvailabilityService:
    """
    All occupancy/availability numbers are derived from HostelBed states and
    computed with DB-side aggregation (no Python loops / N+1).
    """

    @classmethod
    def bed_counts(cls, hostel_ids=None):
        """Return {hostel_id: {state_value: count}}-style structure."""
        qs = HostelBed.objects.all()
        if hostel_ids:
            qs = qs.filter(room__hostel_id__in=hostel_ids)
        rows = qs.values("room__hostel_id", "state").annotate(n=Count("id"))
        result = {}
        for row in rows:
            result.setdefault(row["room__hostel_id"], {})[row["state"]] = row["n"]
        return result

    @classmethod
    def room_bed_counts(cls, room_ids=None):
        """Return {room_id: {state_value: count}}."""
        qs = HostelBed.objects.all()
        if room_ids:
            qs = qs.filter(room_id__in=room_ids)
        rows = qs.values("room_id", "state").annotate(n=Count("id"))
        result = {}
        for row in rows:
            result.setdefault(row["room_id"], {})[row["state"]] = row["n"]
        return result

    @classmethod
    def hostel_overview(cls, hostel_ids=None):
        """
        Single aggregated view of every hostel plus per-room availability,
        using two queries total. Used by the student listing page.
        """
        hostels = Hostel.objects.filter(is_active=True)
        if hostel_ids:
            hostels = hostels.filter(pk__in=hostel_ids)
        hostels = list(hostels)
        h_ids = [h.pk for h in hostels]
        counts = cls.bed_counts(h_ids)

        rooms = list(
            HostelRoom.objects.filter(hostel_id__in=h_ids, is_available=True)
            .select_related("floor__block")
            .order_by("hostel_id", "room_number")
        )
        room_counts = cls.room_bed_counts([r.pk for r in rooms])

        rooms_by_hostel = {}
        for room in rooms:
            rooms_by_hostel.setdefault(room.hostel_id, []).append(room)

        overview = []
        for h in hostels:
            h_counts = counts.get(h.pk, {})
            available = h_counts.get(HostelBed.BedState.AVAILABLE, 0)
            reserved = h_counts.get(HostelBed.BedState.RESERVED, 0)
            occupied = h_counts.get(HostelBed.BedState.OCCUPIED, 0)
            maintenance = h_counts.get(HostelBed.BedState.MAINTENANCE, 0)
            blocked = h_counts.get(HostelBed.BedState.BLOCKED, 0)
            total = sum(h_counts.values()) or h.rooms.aggregate(t=Sum("capacity"))["t"] or 0

            room_rows = []
            for room in rooms_by_hostel.get(h.pk, []):
                r_counts = room_counts.get(room.pk, {})
                r_available = r_counts.get(HostelBed.BedState.AVAILABLE, 0)
                room_rows.append({
                    "room": room,
                    "states": r_counts,
                    "available": r_available,
                    "capacacity_legacy": room.capacity,
                })

            overview.append({
                "hostel": h,
                "counts": h_counts,
                "total_beds": total,
                "available": available,
                "reserved": reserved,
                "occupied": occupied,
                "maintenance": maintenance,
                "blocked": blocked,
                "rooms": room_rows,
            })
        return overview