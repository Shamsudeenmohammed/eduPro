"""Portal-specific PDF generation — application & admission letters."""

import io
from datetime import date

from django.http import HttpResponse

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable,
)
from reportlab.lib.colors import HexColor


# ── Shared colours ─────────────────────────────────────────────────────────
ACCENT    = HexColor("#1e1b4b")
MUTED     = HexColor("#64748b")
DARK      = HexColor("#0f172a")
HR_COLOUR = HexColor("#cbd5e1")


def _styles():
    return {
        "title": ParagraphStyle(
            "Title", fontName="Helvetica-Bold", fontSize=20,
            textColor=ACCENT, alignment=TA_CENTER, spaceAfter=2*mm,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", fontName="Helvetica", fontSize=9,
            textColor=MUTED, alignment=TA_CENTER, spaceAfter=6*mm,
        ),
        "ref": ParagraphStyle(
            "Ref", fontName="Helvetica", fontSize=8,
            textColor=MUTED, alignment=TA_LEFT,
        ),
        "date_right": ParagraphStyle(
            "DateRight", fontName="Helvetica", fontSize=10,
            alignment=TA_RIGHT, spaceAfter=4*mm,
        ),
        "salutation": ParagraphStyle(
            "Salutation", fontName="Helvetica", fontSize=11,
            textColor=DARK, spaceAfter=2*mm,
        ),
        "body": ParagraphStyle(
            "Body", fontName="Helvetica", fontSize=10.5,
            leading=16, alignment=TA_JUSTIFY,
            textColor=DARK, spaceAfter=3*mm,
        ),
        "body_bold": ParagraphStyle(
            "BodyBold", fontName="Helvetica-Bold", fontSize=10.5,
            leading=16, alignment=TA_JUSTIFY,
            textColor=DARK, spaceAfter=3*mm,
        ),
        "info_label": ParagraphStyle(
            "InfoLabel", fontName="Helvetica-Bold", fontSize=9.5,
            textColor=MUTED, spaceAfter=1*mm,
        ),
        "info_value": ParagraphStyle(
            "InfoValue", fontName="Helvetica", fontSize=11,
            textColor=DARK, spaceAfter=5*mm,
        ),
        "footer": ParagraphStyle(
            "Footer", fontName="Helvetica", fontSize=8,
            textColor=MUTED, alignment=TA_CENTER,
        ),
        "signature": ParagraphStyle(
            "Signature", fontName="Helvetica", fontSize=10.5,
            textColor=DARK, spaceBefore=6*mm,
        ),
        "signature_name": ParagraphStyle(
            "SignatureName", fontName="Helvetica-Bold", fontSize=11,
            textColor=DARK,
        ),
    }


def _build_document(title, story):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=20*mm, bottomMargin=20*mm,
        leftMargin=25*mm, rightMargin=25*mm,
    )
    doc.build(story)
    buf.seek(0)
    return buf


