"""Student-facing hostel views."""

import json
import uuid
from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from accounts.decorators import student_required
from academics.models import AcademicSession, Semester

from hostel import services
from hostel.forms import HostelApplyForm, HostelTransferRequestForm
from hostel.models import (
    Hostel,
    HostelAllocation,
    HostelApplication,
    HostelBed,
    HostelRoom,
    HostelTransfer,
)

from .common import _base_for_user, _page_obj, _service_message


def _current_semester_for(session):
    """The active semester for a session, or None when it isn't set."""
    current = Semester.get_current()
    if current and current.session_id == session.id:
        return current
    return None


@login_required
def hostel_list(request):
    services.HostelAllocationService.expire_due_reservations()

    from hostel import schema
    from hostel.services.finance import HostelFinanceService
    from hostel.models import Amenity, HostelFeeConfig

    overview = services.HostelAvailabilityService.hostel_overview()

    amenities_ok = schema.table_exists(
        Hostel.amenities.through._meta.db_table
    )
    fee_ok = schema.column_exists(
        HostelFeeConfig._meta.db_table, "semester_id"
    )
    session = AcademicSession.get_current() if fee_ok else None
    semester = None
    if session is not None:
        current = Semester.get_current()
        if current is not None and current.session_id == session.id:
            semester = current
    for item in overview:
        hostel = item["hostel"]
        item["amenities"] = list(hostel.amenities.all()) if amenities_ok else None
        item["fee_amount"] = (
            HostelFinanceService.fee_for(hostel, session, semester=semester)
            if fee_ok and session is not None else None
        )

    my_allocation = None
    my_application = None
    show_form = False
    can_renew = False
    balance_info = None

    if getattr(request.user, "is_student", False):
        my_allocation = (
            HostelAllocation.objects.filter(
                student=request.user,
                status__in=(
                    HostelAllocation.Status.RESERVED,
                    HostelAllocation.Status.ACTIVE,
                ),
            )
            .select_related(
                "bed__room__hostel", "bed__room__floor__block",
                "room__hostel", "session",
            )
            .order_by("-created_at")
            .first()
        )
        my_application = (
            HostelApplication.objects.filter(student=request.user)
            .select_related("room__hostel", "session")
            .exclude(
                status__in=(
                    HostelApplication.Status.CANCELLED,
                    HostelApplication.Status.EXPIRED,
                )
            )
            .order_by("-created_at")
            .first()
        )
        session = my_allocation.session if my_allocation else None
        if my_allocation:
            can_renew = (
                my_allocation.status == HostelAllocation.Status.ACTIVE
                and my_allocation.days_left <= 60
            )
            if session:
                balance_info = services.HostelFinanceService.balance_info(
                    request.user, session
                )
            show_form = False
        else:
            session = my_application.session if my_application else None
            if session:
                balance_info = services.HostelFinanceService.balance_info(request.user, session)
            live_statuses = (
                HostelApplication.Status.PENDING,
                HostelApplication.Status.UNDER_REVIEW,
                HostelApplication.Status.APPROVED,
            )
            show_form = not my_application or my_application.status not in live_statuses

    form = HostelApplyForm() if show_form else None

    return render(request, "hostel/hostel.html", {
        "overview": overview,
        "my_allocation": my_allocation,
        "my_application": my_application,
        "form": form,
        "can_renew": can_renew,
        "balance_info": balance_info,
        "paystack_enabled": services.PaystackService.enabled(),
        "page_title": "Hostel & Accommodation",
        "base_template": _base_for_user(request.user),
    })


@login_required
@student_required
@require_http_methods(["GET", "POST"])
def hostel_apply(request):
    form = HostelApplyForm(
        request.POST or None,
        initial={"hostel": request.GET.get("hostel")},
    )
    if request.method == "POST":
        if form.is_valid():
            try:
                application = services.HostelApplicationService.submit_application(
                    student=request.user,
                    room=form.cleaned_data["room"],
                )
                messages.success(
                    request,
                    f"Application submitted for {application.room}. Awaiting admin review.",
                )
            except services.HostelServiceError as exc:
                _service_message(request, exc)
        else:
            messages.error(request, "Please select a valid hostel and room.")
        return redirect("hostel:hostel")

    return render(request, "hostel/hostel_apply.html", {
        "form": form,
        "page_title": "Apply for Hostel Accommodation",
        "base_template": _base_for_user(request.user),
    })


@login_required
def hostel_rooms_api(request):
    """JSON endpoint returning rooms with at least one available bed."""
    hostel_id = request.GET.get("hostel")
    if not hostel_id:
        return JsonResponse({"rooms": []})
    room_ids = HostelBed.objects.filter(
        state=HostelBed.BedState.AVAILABLE,
        room__hostel_id=hostel_id,
        room__is_available=True,
    ).values_list("room_id", flat=True).distinct()
    rooms = HostelRoom.objects.filter(pk__in=room_ids).values(
        "id", "room_number", "capacity"
    )
    return JsonResponse({"rooms": list(rooms)})


