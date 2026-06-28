from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from accounts.decorators import admin_required, student_required, teacher_required
from .forms import FeePaymentForm, FeeStructureForm, PayrollForm, StudentFeeForm
from .models import FeePayment, FeeStructure, PayrollRecord, StudentFee

User = get_user_model()


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

    search_query = request.GET.get("q", "").strip()
    if search_query:
        qs = qs.filter(
            Q(student__first_name__icontains=search_query) |
            Q(student__last_name__icontains=search_query) |
            Q(student__email__icontains=search_query)
        )

    paginator = Paginator(qs, 25)
    form = StudentFeeForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Fee assigned to student.")
        return redirect("finance:student_fees")

    if request.method == "POST" and "bulk_assign" in request.POST:
        fee_structure_id = request.POST.get("bulk_fee_structure")
        amount_due = request.POST.get("bulk_amount_due")
        due_date = request.POST.get("bulk_due_date") or None
        if fee_structure_id and amount_due:
            fee_structure = FeeStructure.objects.get(pk=fee_structure_id)
            students = User.objects.filter(role="student", is_active=True)
            created = 0
            for student in students:
                _, was_created = StudentFee.objects.get_or_create(
                    student=student,
                    fee_structure=fee_structure,
                    defaults={
                        "amount_due": amount_due,
                        "due_date": due_date if due_date else fee_structure.due_date,
                    },
                )
                if was_created:
                    created += 1
            messages.success(request, f"Fee assigned to {created} student(s).")
            return redirect("finance:student_fees")

    return render(request, "finance/student_fees.html", {
        "page_obj": paginator.get_page(request.GET.get("page")),
        "form": form, "page_title": "Student Fees",
        "search_query": search_query,
        "fee_structures": FeeStructure.objects.filter(is_active=True),
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

    search_query = request.GET.get("q", "").strip()
    if search_query:
        qs = qs.filter(
            Q(employee__first_name__icontains=search_query) |
            Q(employee__last_name__icontains=search_query) |
            Q(employee__email__icontains=search_query)
        )

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
        "search_query": search_query,
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
