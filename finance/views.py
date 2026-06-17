from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from accounts.decorators import admin_required, student_required, teacher_required
from .forms import FeePaymentForm, FeeStructureForm, PayrollForm, StudentFeeForm
from .models import FeePayment, FeeStructure, PayrollRecord, StudentFee


@login_required
@admin_required
def fee_dashboard(request):
    total_due = StudentFee.objects.aggregate(t=Sum("amount_due"))["t"] or 0
    total_paid = StudentFee.objects.aggregate(t=Sum("amount_paid"))["t"] or 0
    stats = {
        "total_due": total_due,
        "total_paid": total_paid,
        "pending_count": StudentFee.objects.filter(status="pending").count(),
        "outstanding": total_due - total_paid,
    }
    recent = StudentFee.objects.select_related("student", "fee_structure").order_by("-created_at")[:10]
    return render(request, "finance/fee_dashboard.html", {
        "stats": stats, "recent": recent, "page_title": "Fee Management",
    })


@login_required
@admin_required
def fee_structure_list(request):
    items = FeeStructure.objects.select_related("program", "session").order_by("-session__start_date")
    form = FeeStructureForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Fee structure created.")
        return redirect("finance:fee_structures")
    return render(request, "finance/fee_structures.html", {
        "items": items, "form": form, "page_title": "Fee Structures",
    })


@login_required
@admin_required
def student_fees(request):
    qs = StudentFee.objects.select_related("student", "fee_structure")
    paginator = Paginator(qs, 25)
    form = StudentFeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Fee assigned to student.")
        return redirect("finance:student_fees")
    return render(request, "finance/student_fees.html", {
        "page_obj": paginator.get_page(request.GET.get("page")),
        "form": form, "page_title": "Student Fees",
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def record_payment(request, fee_pk):
    fee = StudentFee.objects.get(pk=fee_pk)
    form = FeePaymentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        payment = form.save(commit=False)
        payment.student_fee = fee
        payment.received_by = request.user
        payment.save()
        fee.amount_paid += payment.amount
        if fee.amount_paid >= fee.amount_due:
            fee.status = "paid"
        elif fee.amount_paid > 0:
            fee.status = "partial"
        fee.save()
        messages.success(request, "Payment recorded.")
        return redirect("finance:student_fees")
    return render(request, "finance/record_payment.html", {
        "fee": fee, "form": form, "page_title": "Record Payment",
    })


@login_required
@student_required
def my_fees(request):
    fees = StudentFee.objects.filter(student=request.user).select_related("fee_structure")
    return render(request, "students/my_fees.html", {"fees": fees, "page_title": "My Fees"})


@login_required
@admin_required
def payroll_list(request):
    qs = PayrollRecord.objects.select_related("employee").order_by("-period_year", "-period_month")
    form = PayrollForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        rec = form.save(commit=False)
        rec.processed_by = request.user
        rec.save()
        messages.success(request, "Payroll record created.")
        return redirect("finance:payroll")
    paginator = Paginator(qs, 25)
    return render(request, "finance/payroll.html", {
        "page_obj": paginator.get_page(request.GET.get("page")),
        "form": form, "page_title": "Payroll",
    })


@login_required
@teacher_required
def my_payroll(request):
    records = PayrollRecord.objects.filter(employee=request.user).order_by("-period_year", "-period_month")
    paginator = Paginator(records, 25)
    tpl = "finance/my_payroll.html"
    if request.user.is_teacher:
        tpl = "teachers/my_payroll.html"
    return render(request, tpl, {
        "page_obj": paginator.get_page(request.GET.get("page")),
        "page_title": "My Payroll",
    })
