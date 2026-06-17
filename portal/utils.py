"""Portal-specific PDF generation — application & admission letters."""

from django.template.loader import render_to_string

from core.utils import generate_pdf_from_html


def render_application_letter(application):
    """Generate PDF of the applicant's submitted application details."""
    html = render_to_string("portal/application_letter_pdf.html", {
        "application": application,
    })
    return generate_pdf_from_html(
        html,
        f"application_{application.reference_number}.pdf",
    )


def render_admission_letter(application):
    """Generate a formal admission/acceptance letter PDF."""
    html = render_to_string("portal/admission_letter_pdf.html", {
        "application": application,
    })
    return generate_pdf_from_html(
        html,
        f"admission_{application.reference_number}.pdf",
    )
