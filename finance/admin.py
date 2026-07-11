from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import FeePayment, FeeStructure, PayrollRecord, RetakeFeeSetting, StudentFee, StudentRetakeFee


@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    list_display = ("name", "program", "session", "amount", "due_date", "is_active")
    list_filter = ("is_active", "session")
    search_fields = ("name", "program__name", "session__name")

@admin.register(FeePayment)
class FeePaymentAdmin(admin.ModelAdmin):
    list_display = ("student_fee", "amount", "payment_method", "reference", "received_by", "created_at")
    list_filter = ("payment_method", "created_at")
    search_fields = ("reference", "student_fee__student__email", "student_fee__student__first_name", "received_by__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PayrollRecord)
class PayrollRecordAdmin(admin.ModelAdmin):
    list_display = ("employee", "period_month", "period_year", "net_pay", "status", "processed_by")
    list_filter = ("status", "period_year", "period_month")
    search_fields = ("employee__email", "employee__first_name", "employee__last_name", "processed_by__email")


@admin.register(StudentFee)
class StudentFeeAdmin(admin.ModelAdmin):
    list_display = ("student", "fee_structure", "amount_due", "amount_paid", "balance", "status")
    list_filter = ("status", "fee_structure__session")
    search_fields = ("student__email", "student__first_name", "student__last_name")
    autocomplete_fields = ("student", "fee_structure")


@admin.register(RetakeFeeSetting)
class RetakeFeeSettingAdmin(admin.ModelAdmin):
    list_display = ("session", "amount", "is_active")
    list_filter = ("is_active",)
    search_fields = ("session__name",)


@admin.register(StudentRetakeFee)
class StudentRetakeFeeAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "session", "amount", "is_paid", "paid_at")
    list_filter = ("is_paid", "session")
    search_fields = ("student__email", "student__first_name", "student__last_name", "course__code")
    autocomplete_fields = ("student", "course", "paid_by")
    actions = ["mark_as_paid"]

    @admin.action(description=_("Mark selected retake fees as paid"))
    def mark_as_paid(self, request, queryset):
        from django.utils import timezone
        updated = queryset.filter(is_paid=False).update(
            is_paid=True,
            paid_at=timezone.now(),
            paid_by=request.user,
        )
        self.message_user(request, f"{updated} retake fee(s) marked as paid.")
