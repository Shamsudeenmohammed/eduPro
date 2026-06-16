"""
portal/admin.py

Django admin for the admissions portal.
"""

from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import AdmissionApplication, AdmissionCycle, AdmissionStatus, DocumentRequest


# ── Inlines ────────────────────────────────────────────────────────────────

class DocumentRequestInline(admin.TabularInline):
    model  = DocumentRequest
    extra  = 0
    fields = ("document_name", "status", "requested_by", "requested_at", "fulfilled_at")
    readonly_fields = ("requested_at", "fulfilled_at")
    show_change_link = True


# ── Admin actions ──────────────────────────────────────────────────────────

@admin.action(description=_("Mark selected as Under Review"))
def mark_reviewing(modeladmin, request, queryset):
    updated = 0
    for app in queryset.filter(status=AdmissionStatus.PENDING):
        try:
            app.mark_reviewing(request.user)
            updated += 1
        except Exception:
            pass
    modeladmin.message_user(request, f"{updated} application(s) moved to Under Review.")


@admin.action(description=_("Approve selected applications (provisions user accounts)"))
def approve_applications(modeladmin, request, queryset):
    approved = errors = 0
    for app in queryset.filter(
        status__in=[AdmissionStatus.PENDING, AdmissionStatus.REVIEWING]
    ):
        try:
            app.approve(actor=request.user)
            approved += 1
        except Exception as e:
            errors += 1
    msg = f"{approved} application(s) approved."
    if errors:
        msg += f" {errors} failed (may already have a user account or be in wrong state)."
    modeladmin.message_user(request, msg)


@admin.action(description=_("Reject selected applications"))
def reject_applications(modeladmin, request, queryset):
    rejectable = queryset.exclude(
        status__in=[AdmissionStatus.APPROVED, AdmissionStatus.WITHDRAWN]
    )
    updated = 0
    for app in rejectable:
        try:
            app.reject(actor=request.user, reason="Rejected via bulk action.")
            updated += 1
        except Exception:
            pass
    modeladmin.message_user(request, f"{updated} application(s) rejected.")


# ── Cycle Admin ────────────────────────────────────────────────────────────

@admin.register(AdmissionCycle)
class AdmissionCycleAdmin(admin.ModelAdmin):
    list_display  = ("name", "academic_year", "start_date", "end_date", "is_active", "max_applications")
    list_filter   = ("is_active",)
    search_fields = ("name", "academic_year")
    readonly_fields = ("created_at", "updated_at")


# ── Application Admin ──────────────────────────────────────────────────────

@admin.register(AdmissionApplication)
class AdmissionApplicationAdmin(admin.ModelAdmin):
    list_display  = (
        "reference_number", "get_full_name_display",
        "program_applied", "application_type",
        "status", "cycle", "created_at",
    )
    list_filter   = (
        "status", "application_type",
        "cycle", "program_applied__department__faculty",
        "program_applied__department",
    )
    search_fields = (
        "first_name", "last_name", "email",
        "reference_number", "phone",
    )
    readonly_fields = (
        "reference_number", "created_at", "updated_at",
        "reviewed_at", "approved_at", "rejected_at",
    )
    raw_id_fields   = ("reviewed_by", "approved_by", "rejected_by", "user")
    inlines         = [DocumentRequestInline]
    actions         = [mark_reviewing, approve_applications, reject_applications]
    date_hierarchy  = "created_at"

    fieldsets = (
        (_("Application Info"), {
            "fields": (
                "reference_number", "cycle", "program_applied",
                "application_type", "status",
            ),
        }),
        (_("Personal Details"), {
            "fields": (
                "first_name", "last_name", "other_names",
                "date_of_birth", "gender", "nationality",
                "email", "phone", "address",
            ),
        }),
        (_("Academic Background"), {
            "fields": (
                "previous_school", "qualification",
                "year_of_completion", "aggregate_score",
            ),
        }),
        (_("Documents"), {
            "fields": ("transcript", "id_document", "passport_photo", "personal_statement"),
        }),
        (_("Review"), {
            "fields": ("reviewed_by", "reviewed_at", "review_notes"),
        }),
        (_("Approval"), {
            "fields": ("approved_by", "approved_at", "user"),
        }),
        (_("Rejection"), {
            "fields": ("rejected_by", "rejected_at", "rejection_reason"),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )

    def get_full_name_display(self, obj):
        return obj.get_full_name()
    get_full_name_display.short_description = _("Applicant Name")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "cycle", "program_applied__department", "reviewed_by", "approved_by", "user"
        )


# ── Document Request Admin ─────────────────────────────────────────────────

@admin.register(DocumentRequest)
class DocumentRequestAdmin(admin.ModelAdmin):
    list_display  = ("document_name", "application", "status", "requested_by", "requested_at", "fulfilled_at")
    list_filter   = ("status",)
    search_fields = ("document_name", "application__reference_number", "application__email")
    raw_id_fields = ("requested_by",)
    readonly_fields = ("requested_at", "fulfilled_at")
