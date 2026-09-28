"""
Shared factories for the Applications & Admissions engine tests.

These build the smallest object graph the engine actually needs: an
``Institution``, a faculty -> department -> programme chain, an academic
session, an open cycle and an applicant. Nothing here depends on legacy
admissions rows, so every test starts from an explicit, described state.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from unittest import SkipTest

from accounts.models import StaffResponsibility
from academics.models import AcademicSession, Department, Faculty, Institution, Program
from portal.models import AdmissionCycle, ApplicationType, CycleStatus
from portal.services import CycleService

User = get_user_model()


def schema_available():
    """
    Whether the admissions engine's tables exist in this database.

    The engine was built without migrations, so the ordinary test runner builds
    its schema from the existing migration state and the ``portal_*`` tables are
    simply absent. Tests that need them should skip with a pointer to the
    no-migrations settings rather than fail with a confusing
    ``no such table`` error::

        python manage.py test portal.admissions_tests \\
            --settings=eduPro.settings.test_nomigrations
    """
    from django.db import connection

    tables = set(connection.introspection.table_names())
    return {
        "portal_admissionapplication",
        "portal_applicationtype",
        "portal_admissionoffer",
    }.issubset(tables)


def make_institution(name="EduPro University"):
    return Institution.objects.create(name=name)


class AdmissionsSchemaTestCase(TestCase):
    """
    Base case for the admissions scenario tests.

    Skips (with an actionable message) when the database was built from
    migrations and therefore has no ``portal_*`` admissions tables.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not schema_available():
            raise SkipTest(
                "Admissions engine tables are not present in this database "
                "(the engine ships no migrations). Run with "
                "--settings=eduPro.settings.test_nomigrations to build the "
                "schema from the models."
            )


def make_session(name="2025/2026", current=True, start="2025-09-01", end="2026-08-31"):
    return AcademicSession.objects.create(
        name=name, start_date=start, end_date=end, is_current=current,
    )


def make_programme(institution=None, session=None, code="ENG",
                   program_type="undergraduate", level=None):
    """
    A faculty -> department -> programme chain.

    ``Program`` has no session FK — sessions are reached through the cycle and
    through ``StudentProfile.current_session`` — so the session is accepted here
    only to keep call sites readable.
    """
    institution = institution or make_institution()
    session = session or AcademicSession.objects.filter(is_current=True).first() \
        or make_session()
    # Faculty/Department are unique on (institution, code), so derive the codes
    # from the programme code to let a test create several programmes.
    faculty = Faculty.objects.create(
        institution=institution, name=f"Faculty of {code}", code=f"F-{code}",
        description="Science", is_active=True,
    )
    department = Department.objects.create(
        institution=institution, faculty=faculty, name=f"Department of {code}",
        code=f"D-{code}", description="Computing", is_active=True,
    )
    defaults = {
        "code": code,
        "program_type": program_type,
        "duration_years": 4 if program_type == "undergraduate" else 2,
        "total_credits": 120 if program_type == "undergraduate" else 60,
        "description": f"{code} programme",
        "is_active": True,
    }
    if level is not None:
        defaults["level"] = level
    program = Program.objects.create(
        department=department, name=f"Programme {code}", **defaults
    )
    return program


def make_cycle(session=None, fee="500.00", active=True, status=None,
               payment_required=True, name=None, **extra):
    session = session or AcademicSession.objects.filter(is_current=True).first() \
        or make_session()
    if status is None:
        status = CycleStatus.OPEN if active else CycleStatus.CLOSED
    # ``name`` is unique; default to a fresh one so a test can open a second
    # cycle for the same session.
    if name is None:
        base = f"{session.name} Admissions"
        existing = AdmissionCycle.objects.filter(
            name__startswith=base).count()
        name = base if existing == 0 else f"{base} ({existing + 1})"
    return AdmissionCycle.objects.create(
        name=name,
        academic_year=session.name,
        academic_session=session,
        start_date=session.start_date,
        end_date=session.end_date,
        application_fee=fee,
        payment_required_to_progress=payment_required,
        is_active=active,
        status=status,
        **extra,
    )


def make_application(cycle=None, program=None, user=None, type_code="undergraduate",
                     status="draft", **extra):
    """Create an ``AdmissionApplication`` wired to a real user and cycle."""
    from portal.models import AdmissionApplication, ApplicationTypeCode

    cycle = cycle or make_cycle()
    program = program or make_programme()
    user = user or make_applicant()
    atype = ApplicationType.for_code(type_code) or seed_application_types()
    defaults = {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "phone": "+233200000000",
    }
    defaults.update(extra)
    return AdmissionApplication.objects.create(
        cycle=cycle, program_applied=program, user=user,
        application_type=type_code, application_type_ref=atype,
        status=status, **defaults
    )


def make_applicant(email="applicant@example.com", first="Ada", last="Lovelace",
                   password="s3cret-pass", role="student", **extra):
    # EduProUser.is_active defaults to False, so accounts must be activated
    # explicitly or the custom auth backend refuses them.
    extra.setdefault("is_active", True)
    return User.objects.create_user(
        email=email, password=password, first_name=first, last_name=last,
        role=role, **extra,
    )


def make_staff(email="officer@example.com", role="teacher", **extra):
    """
    Create a staff account.

    Note that ``EduProUser.role`` only has three values (admin/teacher/student);
    academic authority such as "can decide admissions" comes from a
    :class:`UserStaffRole` row, not from ``role``. Use
    :func:`grant_responsibility` to confer it.
    """
    extra.setdefault("is_active", True)
    return User.objects.create_user(
        email=email, password="s3cret-pass", first_name="Staff", last_name="User",
        role=role, **extra,
    )


def grant_responsibility(user, responsibility, department=None, granted_by=None):
    """Give ``user`` a :class:`UserStaffRole` (e.g. ADMISSIONS_OFFICER, HOD)."""
    from accounts.models import UserStaffRole

    return UserStaffRole.objects.create(
        user=user, responsibility=responsibility, department=department,
        granted_by=granted_by, is_active=True,
    )


def make_admissions_officer(email="officer@example.com", department=None):
    user = make_staff(email, role="teacher")
    grant_responsibility(user, StaffResponsibility.ADMISSIONS_OFFICER, department)
    return user


def make_admin(email="admin@example.com"):
    return make_staff(email, role="admin")


def make_hod(email="hod@example.com", department=None):
    user = make_staff(email, role="teacher")
    grant_responsibility(user, StaffResponsibility.HOD, department)
    return user


def upload_file(name="transcript.pdf", content=b"%PDF-1.4 test bytes",
                content_type="application/pdf"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(name, content, content_type=content_type)


def seed_application_types():
    """Idempotently create the configurable application types + requirements."""
    CycleService.bootstrap()
    return ApplicationType.objects.all()
