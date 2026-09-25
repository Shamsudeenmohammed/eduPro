"""
id_cards/views.py

Thin HTTP layer over :mod:`id_cards.services`.  Every page enforces RBAC
through :mod:`id_cards.permissions` and every lifecycle change goes through
the service layer so audit logging and invariants cannot be bypassed.

RBAC summary
------------
* ``staff_required``  — admin, ID-card officer, or HOD (HOD is scoped to their
  own department(s) by :func:`id_cards.services.scoped_students`).
* ``admin_required``  — admin or ID-card officer only (templates & settings).
* Students only reach ``my_card`` / ``replacement_request`` for themselves.
"""

import csv
import logging

from django.contrib import messages
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from accounts.decorators import student_required
from academics.models import Department, StudentProfile

from . import renderers
from .forms import (
    PhotoUploadForm,
    RejectionForm,
    ReplacementRequestForm,
    SettingsForm,
    TemplateForm,
)
from .models import (
    AuditAction,
    BatchStatus,
    CardStatus,
    IDCard,
    IDCardAuditLog,
    IDCardBatch,
    IDCardSettings,
    IDCardTemplate,
    PassportPhoto,
    PhotoStatus,
    ReplacementRequest,
    ReplacementStatus,
    TemplateStatus,
)
from .permissions import admin_required, current_institution, staff_required
from .services import (
    CardGenerationError,
    PREFLIGHT_LABELS,
    approve_photo,
    approve_replacement_request,
    create_replacement_request,
    generate_batch,
    generate_card_for_student,
    is_id_card_admin,
    issue_card,
    cancel_card,
    log,
    mark_lost,
    mark_damaged,
    mark_printed,
    preflight,
    process_expirations,
    readiness_stats,
    readiness_students,
    reject_photo,
    reject_replacement_request,
    replace_card_direct,
    scoped_students,
    student_institution,
    upload_photo,
    verify_token,
)

logger = logging.getLogger("eduPro.id_cards")


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get_cards_from_request(request):
    """
    Resolve the cards selected by print forms/links.

    Accepts either an explicit ``cards`` list of PKs (comma separated) or a
    ``batch`` PK.  Returns a queryset scoped to what the user may manage.
    """
    batch_pk = request.GET.get("batch") or request.POST.get("batch")
    card_pks = (
        request.GET.get("cards") or request.POST.get("cards") or ""
    ).strip()

    qs = IDCard.objects.select_related(
        "student", "template", "photo", "institution",
    ).order_by("card_number")

    if batch_pk:
        batch = get_object_or_404(IDCardBatch, pk=batch_pk)
        qs = qs.filter(batch=batch)
    elif card_pks:
        pks = [p for p in card_pks.split(",") if p.strip().isdigit()]
        if pks:
            qs = qs.filter(pk__in=pks)
        else:
            qs = qs.none()
    else:
        qs = qs.none()

    if is_id_card_admin(request.user):
        return qs
    allowed = scoped_students(request.user).values_list("pk", flat=True)
    return qs.filter(student_id__in=allowed)


def _scoped_or_404(request, queryset, pk):
    obj = get_object_or_404(queryset, pk=pk)
    if not is_id_card_admin(request.user):
        allowed = set(scoped_students(request.user).values_list("pk", flat=True))
        if getattr(obj, "student_id", None) not in allowed:
            raise Http404
    return obj


def _card_is_manageable(request, card):
    if is_id_card_admin(request.user):
        return True
    return scoped_students(request.user).filter(pk=card.student_id).exists()


def _staff_badge(request):
    """Count of photos waiting for review (sidebar badge)."""
    if not is_id_card_admin(request.user):
        return None
    students = scoped_students(request.user)
    return PassportPhoto.objects.filter(
        student__in=students, status=PhotoStatus.PENDING_APPROVAL,
    ).count()


# ─────────────────────────────────────────────────────────────────────────────
# STAFF: DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
def dashboard(request):
    institution = current_institution(request)
    students = scoped_students(request.user, institution)

    stats = readiness_stats(students)
    pending_photos = PassportPhoto.objects.filter(
        student__in=students, status=PhotoStatus.PENDING_APPROVAL,
    ).count()

    context = {
        "stats": stats,
        "institution": institution,
        "pending_photos": pending_photos,
        "recent_batches": IDCardBatch.objects.filter(
            institution=institution,
        ).select_related("generated_by")[:6],
        "pending_replacements": ReplacementRequest.objects.filter(
            institution=institution, status=ReplacementStatus.PENDING,
        ).count(),
        "id_card_pending_photos": pending_photos,
        "is_admin_user": is_id_card_admin(request.user),
        "active_templates": IDCardTemplate.objects.filter(
            institution=institution, status=TemplateStatus.ACTIVE,
        ).count(),
    }
    return render(request, "id_cards/dashboard.html", context)


