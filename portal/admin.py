"""
portal/admin.py

Django admin for the admissions portal.
"""

from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import (
    AdmissionApplication,
    AdmissionCycle,
    AdmissionOffer,
    AdmissionStatus,
    ApplicationAuditLog,
    ApplicationDocument,
    ApplicationPayment,
    ApplicationRequirement,
    ApplicationType,
    DocumentRequest,
)


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
    list_display  = (
        "name", "academic_year", "academic_session", "application_type",
        "application_fee", "status", "is_active", "start_date", "end_date",
        "max_applications",
    )
    list_filter   = ("is_active", "status", "application_type", "academic_session")
    search_fields = ("name", "academic_year")
    raw_id_fields = ("academic_session", "application_type")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (_("Cycle"), {
            "fields": (
                "name", "academic_year", "academic_session", "application_type",
                "start_date", "end_date", "status", "is_active", "max_applications",
            ),
        }),
        (_("Application fee"), {
            "fields": ("application_fee", "payment_required_to_progress"),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at"),
        }),
    )


# ── Application Admin ──────────────────────────────────────────────────────

@admin.register(AdmissionApplication)
class AdmissionApplicationAdmin(admin.ModelAdmin):
    list_display  = (
        "reference_number", "get_full_name_display",
        "program_applied", "application_type",
        "status", "decision", "cycle", "created_at",
    )
    list_filter   = (
        "status", "decision", "application_type",
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
                "application_type", "application_type_ref", "status",
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
        (_("Admission decision"), {
            "fields": (
                "decision", "decision_made_by", "decision_at",
                "decision_notes", "approved_by", "approved_at", "user",
            ),
        }),
        (_("Correction & interview"), {
            "fields": (
                "correction_reason", "corrected_at", "interview_date",
                "interview_notes",
            ),
        }),
        (_("Conversion"), {
            "fields": ("converted_at", "converted_by"),
        }),
        (_("Rejection"), {
            "fields": ("rejected_by", "rejected_at", "rejection_reason"),
        }),
        (_("Timestamps"), {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at", "submitted_at"),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        base = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.is_converted:
            # Never let the admin contradict a completed conversion.
            base += ["status", "decision", "program_applied", "cycle", "user"]
        return tuple(base)

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


# ═════════════════════════════════════════════════════════════════════════════
# APPLICATIONS & ADMISSIONS ENGINE — ADMIN
#
# These are read-oriented registrations. All state changes belong to the
# services in portal.services so the workflow graph, audit trail and
# notifications cannot be bypassed by editing a row here.
# ═════════════════════════════════════════════════════════════════════════════

class ApplicationDocumentInline(admin.TabularInline):
    model = ApplicationDocument
    extra = 0
    fields = (
        "document_type", "label", "status", "version",
        "uploaded_at", "verified_by", "verified_at",
    )
    readonly_fields = (
        "version", "uploaded_at", "verified_by",
        "verified_at", "status",
    )
    ordering = ("document_type", "-version")


class ApplicationPaymentInline(admin.TabularInline):
    model = ApplicationPayment
    extra = 0
    fields = (
        "payment_reference", "amount", "provider",
        "status", "transaction_id", "verified_by", "verified_at",
    )
    readonly_fields = (
        "payment_reference", "status", "verified_by", "verified_at",
    )
    ordering = ("-created_at",)


class AdmissionOfferInline(admin.TabularInline):
    model = AdmissionOffer
    extra = 0
    fields = (
        "offer_number", "status", "admission_type",
        "issue_date", "expiry_date", "acceptance_date",
        "issued_by", "revoked_by",
    )
    readonly_fields = ("offer_number", "issued_by", "acceptance_date")
    ordering = ("-issue_date",)


@admin.register(ApplicationType)
class ApplicationTypeAdmin(admin.ModelAdmin):
    list_display  = ("label", "code", "is_active", "requires_program",
                     "requires_payment", "order", "requirement_count")
    list_filter   = ("is_active", "requires_program", "requires_payment")
    search_fields = ("code", "label", "description")
    ordering      = ("order", "code")

    def requirement_count(self, obj):
        return obj.requirements.count()
    requirement_count.short_description = _("Requirements")


@admin.register(ApplicationRequirement)
class ApplicationRequirementAdmin(admin.ModelAdmin):
    list_display  = ("label", "application_type", "program", "kind",
                     "code", "is_required", "order")
    list_filter   = ("kind", "is_required", "application_type")
    search_fields = ("code", "label", "help_text")
    raw_id_fields = ("application_type", "program")
    ordering      = ("application_type", "order")


@admin.register(ApplicationDocument)
class ApplicationDocumentAdmin(admin.ModelAdmin):
    list_display  = (
        "name", "application", "document_type", "status",
        "version", "uploaded_at", "verified_by",
    )
    list_filter   = ("status", "document_type")
    search_fields = (
        "application__reference_number", "application__email",
        "application__first_name", "application__last_name",
    )
    raw_id_fields = ("application", "verified_by")
    readonly_fields = (
        "version", "uploaded_at", "verified_by", "verified_at", "status",
    )
    date_hierarchy = "uploaded_at"
    inlines = []

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "application", "verified_by",
        )


@admin.register(ApplicationPayment)
class ApplicationPaymentAdmin(admin.ModelAdmin):
    list_display  = (
        "payment_reference", "application", "amount",
        "provider", "status", "verified_by", "verified_at", "created_at",
    )
    list_filter   = ("status", "provider")
    search_fields = (
        "payment_reference", "transaction_id",
        "application__reference_number", "application__email",
    )
    raw_id_fields = ("application", "verified_by")
    readonly_fields = (
        "payment_reference", "status", "verified_by",
        "verified_at", "created_at", "updated_at",
    )
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "application", "verified_by",
        )


@admin.register(AdmissionOffer)
class AdmissionOfferAdmin(admin.ModelAdmin):
    list_display  = (
        "offer_number", "application", "status",
        "admission_type", "issue_date", "expiry_date", "acceptance_date",
    )
    list_filter   = ("status", "admission_type")
    search_fields = (
        "offer_number", "application__reference_number",
        "application__email", "application__first_name",
    )
    raw_id_fields = ("application", "issued_by")
    readonly_fields = (
        "offer_number", "issue_date", "issued_by",
        "acceptance_date", "responded_at",
    )
    date_hierarchy = "issue_date"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("application")


@admin.register(ApplicationAuditLog)
class ApplicationAuditLogAdmin(admin.ModelAdmin):
    """Append-only. Deliberately read-only in the admin."""

    list_display  = ("created_at", "application", "action", "actor", "is_visible_to_applicant")
    list_filter   = ("action", "is_visible_to_applicant")
    search_fields = (
        "application__reference_number", "application__email", "remark",
    )
    raw_id_fields = ("application", "actor")
    readonly_fields = (
        "application", "action", "actor", "remark",
        "from_status", "to_status", "is_visible_to_applicant",
        "created_at",
    )
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
