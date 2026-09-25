"""Staff endpoints — under the ``hostel`` namespace."""

from django.urls import path

from hostel.views import (
    allocation_cancel,
    allocation_create,
    allocations,
    amenity_create,
    amenities,
    application_approve,
    application_reject,
    applications,
    bed_action,
    beds,
    block_create,
    blocks,
    check_in,
    check_out,
    checkins,
    checkouts,
    dashboard,
    fee_config_create,
    fee_config_delete,
    fee_config_edit,
    fee_configs,
    floor_create,
    floors,
    hostel_create,
    hostel_detail,
    hostel_edit,
    hostels,
    incident_create,
    incident_resolve,
    incidents,
    maintenance,
    maintenance_complete,
    maintenance_create,
    maintenance_delete,
    maintenance_edit,
    outstanding_balances,
    policy,
    reports,
    residents,
    room_create,
    rooms,
    transfer_complete,
    transfer_review,
    transfers,
)

urlpatterns = [
    # Dashboard
    path("dashboard/", dashboard, name="dashboard"),

    # Accommodation
    path("hostels/", hostels, name="hostels"),
    path("hostels/create/", hostel_create, name="hostel_create"),
    path("hostels/<int:pk>/", hostel_detail, name="hostel_detail"),
    path("hostels/<int:pk>/edit/", hostel_edit, name="hostel_edit"),
    path("blocks/", blocks, name="blocks"),
    path("blocks/create/", block_create, name="block_create"),
    path("floors/", floors, name="floors"),
    path("floors/create/", floor_create, name="floor_create"),
    path("rooms/", rooms, name="rooms"),
    path("rooms/create/", room_create, name="room_create"),
    path("beds/", beds, name="beds"),
    path("beds/<int:pk>/action/", bed_action, name="bed_action"),

    # Applications
    path("applications/", applications, name="applications"),
    path("applications/<int:pk>/approve/", application_approve, name="application_approve"),
    path("applications/<int:pk>/reject/", application_reject, name="application_reject"),

    # Allocations / residents / check-in / check-out
    path("allocations/", allocations, name="allocations"),
    path("allocations/create/", allocation_create, name="allocation_create"),
    path("allocations/<int:pk>/cancel/", allocation_cancel, name="allocation_cancel"),
    path("allocations/<int:pk>/check-in/", check_in, name="check_in"),
    path("allocations/<int:pk>/check-out/", check_out, name="check_out"),
    path("residents/", residents, name="residents"),
    path("check-ins/", checkins, name="checkins"),
    path("check-outs/", checkouts, name="checkouts"),

    # Transfers
    path("transfers/", transfers, name="transfers"),
    path("transfers/<int:pk>/review/", transfer_review, name="transfer_review"),
    path("transfers/<int:pk>/complete/", transfer_complete, name="transfer_complete"),

    # Maintenance
    path("maintenance/", maintenance, name="maintenance"),
    path("maintenance/create/", maintenance_create, name="maintenance_create"),
    path("maintenance/<int:pk>/edit/", maintenance_edit, name="maintenance_edit"),
    path("maintenance/<int:pk>/delete/", maintenance_delete, name="maintenance_delete"),
    path("maintenance/<int:pk>/complete/", maintenance_complete, name="maintenance_complete"),

    # Incidents
    path("incidents/", incidents, name="incidents"),
    path("incidents/create/", incident_create, name="incident_create"),
    path("incidents/<int:pk>/resolve/", incident_resolve, name="incident_resolve"),

    # Finance
    path("outstanding-balances/", outstanding_balances, name="outstanding_balances"),
    path("fee-configs/", fee_configs, name="fee_configs"),
    path("fee-configs/create/", fee_config_create, name="fee_config_create"),
    path("fee-configs/<int:pk>/edit/", fee_config_edit, name="fee_config_edit"),
    path("fee-configs/<int:pk>/delete/", fee_config_delete, name="fee_config_delete"),

    # Reports
    path("reports/", reports, name="reports"),

    # Settings
    path("policy/", policy, name="policy"),
    path("amenities/", amenities, name="amenities"),
    path("amenities/create/", amenity_create, name="amenity_create"),
]