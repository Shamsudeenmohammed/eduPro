"""Shared utilities — PDF generation, GPA, imports."""

import io
from decimal import Decimal

from django.http import HttpResponse
from django.template.loader import render_to_string

from academics.models import Enrolment, StudentProfile
from teachers.models import GRADE_POINTS, StudentResult


def calculate_semester_gpa(student, semester):
    """GPA for one semester: sum(grade_point * credits) / sum(credits)."""
    results = StudentResult.objects.filter(
        enrolment__student=student,
        enrolment__offering__semester=semester,
        result_sheet__status="approved",
        grade_point__isnull=False,
    ).select_related("enrolment__offering__course")

    total_points = Decimal("0")
    total_credits = 0
    for r in results:
        credits = r.enrolment.offering.course.credit_units
        total_points += Decimal(str(r.grade_point)) * credits
        total_credits += credits

    if total_credits == 0:
        return None
    return round(total_points / total_credits, 2)


def calculate_cgpa(student):
    """Cumulative GPA across all completed enrolments."""
    results = StudentResult.objects.filter(
        enrolment__student=student,
        result_sheet__status="approved",
        grade_point__isnull=False,
    ).select_related("enrolment__offering__course")

    total_points = Decimal("0")
    total_credits = 0
    for r in results:
        credits = r.enrolment.offering.course.credit_units
        total_points += Decimal(str(r.grade_point)) * credits
        total_credits += credits

    if total_credits == 0:
        return None
    return round(total_points / total_credits, 2)


def update_student_gpa(student):
    """Persist CGPA on StudentProfile."""
    cgpa = calculate_cgpa(student)
    if cgpa is None:
        return
    try:
        profile = student.academic_profile
        profile.cumulative_gpa = cgpa
        profile.save(update_fields=["cumulative_gpa", "updated_at"])
    except StudentProfile.DoesNotExist:
        pass


def generate_pdf_from_html(html_content, filename="document.pdf"):
    """Generate PDF using reportlab fallback or HTML response."""
    try:
        from xhtml2pdf import pisa
        buffer = io.BytesIO()
        pisa.CreatePDF(html_content, dest=buffer)
        buffer.seek(0)
        response = HttpResponse(buffer.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except ImportError:
        pass

    # Fallback: simple text-based PDF via reportlab
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        c.setFont("Helvetica", 10)
        y = 800
        for line in html_content.replace("<br>", "\n").split("\n")[:60]:
            clean = line.replace("<", " ").replace(">", " ").strip()[:90]
            if clean:
                c.drawString(50, y, clean)
                y -= 14
                if y < 50:
                    c.showPage()
                    y = 800
        c.save()
        buffer.seek(0)
        response = HttpResponse(buffer.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except ImportError:
        response = HttpResponse(html_content, content_type="text/html")
        response["Content-Disposition"] = f'inline; filename="{filename.replace(".pdf", ".html")}"'
        return response


def render_transcript_pdf(student):
    """Build academic transcript PDF for a student."""
    profile = getattr(student, "academic_profile", None)
    results = StudentResult.objects.filter(
        enrolment__student=student,
        result_sheet__status="approved",
    ).select_related(
        "result_sheet__offering__course",
        "result_sheet__offering__semester__session",
        "enrolment__offering__level",
    ).order_by(
        "result_sheet__offering__semester__session__start_date",
        "result_sheet__offering__course__code",
    )

    cgpa = calculate_cgpa(student)
    html = render_to_string("core/transcript_pdf.html", {
        "student": student,
        "profile": profile,
        "results": results,
        "cgpa": cgpa,
    })
    return generate_pdf_from_html(html, f"transcript_{student.pk}.pdf")


def render_id_card_pdf(user):
    """Generate student/teacher ID card PDF."""
    html = render_to_string("core/id_card_pdf.html", {"user": user})
    return generate_pdf_from_html(html, f"id_card_{user.pk}.pdf")