@staff_required
@require_POST
def run_expiry_check(request):
    institution = current_institution(request)
    count = process_expirations()
    log(request.user, AuditAction.CARD_EXPIRED,
        description=f"Manual expiry check run; {count} card(s) expired.")
    messages.success(
        request,
        f"Expiry check complete — {count} card(s) moved to Expired.",
    )
    return redirect("id_cards:dashboard")


# ─────────────────────────────────────────────────────────────────────────────
# STAFF: STUDENT READINESS
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
def student_list(request):
    institution = current_institution(request)
    students = scoped_students(request.user, institution).select_related(
        "academic_profile__program__department",
        "academic_profile__program",
    ).order_by("first_name", "last_name")

    flag = (request.GET.get("flag") or "").strip()
    search = (request.GET.get("q") or "").strip()

    if search:
        students = students.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
            | Q(academic_profile__student_number__icontains=search)
        )

    stats = readiness_stats(students)
    flagged = readiness_students(students, flag) if flag else students

    live_card_ids = set(
        IDCard.objects.filter(
            student__in=students, status__in=CardStatus.LIVE_STATUSES,
        ).values_list("student_id", flat=True)
    )

    departments = Department.objects.filter(institution=institution)
    context = {
        "students": flagged,
        "stats": stats,
        "flag": flag,
        "search": search,
        "departments": departments,
        "live_card_ids": live_card_ids,
        "flag_labels": {
            "photos_missing": "Photo Missing",
            "photos_pending": "Photo Pending",
            "photos_rejected": "Photo Rejected",
            "photos_approved": "Photo Approved",
            "ready": "Ready to Generate",
            "generated": "Already Generated",
            "expired": "Expired",
            "printed": "Printed",
            "issued": "Issued",
        },
        "id_card_pending_photos": _staff_badge(request),
    }
    return render(request, "id_cards/student_list.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# STAFF: PHOTO REVIEW
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
def photo_review(request):
    status_filter = (request.GET.get("status") or PhotoStatus.PENDING_APPROVAL).strip()
    if status_filter not in {s.value for s in PhotoStatus}:
        status_filter = PhotoStatus.PENDING_APPROVAL

    students = scoped_students(request.user)
    photos = PassportPhoto.objects.filter(
        student__in=students, status=status_filter,
    ).select_related(
        "student", "student__academic_profile__program__department",
        "reviewed_by",
    ).order_by("-submitted_at")[:200]

    context = {
        "photos": photos,
        "status_filter": status_filter,
        "status_choices": PhotoStatus.choices,
        "reject_form": RejectionForm(),
        "id_card_pending_photos": _staff_badge(request),
    }
    return render(request, "id_cards/photo_review.html", context)


@staff_required
@require_POST
def photo_approve(request, photo_pk):
    photo = get_object_or_404(PassportPhoto, pk=photo_pk)
    if not _card_is_manageable(request, photo.student):
        raise Http404
    approve_photo(photo, request.user)
    messages.success(request, f"Photo approved for {photo.student.get_full_name()}.")
    return redirect(request.META.get("HTTP_REFERER", reverse("id_cards:photo_review")))


@staff_required
@require_POST
def photo_reject(request, photo_pk):
    photo = get_object_or_404(PassportPhoto, pk=photo_pk)
    if not _card_is_manageable(request, photo.student):
        raise Http404
    form = RejectionForm(request.POST)
    if form.is_valid():
        reject_photo(photo, request.user, form.cleaned_data["reason"])
        messages.success(request, f"Photo rejected for {photo.student.get_full_name()}.")
    else:
        messages.error(request, "A rejection reason is required.")
    return redirect(request.META.get("HTTP_REFERER", reverse("id_cards:photo_review")))


# ─────────────────────────────────────────────────────────────────────────────
# STAFF: SINGLE GENERATION
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
def generate_single(request, student_pk):
    students = scoped_students(request.user)
    student = get_object_or_404(students, pk=student_pk)
    code, reasons = preflight(student)

    if request.method == "POST":
        try:
            card = generate_card_for_student(student, request.user)
        except CardGenerationError as exc:
            for reason in exc.reasons or [exc.message]:
                messages.error(request, reason)
        else:
            messages.success(
                request,
                f"Card {card.card_number} generated for {student.get_full_name()}.",
            )
            return redirect("id_cards:card_detail", pk=card.pk)

    context = {
        "student": student,
        "preflight_code": code,
        "preflight_label": PREFLIGHT_LABELS.get(code, code),
        "preflight_reasons": reasons,
        "can_generate": code == "ready",
        "photo": PassportPhoto.objects.filter(student=student).first(),
        "id_card_pending_photos": _staff_badge(request),
    }
    return render(request, "id_cards/generate_single.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# STAFF: BULK GENERATION
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
def generate_batch_view(request):
    institution = current_institution(request)
    students_qs = scoped_students(request.user, institution).select_related(
        "academic_profile__program__department",
        "academic_profile__program",
        "id_photo",
    ).order_by("first_name", "last_name")

    search = (request.GET.get("q") or "").strip()
    if search:
        students_qs = students_qs.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
            | Q(academic_profile__student_number__icontains=search)
        )

    # Preflight every listed student so the operator sees what will happen.
    preview = []
    for student in students_qs[:500]:
        code, reasons = preflight(student, institution=institution)
        preview.append({
            "student": student,
            "code": code,
            "label": PREFLIGHT_LABELS.get(code, code),
            "reasons": reasons,
            "ready": code == "ready",
        })
    ready_count = sum(1 for p in preview if p["ready"])

    if request.method == "POST":
        chosen = request.POST.getlist("students")
        if not chosen:
            messages.error(request, "Select at least one student.")
        else:
            chosen_students = [p["student"] for p in preview if str(p["student"].pk) in chosen]
            if not chosen_students:
                messages.error(request, "Selected students are not in the current view.")
            else:
                template = None
                tpl_pk = request.POST.get("template")
                if tpl_pk:
                    template = IDCardTemplate.objects.filter(
                        pk=tpl_pk, institution=institution,
                    ).first()
                try:
                    batch = generate_batch(
                        chosen_students, request.user,
                        template=template,
                        notes=(request.POST.get("notes") or "").strip(),
                        filters={"q": search} if search else {},
                    )
                except CardGenerationError as exc:
                    messages.error(request, exc.message)
                else:
                    messages.success(
                        request,
                        f"Batch {batch.batch_number}: {batch.generated_count} "
                        f"generated, {batch.skipped_count} skipped.",
                    )
                    return redirect("id_cards:batch_detail", pk=batch.pk)

    context = {
        "preview": preview,
        "ready_count": ready_count,
        "search": search,
        "templates": IDCardTemplate.objects.filter(
            institution=institution, status=TemplateStatus.ACTIVE,
        ),
        "id_card_pending_photos": _staff_badge(request),
    }
    return render(request, "id_cards/generate_batch.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# STAFF: BATCHES
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
def batch_list(request):
    institution = current_institution(request)
    batches = IDCardBatch.objects.filter(
        institution=institution,
    ).select_related("generated_by").annotate(card_count=Count("cards"))[:200]
    context = {
        "batches": batches,
        "id_card_pending_photos": _staff_badge(request),
    }
    return render(request, "id_cards/batch_list.html", context)


@staff_required
def batch_detail(request, pk):
    institution = current_institution(request)
    batch = get_object_or_404(
        IDCardBatch.objects.select_related("generated_by"), pk=pk,
        institution=institution,
    )
    cards = batch.cards.select_related(
        "student", "template", "photo",
    ).order_by("card_number")

    context = {
        "batch": batch,
        "cards": cards,
        "skips": batch.skips or {},
        "print_modes": (("front", "Front only"), ("back", "Back only"),
                        ("duplex", "Front + back (duplex)")),
        "id_card_pending_photos": _staff_badge(request),
    }
    return render(request, "id_cards/batch_detail.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# STAFF: CARDS
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
def card_list(request):
    institution = current_institution(request)
    students = scoped_students(request.user, institution)

    cards = IDCard.objects.filter(
        student__in=students, institution=institution,
    ).select_related("student", "template", "photo").order_by("-created_at")

    status_filter = (request.GET.get("status") or "").strip()
    if status_filter and status_filter in {s.value for s in CardStatus}:
        cards = cards.filter(status=status_filter)
    search = (request.GET.get("q") or "").strip()
    if search:
        cards = cards.filter(
            Q(card_number__icontains=search)
            | Q(student__first_name__icontains=search)
            | Q(student__last_name__icontains=search)
            | Q(student__academic_profile__student_number__icontains=search)
        )

    context = {
        "cards": cards[:300],
        "status_choices": CardStatus.choices,
        "status_filter": status_filter,
        "search": search,
        "id_card_pending_photos": _staff_badge(request),
    }
    return render(request, "id_cards/card_list.html", context)


@staff_required
def card_detail(request, pk):
    cards = IDCard.objects.select_related(
        "student", "template", "photo", "batch", "institution",
    )
    card = _scoped_or_404(request, cards, pk)

    replacement_form = ReplacementRequestForm()
    context = {
        "card": card,
        "audit_logs": IDCardAuditLog.objects.filter(card=card)[:50],
        "replacement_requests": ReplacementRequest.objects.filter(card=card),
        "rejection_form": RejectionForm(),
        "replacement_form": replacement_form,
        "can_issue": card.is_active_or_issued,
        "id_card_pending_photos": _staff_badge(request),
    }
    return render(request, "id_cards/card_detail.html", context)


@staff_required
@require_POST
def card_action(request, pk, action):
    cards = IDCard.objects.select_related("student", "template")
    card = _scoped_or_404(request, cards, pk=pk)
    action = action.strip().lower()

    try:
        if action == "print":
            mark_printed([card], request.user)
            messages.success(request, f"{card.card_number} marked printed.")
        elif action == "issue":
            issue_card(card, request.user)
            messages.success(request, f"{card.card_number} issued — QR now verifies.")
        elif action == "cancel":
            cancel_card(card, request.user,
                        reason=(request.POST.get("reason") or "").strip())
            messages.success(request, f"{card.card_number} cancelled.")
        elif action == "lost":
            mark_lost(card, request.user)
            messages.success(
                request,
                f"{card.card_number} marked lost. Student can request a replacement.",
            )
        elif action == "damaged":
            mark_damaged(card, request.user)
            messages.success(
                request,
                f"{card.card_number} marked damaged. Student can request a replacement.",
            )
        elif action == "replace":
            replace_card_direct(
                card, request.user,
                reason=(request.POST.get("reason") or "other"),
                notes=(request.POST.get("notes") or "").strip(),
            )
            messages.success(request, f"{card.card_number} replaced with a new card.")
        else:
            messages.error(request, "Unknown action.")
    except CardGenerationError as exc:
        for reason in exc.reasons or [exc.message]:
            messages.error(request, reason)

    return redirect("id_cards:card_detail", pk=pk)


# ─────────────────────────────────────────────────────────────────────────────
# PRINT / PREVIEW / PDF
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
@require_GET
def print_preview(request):
    cards = list(_get_cards_from_request(request))
    if not cards:
        messages.error(request, "No cards selected for printing.")
        return redirect(request.META.get("HTTP_REFERER", reverse("id_cards:card_list")))

    mode = (request.GET.get("mode") or "combined").strip()
    if mode not in {"front", "back", "duplex", "combined"}:
        mode = "combined"

    previews = []
    for card in cards[:60]:
        try:
            if mode == "combined":
                combined_bytes = renderers.combined_card_bytes(card, request)
            else:
                front_bytes = renderers.digital_card_bytes(card, "front", request)
                back_bytes = renderers.digital_card_bytes(card, "back", request)
        except Exception:  # noqa: BLE001 — preview must never 500
            logger.exception("Preview render failed for %s", card.card_number)
            continue
        if mode == "combined":
            previews.append({
                "card": card,
                "combined": "data:image/jpeg;base64," + _b64(combined_bytes),
            })
        else:
            previews.append({
                "card": card,
                "front": "data:image/jpeg;base64," + _b64(front_bytes),
                "back": "data:image/jpeg;base64," + _b64(back_bytes),
            })

    query = request.GET.urlencode()
    query_pairs = request.GET.items()
    context = {
        "cards": cards,
        "previews": previews,
        "mode": mode,
        "query": query,
        "query_pairs": query_pairs,
        "total": len(cards),
        "id_card_pending_photos": _staff_badge(request),
    }
    return render(request, "id_cards/print_preview.html", context)


def _b64(data):
    import base64
    return base64.b64encode(data).decode("ascii")


@staff_required
@require_GET
def print_pdf(request):
    cards = list(_get_cards_from_request(request))
    if not cards:
        messages.error(request, "No cards selected for printing.")
        return redirect(reverse("id_cards:card_list"))

    mode = (request.GET.get("mode") or "combined").strip()
    if mode not in {"front", "back", "duplex", "combined"}:
        mode = "combined"
    back_order = (request.GET.get("back_order") or "mirrored").strip()
    if back_order not in {"mirrored", "direct"}:
        back_order = "mirrored"

    try:
        buffer = renderers.pdf_for_cards(
            cards, request=request, mode=mode, back_order=back_order,
        )
    except Exception as exc:  # noqa: BLE001 — surface as a friendly message
        logger.exception("PDF generation failed")
        messages.error(request, f"Could not generate PDF: {exc}")
        return redirect(reverse("id_cards:card_list"))

    batches = {c.batch for c in cards if c.batch}
    if len(batches) == 1:
        batch = next(iter(batches))
        log(request.user, AuditAction.BATCH_DOWNLOADED, batch=batch,
            description=(
                f"Batch {batch.batch_number} downloaded ({mode}, {back_order})."
            ))
        for card in cards:
            mark_printed([card], request.user)
    else:
        for card in cards:
            log(request.user, AuditAction.CARD_PRINTED, card=card,
                description=f"Card {card.card_number} PDF downloaded ({mode}).")

    filename = f"idcards-{cards[0].card_number}" if len(cards) == 1 else "id-cards"
    return FileResponse(
        buffer, as_attachment=True, filename=f"{filename}.pdf",
        content_type="application/pdf",
    )


@staff_required
@require_GET
def card_image(request, pk, side):
    cards = IDCard.objects.select_related("student", "template", "photo")
    card = _scoped_or_404(request, cards, pk)
    if side not in {"front", "back", "combined"}:
        raise Http404
    try:
        if side == "combined":
            payload = renderers.combined_card_bytes(card, request)
        else:
            payload = renderers.digital_card_bytes(card, side, request)
    except Exception:  # noqa: BLE001
        logger.exception("Card image render failed for %s", card.card_number)
        messages.error(request, "Could not render the card image.")
        return redirect("id_cards:card_detail", pk=pk)

    filename = (f"{card.card_number}-front-back" if side == "combined"
                else f"{card.card_number}-{side}")
    response = HttpResponse(payload, content_type="image/jpeg")
    response["Content-Disposition"] = (
        f'inline; filename="{filename}.jpg"'
    )
    return response


# ─────────────────────────────────────────────────────────────────────────────
# STAFF: TEMPLATES & SETTINGS (admin / ID-card officer only)
# ─────────────────────────────────────────────────────────────────────────────

@admin_required
def template_list(request):
    institution = current_institution(request)
    templates = IDCardTemplate.objects.filter(
        institution=institution,
    ).order_by("status", "name")
    context = {
        "templates": templates,
        "id_card_pending_photos": _staff_badge(request),
        "status_choices": TemplateStatus.choices,
    }
    return render(request, "id_cards/template_list.html", context)


@admin_required
def template_create(request):
    institution = current_institution(request)
    if institution is None:
        messages.error(request, "No institution configured yet.")
        return redirect("id_cards:dashboard")
    if request.method == "POST":
        form = TemplateForm(request.POST, request.FILES)
        if form.is_valid():
            template = form.save(commit=False)
            template.institution = institution
            template.created_by = request.user
            template.save()
            log(request.user, AuditAction.TEMPLATE_CREATED, template=template,
                description=f"Template {template.name} created.")
            messages.success(request, f"Template {template.name} created.")
            return redirect("id_cards:template_list")
    else:
        form = TemplateForm()
    context = {"form": form, "title": "New ID Card Template"}
    return render(request, "id_cards/template_form.html", context)


@admin_required
def template_edit(request, pk):
    institution = current_institution(request)
    template = get_object_or_404(
        IDCardTemplate, pk=pk, institution=institution,
    )
    if request.method == "POST":
        form = TemplateForm(request.POST, request.FILES, instance=template)
        if form.is_valid():
            template = form.save(commit=False)
            if template.status == TemplateStatus.ARCHIVED:
                template.is_default = False
                template.archived_at = template.archived_at or _now()
            template.save()
            log(request.user, AuditAction.TEMPLATE_CHANGED, template=template,
                description=f"Template {template.name} updated.")
            messages.success(request, f"Template {template.name} updated.")
            return redirect("id_cards:template_list")
    else:
        form = TemplateForm(instance=template)
    context = {"form": form, "title": f"Edit — {template.name}", "template": template}
    return render(request, "id_cards/template_form.html", context)


@admin_required
@require_POST
def template_set_default(request, pk):
    institution = current_institution(request)
    template = get_object_or_404(
        IDCardTemplate, pk=pk, institution=institution,
    )
    if template.status != TemplateStatus.ACTIVE:
        messages.error(request, "Only active templates can be the default.")
    else:
        template.is_default = True
        template.save()
        log(request.user, AuditAction.TEMPLATE_ACTIVATED, template=template,
            description=f"Template {template.name} set as default.")
        messages.success(request, f"{template.name} is now the default template.")
    return redirect("id_cards:template_list")


@admin_required
@require_POST
def template_archive(request, pk):
    institution = current_institution(request)
    template = get_object_or_404(
        IDCardTemplate, pk=pk, institution=institution,
    )
    template.status = TemplateStatus.ARCHIVED
    template.is_default = False
    template.archived_at = template.archived_at or _now()
    template.save()
    log(request.user, AuditAction.TEMPLATE_ARCHIVED, template=template,
        description=f"Template {template.name} archived.")
    messages.success(
        request,
        f"{template.name} archived. Existing cards keep their copy of the design.",
    )
    return redirect("id_cards:template_list")


@admin_required
@require_POST
def template_set_status(request, pk):
    """Quick status switch from the templates list (draft/active/archived)."""
    institution = current_institution(request)
    template = get_object_or_404(IDCardTemplate, pk=pk, institution=institution)
    try:
        status = TemplateStatus(request.POST.get("status", ""))
    except ValueError:
        messages.error(request, "Invalid template status.")
        return redirect("id_cards:template_list")

    previous = template.status
    if status == previous:
        messages.info(request, f"{template.name} is already {status.label}.")
        return redirect("id_cards:template_list")

    template.status = status
    if status == TemplateStatus.ARCHIVED:
        template.is_default = False
        template.archived_at = template.archived_at or _now()
    else:
        template.archived_at = None
    template.save()

    if status == TemplateStatus.ARCHIVED:
        action, verb = AuditAction.TEMPLATE_ARCHIVED, "archived"
    elif status == TemplateStatus.ACTIVE:
        action, verb = AuditAction.TEMPLATE_ACTIVATED, "activated"
    else:
        action, verb = AuditAction.TEMPLATE_CHANGED, f"changed to {status.label}"
    log(request.user, action, template=template,
        description=f"Template {template.name} {verb}.")
    messages.success(request, f"Template {template.name} {verb}.")
    return redirect("id_cards:template_list")


@admin_required
@require_POST
def template_duplicate(request, pk):
    institution = current_institution(request)
    source = get_object_or_404(IDCardTemplate, pk=pk, institution=institution)
    duplicate = IDCardTemplate.objects.create(
        institution=institution,
        name=f"{source.name} (copy)",
        status=TemplateStatus.DRAFT,
        card_width_mm=source.card_width_mm,
        card_height_mm=source.card_height_mm,
        primary_color=source.primary_color,
        secondary_color=source.secondary_color,
        text_color=source.text_color,
        front_fields=list(source.front_fields or []),
        back_fields=list(source.back_fields or []),
        custom_front_text=source.custom_front_text,
        custom_back_text=source.custom_back_text,
        return_instructions=source.return_instructions,
        card_terms=source.card_terms,
        authorized_signature_name=source.authorized_signature_name,
        show_signature_line=source.show_signature_line,
        validity_years=source.validity_years,
        created_by=request.user,
    )
    log(request.user, AuditAction.TEMPLATE_DUPLICATED, template=duplicate,
        description=f"Template duplicated from {source.name}.")
    messages.success(request, f"Copied to “{duplicate.name}” (draft).")
    return redirect("id_cards:template_edit", pk=duplicate.pk)


@admin_required
def settings_view(request):
    institution = current_institution(request)
    if institution is None:
        messages.error(request, "No institution configured yet.")
        return redirect("id_cards:dashboard")
    settings_obj = IDCardSettings.get_for(institution)
    if request.method == "POST":
        form = SettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            settings_obj = form.save()
            log(request.user, AuditAction.SETTINGS_UPDATED,
                description="ID card settings updated.")
            messages.success(request, "ID card settings saved.")
            return redirect("id_cards:settings")
    else:
        form = SettingsForm(instance=settings_obj)
    context = {"form": form, "settings_obj": settings_obj}
    return render(request, "id_cards/settings.html", context)


def _now():
    from django.utils import timezone
    return timezone.now()


# ─────────────────────────────────────────────────────────────────────────────
# STAFF: REPLACEMENT REQUESTS
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
def replacement_list(request):
    institution = current_institution(request)
    status_filter = (request.GET.get("status") or ReplacementStatus.PENDING).strip()
    requests_qs = ReplacementRequest.objects.filter(
        institution=institution,
    ).select_related("student", "card", "requested_by").order_by("-requested_at")
    if status_filter in {s.value for s in ReplacementStatus}:
        requests_qs = requests_qs.filter(status=status_filter)

    if not is_id_card_admin(request.user):
        students = set(scoped_students(request.user).values_list("pk", flat=True))
        requests_qs = [r for r in requests_qs if r.student_id in students]

    context = {
        "requests": requests_qs[:200],
        "status_filter": status_filter,
        "status_choices": ReplacementStatus.choices,
        "reject_form": RejectionForm(),
        "id_card_pending_photos": _staff_badge(request),
    }
    return render(request, "id_cards/replacement_list.html", context)


@staff_required
@require_POST
def replacement_approve(request, pk):
    request_obj = get_object_or_404(ReplacementRequest, pk=pk)
    if not _card_is_manageable(request, request_obj.student):
        raise Http404
    try:
        approve_replacement_request(request_obj, request.user)
    except CardGenerationError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "Replacement approved — new card generated.")
    return redirect(request.META.get("HTTP_REFERER", reverse("id_cards:replacement_list")))


@staff_required
@require_POST
def replacement_reject(request, pk):
    request_obj = get_object_or_404(ReplacementRequest, pk=pk)
    if not _card_is_manageable(request, request_obj.student):
        raise Http404
    form = RejectionForm(request.POST)
    if form.is_valid():
        reject_replacement_request(request_obj, request.user,
                                   form.cleaned_data["reason"])
        messages.success(request, "Replacement request rejected.")
    else:
        messages.error(request, "A rejection reason is required.")
    return redirect(request.META.get("HTTP_REFERER", reverse("id_cards:replacement_list")))


# ─────────────────────────────────────────────────────────────────────────────
# STAFF: REPORTS
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
def reports(request):
    institution = current_institution(request)
    students = scoped_students(request.user, institution)
    stats = readiness_stats(students)

    status_counts = dict(
        IDCard.objects.filter(institution=institution)
        .values_list("status")
        .annotate(n=Count("pk"))
    )
    status_rows = [
        (value, label, status_counts.get(value, 0))
        for value, label in CardStatus.choices
    ]

    context = {
        "stats": stats,
        "institution": institution,
        "status_choices": CardStatus.choices,
        "status_rows": status_rows,
        "id_card_pending_photos": _staff_badge(request),
    }
    return render(request, "id_cards/reports.html", context)


@staff_required
@require_GET
def export_csv(request):
    institution = current_institution(request)
    kind = (request.GET.get("kind") or "cards").strip()

    response = HttpResponse(content_type="text/csv")
    writer = csv.writer(response)

    if kind == "readiness":
        response["Content-Disposition"] = 'attachment; filename="id-card-readiness.csv"'
        students = scoped_students(request.user, institution).select_related(
            "academic_profile__program__department",
            "academic_profile__program",
        )
        writer.writerow([
            "Student ID", "Name", "Email", "Programme", "Department",
            "Photo Status", "Card Status", "Card Number",
        ])
        photo_map = {
            p.student_id: p.status for p in
            PassportPhoto.objects.filter(student__in=students)
        }
        card_map = {
            c.student_id: c for c in
            IDCard.objects.filter(
                student__in=students, status__in=CardStatus.LIVE_STATUSES,
            )
        }
        profiles = {
            sp.student_id: sp for sp in
            StudentProfile.objects.filter(student__in=students)
            .select_related("program__department")
        }
        for student in students:
            profile = profiles.get(student.pk)
            program = profile.program if profile else None
            department = program.department if program else None
            card = card_map.get(student.pk)
            writer.writerow([
                profile.student_number if profile else "",
                student.get_full_name(),
                student.email,
                program.name if program else "",
                department.name if department else "",
                photo_map.get(student.pk, "missing"),
                card.get_status_display() if card else "none",
                card.card_number if card else "",
            ])
        return response

    response["Content-Disposition"] = 'attachment; filename="id-cards.csv"'
    writer.writerow([
        "Card Number", "Student ID", "Student Name", "Status", "Programme",
        "Department", "Issue Date", "Expiry Date", "Batch", "Replacement Reason",
    ])
    cards = IDCard.objects.filter(institution=institution).select_related(
        "student", "batch", "student__academic_profile__program__department",
    ).order_by("card_number")
    profiles = {
        sp.student_id: sp for sp in
        StudentProfile.objects.filter(
            student__in=cards.values_list("student_id", flat=True),
        ).select_related("program__department")
    }
    for card in cards:
        profile = profiles.get(card.student_id)
        program = profile.program if profile else None
        department = program.department if program else None
        writer.writerow([
            card.card_number,
            profile.student_number if profile else "",
            card.student.get_full_name(),
            card.get_status_display(),
            program.name if program else "",
            department.name if department else "",
            card.issue_date or "",
            card.expiry_date or "",
            card.batch.batch_number if card.batch else "",
            card.replacement_reason or "",
        ])
    return response


# ─────────────────────────────────────────────────────────────────────────────
# STAFF: AUDIT LOG
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
def audit_log(request):
    action = (request.GET.get("action") or "").strip()
    logs = IDCardAuditLog.objects.select_related(
        "actor", "card", "batch", "template", "photo",
    )
    if action:
        logs = logs.filter(action=action)
    context = {
        "logs": logs[:300],
        "action": action,
        "action_choices": AuditAction.choices,
        "id_card_pending_photos": _staff_badge(request),
    }
    return render(request, "id_cards/audit_log.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# STUDENT: MY CARD / PHOTO / REPLACEMENT
# ─────────────────────────────────────────────────────────────────────────────

@student_required
def my_card(request):
    student = request.user
    photo = PassportPhoto.objects.filter(student=student).first()
    live_cards = IDCard.objects.filter(student=student).select_related(
        "template", "photo", "batch",
    ).order_by("-created_at")
    card = next((c for c in live_cards if c.is_live), None)
    history = [c for c in live_cards if not c.is_live][:10]

    institution = student_institution(student)
    settings_obj = IDCardSettings.get_for(institution) if institution else None
    replacements = ReplacementRequest.objects.filter(
        student=student,
    ).select_related("card", "replacement_card").order_by("-requested_at")[:10]

    photo_form = PhotoUploadForm()
    replacement_form = ReplacementRequestForm()
    photo_locked = bool(photo and photo.status == PhotoStatus.APPROVED)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "upload_photo":
            if photo_locked:
                messages.error(
                    request,
                    "Your passport photo is approved and cannot be replaced here. "
                    "File a replacement request to use a new photograph.",
                )
            else:
                photo_form = PhotoUploadForm(request.POST, request.FILES)
                if photo_form.is_valid():
                    try:
                        upload_photo(student, photo_form.cleaned_data["photo"],
                                     actor=student)
                    except Exception as exc:  # noqa: BLE001 — validator errors
                        messages.error(request, str(exc))
                    else:
                        approval = (settings_obj.require_photo_approval
                                    if settings_obj else True)
                        if approval:
                            messages.success(
                                request,
                                "Photo uploaded — it will be reviewed shortly.",
                            )
                        else:
                            messages.success(request, "Photo uploaded and approved.")
                        photo = PassportPhoto.objects.filter(student=student).first()
                else:
                    messages.error(
                        request,
                        "Upload a clear JPG or PNG passport photograph (max 4 MB).",
                    )

        elif action == "request_replacement":
            if not card:
                messages.error(request, "You have no live card to replace.")
            else:
                form = ReplacementRequestForm(request.POST)
                if form.is_valid():
                    try:
                        create_replacement_request(
                            student, card,
                            form.cleaned_data["reason"],
                            details=form.cleaned_data.get("details", ""),
                            actor=student,
                        )
                    except CardGenerationError as exc:
                        messages.error(request, exc.message)
                    else:
                        messages.success(
                            request,
                            "Replacement request submitted — you will be "
                            "notified once it is reviewed.",
                        )
                        return redirect("id_cards:my_card")
                else:
                    messages.error(request, "Choose a reason for the request.")

    live_cards = IDCard.objects.filter(student=student).select_related(
        "template", "photo", "batch",
    ).order_by("-created_at")
    card = next((c for c in live_cards if c.is_live), None)

    context = {
        "photo": photo,
        "card": card,
        "history": history,
        "replacements": replacements,
        "photo_form": photo_form,
        "replacement_form": replacement_form,
        "photo_locked": photo_locked,
        "settings_obj": settings_obj,
        "page_title": "My ID Card",
        "can_replace": bool(card) and (
            settings_obj is None or settings_obj.allow_student_replacement_requests
        ) and card.status == CardStatus.ACTIVE,
    }
    return render(request, "id_cards/my_card.html", context)


@student_required
@require_GET
def digital_card(request, side="combined"):
    """Downloadable digital ID (JPEG) for the student's live card.

    Default side is "combined": front and back side-by-side in one image so
    bulk downloads don't get mixed up.
    """
    if side not in {"front", "back", "combined"}:
        raise Http404
    student = request.user
    card = IDCard.objects.filter(
        student=student,
    ).select_related("template", "photo").order_by("-created_at").first()
    if card is None:
        messages.error(request, "No ID card has been generated for you yet.")
        return redirect("id_cards:my_card")
    try:
        if side == "combined":
            payload = renderers.combined_card_bytes(card, request)
        else:
            payload = renderers.digital_card_bytes(card, side, request)
    except Exception:  # noqa: BLE001
        logger.exception("Digital card render failed for %s", card.card_number)
        messages.error(request, "Could not render your ID card right now.")
        return redirect("id_cards:my_card")

    filename = (f"{card.card_number}-front-back" if side == "combined"
                else f"{card.card_number}-{side}")
    response = HttpResponse(payload, content_type="image/jpeg")
    response["Content-Disposition"] = f'inline; filename="{filename}.jpg"'
    return response


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC: QR VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────

def verify_card(request, token):
    """Public verification page rendered from the QR code."""
    # Honour the institution's "verification requires login" policy.
    result = verify_token(token)
    if result.found and result.card:
        settings_obj = IDCardSettings.get_for(result.card.institution)
        if settings_obj.verification_requires_login and not request.user.is_authenticated:
            return redirect(
                f"/accounts/login/?next={reverse('id_cards:verify_card', args=[token])}"
            )
        if not result.valid:
            log(None, AuditAction.CARD_VERIFIED, card=result.card,
                description=f"Public verification: {result.reason}.")
    else:
        log(None, AuditAction.CARD_VERIFIED,
            description=f"Public verification failed for token {token[:8]}…")

    context = {
        "result": result,
        "token": token,
        "page_title": "ID Card Verification",
    }
    return render(request, "id_cards/verify.html", context)