@login_required
@student_required
@require_POST
def hostel_vacate(request):
    """Student-initiated check-out of their live allocation."""
    allocation = get_object_or_404(
        HostelAllocation,
        student=request.user,
        status__in=(
            HostelAllocation.Status.RESERVED,
            HostelAllocation.Status.ACTIVE,
        ),
    )
    try:
        services.HostelCheckoutService.checkout(
            allocation=allocation,
            actor=request.user,
            note="Student-initiated check-out.",
        )
    except services.HostelServiceError as exc:
        _service_message(request, exc)
    else:
        messages.success(
            request,
            "You have been checked out. Thank you for staying with us.",
        )
    return redirect("hostel:hostel")


@login_required
@student_required
@require_POST
def hostel_confirm_payment(request, pk):
    """
    Route an approved application into Paystack instead of auto-confirming.

    A payment is never marked verified here — the student must actually
    complete a charge on Paystack (test or live mode depending on the key
    configured for the environment) before ``hostel_pay_callback`` /
    ``hostel_pay_webhook`` confirms the payment.
    """
    return hostel_pay_initiate(request, pk)


@login_required
@student_required
@require_POST
def hostel_pay_initiate(request, pk):
    """
    Start a Paystack checkout for an approved hostel application.
    Redirects the student to Paystack's hosted payment page.
    """
    application = get_object_or_404(
        HostelApplication,
        pk=pk,
        student=request.user,
        status=HostelApplication.Status.APPROVED,
    )
    if not services.PaystackService.enabled():
        messages.error(request, "Online payments are not configured yet. Please contact the finance office.")
        return redirect("hostel:hostel")

    fee = services.HostelFinanceService.ensure_charge(
        student=request.user,
        session=application.session,
        hostel=application.room.hostel,
        room=application.room,
        semester=_current_semester_for(application.session),
    )
    if fee is None:
        messages.error(request, "No hostel charge is configured for this session.")
        return redirect("hostel:hostel")

    balance = fee.balance
    if balance <= 0:
        messages.info(request, "Your hostel charge is already fully paid.")
        return redirect("hostel:hostel")

    if not request.user.email:
        messages.error(request, "Your account has no email address. Contact the finance office to update it.")
        return redirect("hostel:hostel")

    reference = f"HOSTEL-{application.pk}-{uuid.uuid4().hex[:12]}"
    extra = "?hostel=1"
    callback_url = settings.PAYSTACK_CALLBACK_URL or request.build_absolute_uri(
        reverse("hostel:hostel_pay_callback")
    )
    try:
        data = services.PaystackService.initialize(
            email=request.user.email,
            amount=fee.balance * 100,
            reference=reference,
            callback_url=callback_url,
            metadata={"application": application.pk, "fee": fee.pk},
        )
    except services.PaystackError as exc:
        _service_message(request, exc)
        return redirect("hostel:hostel")

    auth_url = data.get("authorization_url")
    if not auth_url:
        messages.error(request, "Paystack did not return a payment link.")
        return redirect("hostel:hostel")
    return redirect(auth_url)


@login_required
@student_required
def hostel_pay_callback(request):
    """
    Paystack redirect target. Verifies the transaction and, on success,
    records the payment in finance and confirms the application.
    """
    reference = request.GET.get("reference") or request.GET.get("trxref")
    if not reference:
        messages.error(request, "Missing payment reference.")
        return redirect("hostel:hostel")

    try:
        data = services.PaystackService.verify(reference)
    except services.PaystackError as exc:
        _service_message(request, exc)
        return redirect("hostel:hostel")

    if data.get("status") != "success":
        messages.warning(request, "Payment was not successful. Please try again.")
        return redirect("hostel:hostel")

    app_pk = (data.get("metadata") or {}).get("application")
    fee_pk = (data.get("metadata") or {}).get("fee")
    application = (
        HostelApplication.objects.filter(pk=app_pk, student=request.user).first()
        if app_pk else None
    )
    if application is None:
        messages.error(request, "Could not match this payment to your application.")
        return redirect("hostel:hostel")

    fee = None
    if fee_pk:
        fee = application.student.fees.filter(pk=fee_pk).select_related("fee_structure").first()
    if fee is None:
        fee = services.HostelFinanceService.ensure_charge(
            student=request.user,
            session=application.session,
            hostel=application.room.hostel,
            room=application.room,
            semester=_current_semester_for(application.session),
        )
    if fee is not None:
        amount = services.PaystackService.minor_to_major(data.get("amount"))
        if amount > 0:
            services.HostelFinanceService.record_external_payment(
                fee=fee, amount=amount, reference=reference, method="paystack",
                actor=request.user,
            )

    try:
        services.HostelApplicationService.mark_payment_verified(
            application, actor=request.user
        )
    except services.HostelServiceError as exc:
        _service_message(request, exc)
        return redirect("hostel:hostel")

    messages.success(
        request,
        "Payment confirmed! A bed will be allocated by hostel staff shortly.",
    )
    return redirect("hostel:hostel")