def _make_response(buf, filename):
    response = HttpResponse(buf.read(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ── Shared header ──────────────────────────────────────────────────────────

def _letter_head(story, s, ref, institution_name="EduPro University"):
    story.append(Paragraph(institution_name, s["title"]))
    story.append(Paragraph("Office of Admissions", s["subtitle"]))
    story.append(HRFlowable(
        width="100%", color=HR_COLOUR, thickness=0.5,
        spaceAfter=4*mm, spaceBefore=0,
    ))
    story.append(Paragraph(f"Ref: {ref}", s["ref"]))
    story.append(Paragraph(f"Date: {date.today().strftime('%B %d, %Y')}", s["date_right"]))


# ── Application Letter ─────────────────────────────────────────────────────

def render_application_letter(application):
    """Generate PDF of the applicant's submitted application details."""
    s = _styles()
    story = []
    _letter_head(story, s, application.reference_number)

    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        f"APPLICATION RECORD<br/>"
        f"{application.program_applied.name if application.program_applied else ''}",
        ParagraphStyle("Subj", parent=s["body_bold"], fontSize=12, alignment=TA_CENTER),
    ))
    story.append(Spacer(1, 3*mm))

    # Applicant details table
    data = [
        ["Full Name", f"{application.first_name} {application.last_name}"],
        ["Date of Birth", application.date_of_birth.strftime("%B %d, %Y") if application.date_of_birth else "—"],
        ["Gender", application.get_gender_display() if application.gender else "—"],
        ["Nationality", application.nationality or "—"],
        ["Email", application.email],
        ["Phone", application.phone or "—"],
        ["Program Applied", application.program_applied.name if application.program_applied else "—"],
        ["Application Type", application.get_application_type_display()],
        ["Qualification", application.qualification or "—"],
        ["Previous School", application.previous_school or "—"],
        ["Aggregate Score", str(application.aggregate_score) if application.aggregate_score else "—"],
    ]
    t = Table(data, colWidths=[45*mm, 100*mm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR", (1, 0), (1, -1), DARK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t)

    story.append(Spacer(1, 8*mm))
    story.append(Paragraph(
        "This is a record of your application submission. "
        "Please keep this letter for your reference. "
        "You will be notified once a decision has been made on your application.",
        s["body"],
    ))

    story.append(Spacer(1, 5*mm))
    story.append(HRFlowable(width="40%", color=HR_COLOUR, thickness=0.5, spaceAfter=3*mm))
    story.append(Paragraph("Office of Admissions", s["signature_name"]))
    story.append(Paragraph("EduPro University", s["signature"]))

    buf = _build_document("Application Letter", story)
    return _make_response(buf, f"application_{application.reference_number}.pdf")


# ── Admission Letter ───────────────────────────────────────────────────────

def render_admission_letter(application, student_id=None):
    """Generate a formal admission/acceptance letter PDF."""
    institution_name = "EduPro University"
    s = _styles()
    story = []
    _letter_head(story, s, application.reference_number, institution_name)

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        "ADMISSION LETTER",
        ParagraphStyle("Subj", parent=s["body_bold"], fontSize=13, alignment=TA_CENTER),
    ))
    story.append(Spacer(1, 5*mm))

    # Salutation
    story.append(Paragraph(
        f"Dear {application.first_name} {application.last_name},",
        s["salutation"],
    ))

    # Body
    story.append(Paragraph(
        f"Congratulations! On behalf of the Admissions Committee at "
        f"<b>{institution_name}</b>, I am pleased to inform you that your "
        f"application for admission into the "
        f"<b>{application.program_applied.name if application.program_applied else ''}</b> "
        f"programme for the {application.cycle.name if application.cycle else ''} academic year "
        f"has been <b>approved</b>.",
        s["body"],
    ))

    story.append(Paragraph(
        "After a thorough review of your academic credentials and qualifications, "
        "the committee has determined that you meet the requirements for admission. "
        "We believe you will make a valuable contribution to our academic community.",
        s["body"],
    ))

    # Student credentials
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph("STUDENT CREDENTIALS", s["info_label"]))
    creds_data = [
        ["Student ID", student_id or "—"],
        ["Institutional Email", application.user.institutional_email if hasattr(application.user, 'institutional_email') else "—"],
        ["Default Password", "0123456789"],
        ["Admission Date", date.today().strftime("%B %d, %Y")],
        ["Programme", application.program_applied.name if application.program_applied else "—"],
        ["Department", application.program_applied.department.name if application.program_applied and application.program_applied.department else "—"],
    ]
    ct = Table(creds_data, colWidths=[45*mm, 100*mm])
    ct.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (0, -1), ACCENT),
        ("TEXTCOLOR", (1, 0), (1, -1), DARK),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOX", (0, 0), (-1, -1), 0.4, HR_COLOUR),
        ("INNERGRID", (0, 0), (-1, -1), 0.2, HR_COLOUR),
        ("BACKGROUND", (0, 0), (0, -1), HexColor("#f8fafc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(ct)

    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        "<b>To access the student portal:</b>",
        s["body_bold"],
    ))
    story.append(Paragraph(
        f"1. Visit the student portal at /accounts/login/.<br/>"
        f"2. Sign in using your <b>Student ID</b> and the default password <b>0123456789</b>.<br/>"
        f"3. You will be prompted to change your password after your first login.",
        s["body"],
    ))

    story.append(Paragraph(
        "Please note that your application reference number and email are for tracking "
        "purposes only. Always use your Student ID to access the student dashboard.",
        s["body"],
    ))

    # Closing
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        "We look forward to welcoming you to campus. Should you have any questions "
        "regarding your admission, registration, or other matters, please do not "
        "hesitate to contact the Admissions Office.",
        s["body"],
    ))
    story.append(Paragraph("Once again, congratulations and welcome aboard!", s["body"]))

    story.append(Spacer(1, 5*mm))
    story.append(HRFlowable(width="40%", color=HR_COLOUR, thickness=0.5, spaceAfter=3*mm))
    story.append(Paragraph("Office of Admissions", s["signature_name"]))
    story.append(Paragraph(f"{institution_name}", s["signature"]))

    buf = _build_document("Admission Letter", story)
    return _make_response(buf, f"admission_{application.reference_number}.pdf")