@csrf_exempt
def hostel_pay_webhook(request):
    """
    Paystack server-to-server webhook for reliable delivery of charge
    successes. Verifies the HMAC signature before trusting the payload.
    """
    if request.method != "POST":
        return HttpResponse("Method not allowed", status=405)

    payload = request.body
    signature = request.headers.get("X-Paystack-Signature", "")
    secret = getattr(settings, "PAYSTACK_SECRET_KEY", "")
    if not secret:
        return HttpResponse("Not configured", status=503)

    import hashlib
    import hmac
    expected = hmac.new(secret.encode(), payload, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        return HttpResponse("Bad signature", status=400)

    try:
        event = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponse("Bad JSON", status=400)

    if event.get("event") != "charge.success":
        return JsonResponse({"status": "ignored"})

    data = event.get("data") or {}
    reference = data.get("reference") or ""
    if not reference or data.get("status") != "success":
        return JsonResponse({"status": "skipped"})

    try:
        verified = services.PaystackService.verify(reference)
    except services.PaystackError:
        return HttpResponse("Verify failed", status=502)

    app_pk = (verified.get("metadata") or {}).get("application")
    application = HostelApplication.objects.filter(pk=app_pk).first() if app_pk else None
    if application is None:
        return JsonResponse({"status": "no-application"})

    fee = services.HostelFinanceService.ensure_charge(
        student=application.student,
        session=application.session,
        hostel=application.room.hostel,
        room=application.room,
        semester=_current_semester_for(application.session),
    )
    if fee is not None:
        amount = services.PaystackService.minor_to_major(verified.get("amount"))
        if amount > 0:
            services.HostelFinanceService.record_external_payment(
                fee=fee, amount=amount, reference=reference, method="paystack",
                actor=application.student,
            )
    try:
        services.HostelApplicationService.mark_payment_verified(
            application, actor=application.student
        )
    except services.HostelServiceError:
        # Payment recorded but not fully satisfied yet — accept the event.
        pass
    return JsonResponse({"status": "ok"})


@login_required
@student_required
@require_POST
def hostel_renew_allocation(request):
    """Extend the current active allocation for another year."""
    allocation = get_object_or_404(
        HostelAllocation,
        student=request.user,
        status=HostelAllocation.Status.ACTIVE,
    )
    if allocation.days_left > 60:
        messages.warning(
            request,
            f"Your allocation still has {allocation.days_left} days remaining. "
            "Renewal is available within 60 days of expiry.",
        )
        return redirect("hostel:hostel")

    allocation.expires_at = (
        (allocation.expires_at or allocation.check_in or date.today())
        + timedelta(days=365)
    )
    allocation.save(update_fields=["expires_at"])
    messages.success(
        request,
        f"Your allocation has been renewed! New expiry is {allocation.expires_at}.",
    )
    return redirect("hostel:hostel")


@login_required
@student_required
@require_http_methods(["GET", "POST"])
def hostel_transfer_request(request):
    my_allocation = (
        HostelAllocation.objects.filter(
            student=request.user,
            status__in=(
                HostelAllocation.Status.RESERVED,
                HostelAllocation.Status.ACTIVE,
            ),
        )
        .select_related("bed__room__hostel", "bed__room__floor__block", "session")
        .first()
    )
    pending_transfers = (
        HostelTransfer.objects.filter(
            student=request.user,
            status__in=(HostelTransfer.Status.PENDING, HostelTransfer.Status.APPROVED),
        )
        .select_related("old_bed__room__hostel", "new_bed__room__hostel")
        .order_by("-requested_at")
    )

    form = HostelTransferRequestForm(request.POST or None)
    if request.method == "POST":
        if not my_allocation:
            messages.error(request, "You do not have an active allocation to transfer.")
            return redirect("hostel:hostel")
        if form.is_valid():
            try:
                services.HostelTransferService.request_transfer(
                    student=request.user,
                    old_allocation=my_allocation,
                    new_bed=form.cleaned_data["new_bed"],
                    reason=form.cleaned_data.get("reason", ""),
                    actor=request.user,
                )
            except services.HostelServiceError as exc:
                _service_message(request, exc)
            else:
                messages.success(request, "Transfer request submitted for review.")
                return redirect("hostel:hostel_transfer_request")
        messages.error(request, "Please select a valid available bed.")

    return render(request, "hostel/hostel_transfer_request.html", {
        "form": form,
        "my_allocation": my_allocation,
        "pending_transfers": pending_transfers,
        "page_title": "Request Room / Bed Transfer",
        "base_template": _base_for_user(request.user),
    })


@login_required
@student_required
@require_POST
def hostel_transfer_cancel(request, pk):
    transfer = get_object_or_404(
        HostelTransfer, pk=pk, student=request.user,
    )
    try:
        services.HostelTransferService.cancel_transfer(
            transfer, actor=request.user, note="Cancelled by student."
        )
    except services.HostelServiceError as exc:
        _service_message(request, exc)
    else:
        messages.success(request, "Transfer request cancelled.")
    return redirect("hostel:hostel_transfer_request")